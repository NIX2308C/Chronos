"""Self-check for per-class recollection and student uploads.
Run:  python test_student_context.py

Both of these end up inside the tutor's system prompt, which is the one place a
mistake is invisible: a wrong block doesn't crash, it just quietly changes what
the model is told. The two things worth pinning down are that memory never
crosses a class boundary, and that an uploaded assignment stays demoted to
"the student's own work" instead of becoming course material. Pure functions —
Firestore is never touched.
"""
# The app lives one directory up, so make it importable when this is run
# from anywhere (tests/, the repo root, or a runner's checkout).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A

# ---------- recollection ----------

CHATS = [
    {"id": "c1", "opening": "what is osmosis", "learning_gaps": ["what is osmosis"], "last_active": 10},
    {"id": "c2", "opening": "explain diffusion", "learning_gaps": [], "last_active": 30},
    {"id": "c3", "opening": "explain diffusion", "learning_gaps": [], "last_active": 20},  # duplicate topic
    {"id": "now", "opening": "the question being asked right now", "last_active": 99},
]

m = A.build_memory_block(CHATS, exclude_chat_id="now")
# The current conversation is already replayed as chat history; repeating its
# summary here would tell the model the student "previously asked" what they are
# asking at this moment.
assert "right now" not in m, m
# Newest first, and one line per distinct question however often it was asked.
assert m.index("explain diffusion") < m.index("what is osmosis"), m
assert m.count("explain diffusion") == 1, m
# Gaps are called out separately — that's the bit worth pitching an answer at.
assert "uncertainty or confusion" in m and "what is osmosis" in m, m
assert "Earlier conversations in this course: 3" in m, m

# No chats, or only the current one, means the tutor remembers nothing and says
# nothing — an empty block is dropped from the prompt entirely.
assert A.build_memory_block([]) == ""
assert A.build_memory_block([CHATS[-1]], exclude_chat_id="now") == ""
# A chat that predates summaries still has a title, cut from its opening question.
assert "old thing" in A.build_memory_block([{"id": "x", "title": "old thing", "last_active": 1}])
# Nothing to remember is not the same as a chat with no usable summary.
assert A.build_memory_block([{"id": "x", "last_active": 1}]) == ""

# Recall is bounded, or a heavy user's whole history lands in every prompt.
many = [{"id": "c%d" % i, "opening": "q%d" % i, "last_active": i} for i in range(50)]
assert A.build_memory_block(many).count(";") == A.MEMORY_CHATS - 1

# ---------- uploaded work ----------

DOCS = [
    {"kind": "assignment", "name": "essay.docx", "text": "my essay"},
    {"kind": "rubric", "name": "grid.pdf", "text": "marking criteria"},
]
b = A.build_docs_block(DOCS)
# Criteria before the work they're applied to.
assert b.index("grid.pdf") < b.index("essay.docx"), b
# The label is load-bearing: this is the only thing telling the model these pages
# are the student's, not the teacher's.
assert "NOT teacher material" in b, b
assert "my essay" in b and "marking criteria" in b, b

assert A.build_docs_block([]) == ""
assert A.build_docs_block([{"kind": "assignment", "name": "empty.txt", "text": "   "}]) == ""

# The whole block is capped, not just each file: three documents at the per-file
# limit would otherwise ride along on every single message in the conversation.
big = [{"kind": "assignment", "name": "n%d" % i, "text": "x" * A.STUDENT_DOC_CHARS}
       for i in range(A.STUDENT_DOCS_MAX + 2)]
assert A.build_docs_block(big).count("x") <= A.STUDENT_CONTEXT_CHARS

# ---------- the assembled prompt ----------

plain = A.build_system_instruction("teacher stuff")
assert "teacher stuff" in plain
# The model shouldn't be told to weigh a rubric nobody uploaded, or to recall a
# conversation that never happened.
assert "uploaded work" not in plain and "recollection notes" not in plain, plain
assert "What you remember" not in plain and "NOT teacher material" not in plain, plain

full = A.build_system_instruction("teacher stuff", m, b)
assert "recollection notes" in full and "uploaded work" in full, full
# Rules stay contiguously numbered however many blocks are present.
for i in range(1, 7):
    assert "\n%d. " % i in full or full.startswith("%d. " % i) or ":\n%d. " % i in full, i
# Teacher material comes first: everything after it is framed as context.
assert full.index("Teacher material:") < full.index("What you remember"), full
assert full.index("What you remember") < full.index("NOT teacher material"), full

# Retrieval found nothing, but the student attached work. The tutor may review it
# and must not fill the subject gap from its own knowledge — this is the branch
# that makes "mark my essay" answerable without reopening the grounding hole.
gapful = A.build_system_instruction("", "", b)
assert "do not supply subject facts" in gapful, gapful
assert "nothing in this course matched" in gapful, gapful
# ...and that licence only exists when there is work to review.
assert "do not supply subject facts" not in A.build_system_instruction("", m, "")

print("ok - memory stays in its class, uploads stay demoted, prompt assembles in order")

# ---------- the profile, and the class wall around it ----------

# This is the requirement in one block: what the tutor knows about a student in
# one class must be unreachable from another. The wall is the storage shape —
# Users/{uid}/Profiles/{class_id}, where the class id is the *document id* — so
# what's pinned here is that the code actually reads it that way, and that the
# tripwire fires if a document ever turns up under the wrong id anyway.

import student_profile as SP

_reads = []


class _Snap:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


class _Doc:
    def __init__(self, path, store):
        self.path, self.store = path, store

    def get(self):
        _reads.append(self.path)
        return _Snap(self.store.get(self.path))


class _Profiles:
    """Stands in for Users/{uid}/Profiles, recording the exact path read."""
    def __init__(self, uid, store):
        self.uid, self.store = uid, store

    def document(self, class_id):
        return _Doc("%s/%s" % (self.uid, class_id), self.store)


BIO = {"msgs": 9, "words": 40, "sents": 9, "chars": 200, "word_chars": 150,
       "questions": 8, "lower_starts": 9, "txtspeak": 7, "long_words": 0,
       "class_id": "class_bio", "gap_counts": {"what is osmosis": 4}}
HIST = {"msgs": 9, "words": 400, "sents": 14, "chars": 2400, "word_chars": 2000,
        "questions": 6, "lower_starts": 0, "txtspeak": 0, "long_words": 60,
        "class_id": "class_hist", "gap_counts": {"causes of the war": 5}}
STORE = {"stu/class_bio": BIO, "stu/class_hist": HIST}

_real_user_profile = A._user_profile
A._user_profile = lambda uid, class_id: _Profiles(uid, STORE).document(class_id)
try:
    bio = A.load_class_profile("stu", "class_bio")
    hist = A.load_class_profile("stu", "class_hist")
    # Exactly one document per call, named by the class asked for. Anything else
    # (a collection scan, a query with a filter) is how this leaks.
    assert _reads == ["stu/class_bio", "stu/class_hist"], _reads

    bio_note, hist_note = SP.describe(bio), SP.describe(hist)
    # The two students-in-two-classes read differently, and neither carries a
    # single word of the other's.
    assert "osmosis" in bio_note and "osmosis" not in hist_note, (bio_note, hist_note)
    assert "war" in hist_note and "war" not in bio_note, (bio_note, hist_note)
    assert SP.pitch(bio) == "plain" and SP.pitch(hist) == "stretch"

    # A class the student has never been in remembers nothing at all, rather than
    # falling back to whatever else is under their uid.
    assert A.load_class_profile("stu", "class_new") == {}
    assert SP.describe(A.load_class_profile("stu", "class_new")) == ""

    # The tripwire: a document that somehow carries another class's id is refused
    # outright. It should be unreachable — this is the one failure here that
    # would otherwise read as perfectly ordinary prose in the prompt.
    STORE["stu/class_mixed"] = dict(BIO, class_id="class_hist")
    assert A.load_class_profile("stu", "class_mixed") == {}

    # And the block that actually reaches the model: memory (from chats) and the
    # profile note, composed — with the profile still absent when there's no
    # class to scope it to.
    composed = A.build_memory_block(CHATS, exclude_chat_id="now") + "\n\n" + bio_note
    assert "osmosis" in composed and "how to pitch" in composed
    assert A.load_class_profile("stu", "") == {}
finally:
    A._user_profile = _real_user_profile

# The counters written for one message: increments, scoped, and never a gap that
# would blow the cap.
upd = A.profile_update("class_bio", "What is osmosis again?",
                       {"learning_gaps": ["older gap", "What is osmosis again?"]}, BIO)
assert upd["class_id"] == "class_bio", upd
# Only this exchange's gap is counted. Counting the whole accumulated list would
# re-count every earlier gap on every single message.
keys = [k for k in upd if k.startswith("gap_counts.")]
assert keys == ["gap_counts.what is osmosis again"], keys
# A message with no words leaves the profile completely untouched.
assert A.profile_update("class_bio", "   ", {}, BIO) == {}
# Once the cap is reached, a brand-new sticking point is dropped rather than
# growing the document without bound; an existing one still counts up.
full = dict(BIO, gap_counts={"g%d" % i: 1 for i in range(SP.PROFILE_MAX_GAPS)})
assert not [k for k in A.profile_update("class_bio", "a new thing",
                                        {"learning_gaps": ["a new thing"]}, full)
            if k.startswith("gap_counts.")]
assert [k for k in A.profile_update("class_bio", "g0", {"learning_gaps": ["g0"]}, full)
        if k.startswith("gap_counts.")] == ["gap_counts.g0"]

print("ok - a student's profile is sealed inside one class")
