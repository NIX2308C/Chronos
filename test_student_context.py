"""Self-check for per-class recollection and student uploads.
Run:  python test_student_context.py

Both of these end up inside the tutor's system prompt, which is the one place a
mistake is invisible: a wrong block doesn't crash, it just quietly changes what
the model is told. The two things worth pinning down are that memory never
crosses a class boundary, and that an uploaded assignment stays demoted to
"the student's own work" instead of becoming course material. Pure functions —
Firestore is never touched.
"""
import app as A

# ---------- recollection ----------

CHATS = [
    {"id": "c1", "opening": "what is osmosis", "gaps": ["what is osmosis"], "last_active": 10},
    {"id": "c2", "opening": "explain diffusion", "gaps": [], "last_active": 30},
    {"id": "c3", "opening": "explain diffusion", "gaps": [], "last_active": 20},  # duplicate topic
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
assert "may still be stuck" in m and "what is osmosis" in m, m
assert "Earlier conversations in this class: 3" in m, m

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
# Rules 5 and 6 are about material that isn't there; the model shouldn't be told
# to weigh a rubric nobody uploaded.
assert "uploaded work" not in plain and "recollection notes" not in plain, plain
assert "5." not in plain, plain

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
assert "nothing in this class matched" in gapful, gapful
# ...and that licence only exists when there is work to review.
assert "do not supply subject facts" not in A.build_system_instruction("", m, "")

print("ok - memory stays in its class, uploads stay demoted, prompt assembles in order")
