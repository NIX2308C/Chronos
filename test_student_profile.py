"""Self-check for the per-student profile.
Run:  python test_student_profile.py

The profile is a handful of counters that end up as two sentences inside the
tutor's system prompt and on a teacher's screen. Both are places where a mistake
is invisible rather than loud: nothing crashes, the tutor just starts explaining
at the wrong level, or a teacher reads a confident sentence about a child that
isn't true. So the things pinned here are the ones that would be silent —
additivity (because these are stored as Firestore increments, not recomputed),
the evidence threshold, and the fact that the wording never leaks a raw number.

Pure — no Firestore, no model, no app import.
"""
import student_profile as P

# ---------- measure is additive ----------

# This is the property that lets the counters live as increments. If measuring
# two messages separately ever diverged from measuring them together, every
# profile in the database would drift a little further from the truth with each
# message, and nothing would report it.
A, B = "What is osmosis? I don't follow the diagram.", "ok thx"
sep = P.measure(A)
for k, v in P.measure(B).items():
    sep[k] = sep.get(k, 0) + v
assert sep["msgs"] == 2, sep
assert sep["words"] == P.measure(A)["words"] + P.measure(B)["words"], sep

# Deterministic: the same text must never produce two different readings.
assert P.measure(A) == P.measure(A)

# Nothing measurable is nothing counted — an empty or punctuation-only message
# must not register as a message at all, or a student who sends "..." twice looks
# like someone who writes extremely short sentences.
assert P.measure("") == {}
assert P.measure("   ") == {}
assert P.measure("???") == {}

# The style signals each fire on the thing they claim to measure.
assert P.measure("hey u kno y")["txtspeak"] == 2          # "u" and "y"
assert P.measure("hello there")["lower_starts"] == 1
assert P.measure("Hello there")["lower_starts"] == 0
assert P.measure("Is this photosynthesis?")["questions"] == 1
assert P.measure("It is.")["questions"] == 0
assert P.measure("The mitochondria photosynthesise.")["long_words"] == 2

# ---------- the evidence threshold ----------

TERSE = {"msgs": 6, "words": 30, "sents": 6, "chars": 150, "word_chars": 110,
         "questions": 5, "lower_starts": 6, "txtspeak": 5, "long_words": 0,
         "gap_counts": {}}
VERBOSE = {"msgs": 6, "words": 320, "sents": 12, "chars": 1900, "word_chars": 1600,
           "questions": 5, "lower_starts": 0, "txtspeak": 0, "long_words": 50,
           "gap_counts": {}}

# Below the threshold the tutor is told nothing. Two messages is not evidence of
# a writing style, and a confident wrong read is worse than no read.
assert P.describe({}) == ""
assert P.describe(None) == ""
assert P.describe(dict(TERSE, msgs=P.PROFILE_MIN_MSGS - 1)) == ""
assert P.describe(dict(TERSE, msgs=P.PROFILE_MIN_MSGS)) != ""

# ---------- the description says something useful, and only that ----------

terse, verbose = P.describe(TERSE), P.describe(VERBOSE)
# Two genuinely different students must not get the same guidance, or the whole
# feature is an expensive no-op.
assert terse != verbose
assert "short" in terse and "informal" in terse, terse
assert "short" not in verbose, verbose
assert P.pitch(TERSE) == "plain" and P.pitch(VERBOSE) == "stretch"

# Counters are the implementation, not the message. A teacher reads this, and so
# does a model that has been told not to recite it back — neither should be
# handed "word_chars: 110" or anything resembling a score.
for blob in (terse, verbose):
    for leak in ("word_chars", "msgs", "lower_starts", "txtspeak", "long_words",
                 "score", "level"):
        assert leak not in blob, (leak, blob)

# The description is about how to explain, never about what the student is.
for bad in ("weak", "poor", "bad at", "struggling student", "low ability"):
    assert bad not in terse.lower(), bad

# ---------- sticking points ----------

# Asking something once is a question; coming back to it is a sticking point.
once = dict(TERSE, gap_counts={"what is osmosis": 1})
thrice = dict(TERSE, gap_counts={"what is osmosis": 3, "diffusion": 1})
assert P.sticking_points(once) == []
assert "osmosis" not in P.describe(once)
assert P.sticking_points(thrice) == [("what is osmosis", 3)]
assert "what is osmosis" in P.describe(thrice) and "diffusion" not in P.describe(thrice)

# Most-repeated first, and bounded — a student with twenty sticking points must
# not push twenty lines into every prompt they send.
many = dict(TERSE, gap_counts={"g%d" % i: i + 2 for i in range(20)})
pts = P.sticking_points(many)
assert len(pts) == P.PROFILE_GAP_SHOWN, pts
assert [n for _, n in pts] == sorted((n for _, n in pts), reverse=True), pts

# A corrupt or missing map must not throw in the middle of building a prompt.
assert P.sticking_points({"gap_counts": None}) == []
assert P.sticking_points({"gap_counts": "nonsense"}) == []
assert P.sticking_points({}) == []

# ---------- gap keys ----------

# Firestore map keys can't contain a dot — it is the path separator, so a key
# with one would silently write a nested object instead of a counter.
k = P.gap_key("What is osmosis? Explain... please.")
assert "." not in k and "?" not in k, k
# Two phrasings of one confusion have to land on one counter, or nothing ever
# reaches the "asked more than once" threshold.
assert P.gap_key("What is osmosis?") == P.gap_key("what is  osmosis")
assert P.gap_key("") == ""
assert len(P.gap_key("x " * 200)) <= P.PROFILE_GAP_CHARS

print("ok - profile is additive, silent without evidence, and leaks no counters")
