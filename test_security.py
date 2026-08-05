"""Self-check for the auth gate.  Run:  python test_security.py

Covers the three things that decide who gets into a class: the roleless gate,
the teacher-code comparison, and the throttle standing in front of it. Firestore
is never touched — the collaborators that would reach it are stubbed, so this
runs offline and in a second.
"""
import app as A

c = A.app.test_client()

# Two throwaway routes so the decorator is tested directly rather than through a
# real endpoint's Firestore work.
@A.app.route("/_t/gated")
@A.require_auth
def _t_gated():
    return A.jsonify({"uid": A.request.uid})


@A.app.route("/_t/roleless_ok")
@A.require_auth(allow_roleless=True)
def _t_roleless_ok():
    return A.jsonify({"uid": A.request.uid})


def signed_in_as(uid="u1", role=None):
    A.verify_user = lambda: {"uid": uid, "email": "s@example.test"}
    A.get_role = lambda _uid: role


def test_gate():
    # A valid token with no role behind it is a half-created signup, not a student.
    signed_in_as(role=None)
    assert c.get("/_t/gated").status_code == 403, "roleless account got through the gate"
    assert c.get("/_t/roleless_ok").status_code == 200, "register path must run pre-role"

    signed_in_as(role="student")
    assert c.get("/_t/gated").status_code == 200, "registered student was locked out"

    A.verify_user = lambda: None
    assert c.get("/_t/gated").status_code == 401, "no token should be 401"


def test_teacher_code():
    signed_in_as(role=None)
    A.discard_unregistered_user = lambda uid: None   # would otherwise call Firebase
    A.TEACHER_SIGNUP_CODE = "correct-horse-battery"
    A.REGISTER_RATE_LIMIT = 3
    ip = {"REMOTE_ADDR": "10.0.0.9"}                 # own bucket, independent of other tests

    def attempt(code):
        return c.post("/auth/register", json={"role": "teacher", "teacher_code": code},
                      environ_base=ip).status_code

    # Non-ASCII is attacker-reachable input: as str, compare_digest raises
    # TypeError and the wrong-code 403 becomes a 500.
    assert attempt("café") == 403, "non-ASCII code should be rejected, not crash"
    assert attempt("wrong") == 403
    assert attempt("wrong") == 403
    # Budget spent: the code is the keys to every class, so guessing stops here.
    assert attempt("correct-horse-battery") == 429, "teacher code is brute-forceable"


def test_docx_expansion_cap():
    """A .docx is a zip, so 10 MB on the wire can be gigabytes in memory."""
    import io
    import zipfile

    signed_in_as(role="teacher")
    A.class_owned_by = lambda cid, uid: True

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * (4 << 20))   # 4 MB -> a few KB on disk
    bomb = buf.getvalue()
    assert len(bomb) < 100_000, "payload should be tiny compressed, else the test proves nothing"

    def upload(cap):
        A.MAX_EXTRACT_BYTES = cap
        return c.post("/upload", data={
            "class_id": "c1",
            "file": (io.BytesIO(bomb), "bomb.docx"),
        }, content_type="multipart/form-data").status_code

    assert upload(1 << 20) == 413, "4 MB of expansion slipped past a 1 MB cap"
    # Control: with the cap raised, the same upload gets *past* the guard and
    # fails later as a malformed document — proving the 413 above came from the
    # expansion check and not from something else rejecting the request.
    assert upload(1 << 30) == 400, "expected the guard to be what rejected the bomb"


if __name__ == "__main__":
    test_gate()
    test_teacher_code()
    test_docx_expansion_cap()
    print("ok — auth gate, teacher code, register throttle, and docx expansion cap all hold")
