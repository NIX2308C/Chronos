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


def test_join_throttle_is_not_only_per_uid():
    """A fresh Firebase account is free, so a per-uid budget alone throttles
    nothing: the attacker just signs up again. The IP budget is the one that
    costs them something."""
    A._rate_hits.clear()
    A.JOIN_RATE_LIMIT = 3
    A.JOIN_IP_RATE_LIMIT = 5
    ip = {"REMOTE_ADDR": "10.0.0.21"}

    def attempt(uid):
        signed_in_as(uid=uid, role="student")
        return c.post("/classes/join", json={"join_code": "ZZZZZZ"}, environ_base=ip).status_code

    # Spend one uid's whole budget, then rotate to a brand-new uid — the old
    # behaviour handed the attacker a full fresh budget every time.
    assert [attempt("burner1") for _ in range(4)][-1] == 429, "per-uid budget didn't apply"
    codes = [attempt(f"burner{i}") for i in range(2, 6)]
    assert 429 in codes, "rotating uids bypassed the join throttle entirely"


def test_sources_are_not_sent_to_students():
    """The class material must not leave the server for a student, whatever the
    UI chooses to render."""
    A._rate_hits.clear()
    captured = {}

    class _FakeMsgs:
        def add(self, doc):
            pass

    class _FakeChatDoc:
        id = "chat1"

        def collection(self, _name):
            return _FakeMsgs()

        def get(self):
            class _S:
                exists = False
                def to_dict(self):
                    return {}
            return _S()

        def set(self, *_a, **_k):
            pass

    class _FakeQuery:
        def limit(self, _n):
            return self

        def stream(self):
            return iter(())

    class _FakeChats:
        def document(self, _id=None):
            return _FakeChatDoc()

        # /chat also reads this student's other chats in the class (recollection)
        # and their uploaded work. Both are empty here — this test is about what
        # comes back out, not what went in.
        def where(self, *_a, **_k):
            return _FakeQuery()

    A.user_in_class = lambda uid, cid, role: True
    A._user_chats = lambda uid: _FakeChats()
    A._user_files = lambda uid: _FakeChats()
    A.load_history = lambda *_a, **_k: []
    A.embed = lambda _t: [0.0] * A.EMBED_DIM
    A.summarize_exchange = lambda *_a, **_k: {}
    A.pinecone_index = type("_PC", (), {
        "query": lambda self, **kw: {"matches": [
            {"metadata": {"text": "SECRET TEACHER MATERIAL"}, "score": 0.9}
        ]},
    })()
    A.client = type("_G", (), {
        "models": type("_M", (), {
            "generate_content": lambda self, **kw: type("_R", (), {"text": "an answer"})()
        })()
    })()

    def ask(role):
        signed_in_as(uid="u-" + role, role=role)
        r = c.post("/chat", json={"message": "hi", "class_id": "c1"})
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    student = ask("student")
    assert student["rules_used"] == [], "student was sent the class material"
    assert "SECRET TEACHER MATERIAL" not in A.json.dumps(student), "material leaked in the response"
    # ...but the student still learns whether anything backed the answer, which is
    # what the "knowledge gap" note in the UI hangs off now that rules are empty.
    assert student["grounded"] is True, "students lost the grounded signal"

    teacher = ask("teacher")
    assert teacher["rules_used"] == ["SECRET TEACHER MATERIAL"], "teacher lost their sources"


if __name__ == "__main__":
    test_gate()
    test_teacher_code()
    test_docx_expansion_cap()
    test_join_throttle_is_not_only_per_uid()
    test_sources_are_not_sent_to_students()
    print("ok — auth gate, teacher code, register + join throttles, docx expansion "
          "cap, and teacher-only sources all hold")
