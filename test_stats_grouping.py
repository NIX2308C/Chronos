"""Self-check for the analytics grouping.  Run:  python test_stats_grouping.py

The dashboard leads with "gaps, most-asked first", so _group_questions decides
what a teacher is told to fix next. Getting the keying wrong either splits one
question into several weak rows or merges two different ones. Pure function —
Firestore is never touched.
"""
import app as A

g = A._group_questions

# Same question, different casing/spacing, asked by two students → one row.
rows = [
    (10.0, "What is osmosis?", "u1"),
    (20.0, "what is   osmosis?", "u2"),
    (30.0, "How do I complete the square?", "u1"),
]
out = g(rows)
assert len(out) == 2, out
assert out[0]["question"] == "What is osmosis?", out[0]      # first wording wins
assert out[0]["count"] == 2, out[0]
assert out[0]["students"] == 2, out[0]

# One student asking five times is one student, not five.
out = g([(1.0, "Is this on the test?", "u1")] * 5)
assert out[0]["count"] == 5 and out[0]["students"] == 1, out

# Ties break on recency: the more recently asked question ranks first.
out = g([(1.0, "old", "u1"), (99.0, "new", "u2")])
assert [r["question"] for r in out] == ["new", "old"], out

# Count beats recency.
out = g([(1.0, "twice", "u1"), (2.0, "twice", "u2"), (99.0, "once", "u3")])
assert out[0]["question"] == "twice", out

# Blank and whitespace-only questions are dropped, not counted as a row.
out = g([(1.0, "", "u1"), (2.0, "   ", "u2"), (3.0, None, "u3"), (4.0, "real", "u4")])
assert [r["question"] for r in out] == ["real"], out

# Empty input is an empty list, not a crash.
assert g([]) == []

# Knowledge Gaps only lists questions a teacher can act on. Retrieval finds
# nothing for a keysmash or a swear either, so without this they filled the list.
q = A._is_real_question
for junk in ["j", "g", "fuck", "shit", "fuck you", "wtf is this", "hi", "ok", "", None]:
    assert not q(junk), junk
for real in ["what is osmosis", "define photosynthesis", "How do I complete the square?",
             "qu'est-ce que l'osmose"]:
    assert q(real), real

print("ok - stats grouping: dedupe, distinct askers, ranking, and gap filtering hold")
