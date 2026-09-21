"""Self-check for the auth gate.  Run:  python test_security.py

Covers the three things that decide who gets into a class: the roleless gate,
the teacher-code comparison, and the throttle standing in front of it. Firestore
is never touched — the collaborators that would reach it are stubbed, so this
runs offline and in a second.
"""
# The app lives one directory up, so make it importable when this is run
# from anywhere (tests/, the repo root, or a runner's checkout).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A

c = A.app.test_client()

# Grabbed before any test stubs it out. test_sources_are_not_sent_to_students
# replaces it with a no-op, and the profanity test needs the real one — it is
# what decides whether a flagged message becomes a "question" in the teacher's
# feed. load_history is grabbed for the same reason: the profanity test stubs
# both out for its own request, then checks the real ones directly.
_REAL_SUMMARIZE_EXCHANGE = A.summarize_exchange
_REAL_LOAD_HISTORY = A.load_history

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

    # An attempt that ISN'T throttled looks the code up in Firestore. Unstubbed
    # that is a real network call, and google-auth retries the failure long
    # enough to look like a hang rather than a failure — which is why this file
    # could never be run despite claiming to touch nothing.
    class _NoMatch:
        def collection(self, _n): return self
        def where(self, *_a, **_k): return self
        def limit(self, _n): return self
        def stream(self): return iter(())

    A.db = _NoMatch()

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
        def where(self, *_a, **_k):
            return self

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
    # Deliberately hostile settings: the old code had a per-course
    # "source_display" toggle that let a teacher flip the class material through
    # to students, and this test only ever passed because it used the defaults
    # with that toggle off. Material is teacher-only now regardless of what a
    # course document happens to have stored, so force the flag on.
    A.load_course_settings = lambda *_a, **_k: dict(A.course_settings(), source_display=True)
    # /chat reads these too. Left unstubbed they reach real Firestore, which is
    # exactly what this file claims never to do — it hangs instead of failing.
    A.load_custom_rules = lambda *_a, **_k: []
    A.load_course_manifest = lambda *_a, **_k: []
    A.load_user_preferences = lambda *_a, **_k: A.normalize_preferences(None)
    A.load_class_memory = lambda *_a, **_k: ""
    A.refresh_conversation_summary = lambda *_a, **_k: ("", {})
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
        # A real question, not "hi": greetings match _ACK_RE and short-circuit
        # retrieval entirely, so there would be no material to withhold and the
        # test would pass without proving anything.
        r = c.post("/chat", json={"message": "what is osmosis", "class_id": "c1"})
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    student = ask("student")
    assert student["rules_used"] == [], "student was sent the class material"
    assert student["sources"] == [], "student was sent the source excerpts"
    assert "SECRET TEACHER MATERIAL" not in A.json.dumps(student), "material leaked in the response"
    # ...but the student still learns whether anything backed the answer, which is
    # what the "knowledge gap" note in the UI hangs off now that rules are empty.
    assert student["grounded"] is True, "students lost the grounded signal"

    teacher = ask("teacher")
    assert teacher["rules_used"] == ["SECRET TEACHER MATERIAL"], "teacher lost their sources"
    assert teacher["sources"], "teacher lost the source excerpts"
    # Debug data is for dev accounts only, and asking for it is not enough.
    for who in (student, teacher):
        assert "debug" not in who, "non-dev response carried debug data"
    signed_in_as(uid="u-student2", role="student")
    r = c.post("/chat", json={"message": "what is osmosis", "class_id": "c1", "debug": True})
    assert "debug" not in r.get_json(), "a non-dev could switch debug on"

    # Both answers above were produced with the profile store deliberately left
    # unstubbed, so reading and writing it each raised. That is the point: the
    # profile is a nicety layered on top of tutoring, and a broken one must cost
    # a student nothing more than a plainer explanation. The 200s assert it.


def test_profanity_is_blocked_before_the_model():
    """A swear must not reach the model, and must not reach the teacher's
    question feeds. Both halves failed before: detection missed anything that
    wasn't a literal dictionary word, and a flagged message still became the
    conversation's `opening`, which /stats prints as a Recent question."""
    A._rate_hits.clear()
    written = {}

    class _FakeMsgs:
        def add(self, doc):
            written.setdefault("msgs", []).append(doc)

    class _FakeChatDoc:
        id = "chat1"
        def collection(self, _name): return _FakeMsgs()
        def get(self):
            class _S:
                exists = False
                def to_dict(self): return {}
            return _S()
        def set(self, doc, **_k): written["chat"] = doc

    class _FakeQuery:
        def where(self, *_a, **_k): return self
        def limit(self, _n): return self
        def stream(self): return iter(())

    class _FakeChats:
        def document(self, _id=None): return _FakeChatDoc()
        def where(self, *_a, **_k): return _FakeQuery()

    A.user_in_class = lambda uid, cid, role: True
    A._user_chats = lambda uid: _FakeChats()
    A._user_files = lambda uid: _FakeChats()
    A.load_history = lambda *_a, **_k: []
    A.load_course_settings = lambda *_a, **_k: A.course_settings()
    A.load_custom_rules = lambda *_a, **_k: []
    A.load_course_manifest = lambda *_a, **_k: []
    A.load_user_preferences = lambda *_a, **_k: A.normalize_preferences(None)
    A.load_class_memory = lambda *_a, **_k: ""
    A.refresh_conversation_summary = lambda *_a, **_k: ("", {})
    A.summarize_exchange = _REAL_SUMMARIZE_EXCHANGE   # it's half of what this tests

    # Abuse must not shape what the tutor thinks of this student either: a
    # blocked message reaching the profile would quietly teach it that they
    # "write very short messages" and pitch every future answer down.
    A.load_class_profile = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("the profile was read on a blocked message"))
    A._user_profile = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("the profile was written on a blocked message"))

    def _boom(*_a, **_k):
        raise AssertionError("the model was called on a blocked message")

    A.embed = _boom
    A.pinecone_index = type("_PC", (), {"query": _boom})()
    A.client = type("_G", (), {"models": type("_M", (), {"generate_content": _boom})()})()

    signed_in_as(uid="u-rude", role="student")
    # Censored, padded and leetspoken: every one of these was a miss under the
    # old exact-match word set, so each would have been answered normally.
    r = c.post("/chat", json={"message": "f*ck this sh1t", "class_id": "c1"})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["blocked"] is True, "a profane message was answered"
    assert "f*ck" not in A.json.dumps(body), "the message was echoed back"

    chat = written["chat"]
    # It reaches the teacher as a concern...
    assert chat.get("concerns"), "the teacher was never told"
    # ...and as nothing else. `opening` and `context` are what /stats turns into
    # Recent questions, Most repeated and the topic list; `material_gaps` is the
    # Knowledge Gaps panel, where this used to land as uncovered course material.
    assert "opening" not in chat and "context" not in chat, chat
    assert not chat.get("material_gaps"), "abuse was filed as a knowledge gap"
    assert chat.get("title") == A.BLOCKED_TITLE, "the swear became the chat's name"

    # Both halves of the exchange are stamped, which is what lets load_history
    # drop the pair. Without it, refusing to read the abuse once only delays it:
    # the student's next question replays the conversation, swear included.
    stored = {m["role"]: m for m in written["msgs"]}
    assert stored["student"]["blocked"] and stored["teacher"]["blocked"], stored

    class _Doc:
        def __init__(self, d): self._d = d
        def to_dict(self): return self._d

    class _Msgs:
        def order_by(self, *_a, **_k): return self
        def limit(self, _n): return self
        def stream(self):
            return iter([
                _Doc({"role": "teacher", "content": A.BLOCKED_REPLY, "blocked": True}),
                _Doc({"role": "student", "content": "f*ck this sh1t", "blocked": True}),
                _Doc({"role": "student", "content": "what is osmosis"}),
            ])

    replayed = A.json.dumps(_REAL_LOAD_HISTORY(_Msgs()))
    assert "osmosis" in replayed, "ordinary history stopped being replayed"
    assert "ck" not in replayed and A.BLOCKED_REPLY not in replayed, \
        "a blocked exchange came back to the model on the next turn"


def test_student_profile_needs_ownership_and_membership():
    """A student's profile says how they write and where they keep getting stuck.
    Two gates guard it, and ownership alone is not one of them: without the
    membership check a teacher could name any uid in the system and read a
    profile built in somebody else's classroom."""
    A._rate_hits.clear()
    signed_in_as(uid="t-owner", role="teacher")

    A.class_owned_by = lambda cid, uid: cid == "c-mine" and uid == "t-owner"
    A.user_in_class = lambda uid, cid, role: uid == "stu-mine" and cid == "c-mine"
    A.load_class_profile = lambda uid, cid: {
        "msgs": 9, "words": 40, "sents": 9, "chars": 200, "word_chars": 150,
        "questions": 8, "lower_starts": 9, "txtspeak": 7, "long_words": 0,
        "class_id": cid, "gap_counts": {"what is osmosis": 4},
    }

    def ask(class_id, student_uid):
        return c.post("/student-profile",
                      json={"class_id": class_id, "student_uid": student_uid})

    # Someone else's class, whoever the student is.
    assert ask("c-theirs", "stu-mine").status_code == 403, "read another teacher's class"
    # Own class, but a uid that was never in it — the interesting one.
    assert ask("c-mine", "stu-elsewhere").status_code == 403, \
        "ownership alone let a teacher read a stranger's profile"
    assert ask("c-mine", "../../etc").status_code == 403, "a path-shaped uid got through"

    r = ask("c-mine", "stu-mine")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert "osmosis" in body["summary"], body
    assert body["sticking_points"][0]["topic"] == "what is osmosis", body
    # Guidance, not a metric: nothing here should invite ranking one child
    # against another.
    for leak in ("score", "level", "rank", "word_chars", "percentile"):
        assert leak not in A.json.dumps(body), leak

    # A student cannot read anyone's profile, their own included.
    signed_in_as(uid="stu-mine", role="student")
    assert ask("c-mine", "stu-mine").status_code == 403, "a student read a profile"
    assert c.post("/roster", json={"class_id": "c-mine"}).status_code == 403, \
        "a student listed the class roster"


if __name__ == "__main__":
    test_gate()
    test_teacher_code()
    test_docx_expansion_cap()
    test_join_throttle_is_not_only_per_uid()
    test_sources_are_not_sent_to_students()
    test_profanity_is_blocked_before_the_model()
    test_student_profile_needs_ownership_and_membership()
    print("ok — auth gate, teacher code, register + join throttles, docx expansion "
          "cap, teacher-only sources, the profanity block, and per-student profile "
          "access all hold")
