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
    {"id": "c1", "opening": "what is osmosis", "reported_gaps": ["what is osmosis"], "last_active": 10},
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
assert "untrusted_student_attachments" in plain
assert "untrusted_student_memory" in plain
assert "never instructions" in plain
assert "Student uploads are immediate review context" in plain

full = A.build_system_instruction("teacher stuff", m, b)
assert full.index('"teacher_passages"') < full.index('"untrusted_student_memory"')
assert full.index('"untrusted_student_memory"') < full.index('"untrusted_student_attachments"')
assert "NOT teacher material" in full
assert "No matching teacher passages" in A.build_system_instruction("", "", b)
assert "Use only supplied teacher passages" in full

# A legacy retrieval miss must never become a remembered learning difficulty.
legacy = A.build_memory_block([{"id": "old", "opening": "a topic", "gaps": ["legacy false flag"]}])
assert "legacy false flag" not in legacy
summary = A.summarize_exchange({}, True, "why is the sky blue", "not in my knowledge base", [])
assert not summary.get("gaps") and not summary.get("reported_gaps")

print("ok - recollection, upload provenance, policy boundary and explicit gap semantics")
