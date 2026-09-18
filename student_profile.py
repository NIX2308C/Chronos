"""What the tutor notices about a student, without a model call.

`build_memory_block` already remembers *what* a student asked. It has no idea
*how* they write or how they are coping, so every student gets the same register
whether they type in careful paragraphs or in four lowercase words.

This module closes that gap arithmetically. Each message contributes a handful of
counters; the counters accumulate on one Firestore document per (student, class);
and the accumulated document renders into a couple of plain sentences telling the
model how to pitch an explanation. No second model call, so recollection stays
free in both tokens and latency, and — since a judgement about a child is the
worst possible place for a hallucination — nothing here can invent a claim.

Two deliberate limits:

* Nothing is said until PROFILE_MIN_MSGS messages have been seen. A confident
  wrong characterisation is worse than no characterisation, and three messages is
  not evidence of anything.
* The wording only ever describes *how to explain*, never what the student is.
  "Keep explanations plain" is useful to a tutor and fair to a teacher reading it
  over the student's shoulder; "weak student" is neither.

Pure and dependency-free, so the tests import it directly.
"""
import re

# Below this, say nothing at all. Five messages is roughly where a student's
# habitual register separates from "first thing they happened to type".
PROFILE_MIN_MSGS = 5
# A gap has to recur to be worth mentioning: asking something once is a question,
# asking it across three conversations is a sticking point.
PROFILE_GAP_MIN = 2
PROFILE_GAP_SHOWN = 3        # sticking points named in the prompt
PROFILE_MAX_GAPS = 40        # keys kept on the document, so it can't grow forever
PROFILE_GAP_CHARS = 60       # per remembered gap phrase

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_SENTS = re.compile(r"[.!?]+(?:\s|$)")
# Long enough to signal reach rather than just a long common word.
_LONG_WORD = 8

# Abbreviations that mark an informal register. Deliberately not slang-policing:
# this changes how the tutor writes back, nothing else.
_TXTSPEAK = frozenset("""
u ur urs r n y k kk plz pls thx thanks ty tysm idk idc imo imho tbh btw omg lol
lmao brb rn ngl fr fyi asap cuz coz cos bc bcz wanna gonna gotta dunno aint
""".split())


def measure(text):
    """Per-message counters. Additive: two messages measured apart sum to the
    same thing as the pair measured together, which is what lets these live as
    Firestore increments instead of a re-read of the student's whole history."""
    s = str(text or "").strip()
    if not s:
        return {}
    words = _WORDS.findall(s)
    if not words:
        return {}
    lower = [w.casefold() for w in words]
    # A trailing sentence with no terminator still counts as one, or a student
    # who never types a full stop reads as having written zero sentences.
    sents = len(_SENTS.findall(s)) or 1
    first = s[:1]
    return {
        "msgs": 1,
        "words": len(words),
        "sents": sents,
        "chars": len(s),
        "word_chars": sum(len(w) for w in words),
        "questions": 1 if "?" in s else 0,
        "lower_starts": 1 if first.isalpha() and first.islower() else 0,
        "txtspeak": sum(1 for w in lower if w in _TXTSPEAK),
        "long_words": sum(1 for w in words if len(w) >= _LONG_WORD),
    }


def _avg(prof, key, per="msgs"):
    n = float(prof.get(per) or 0)
    return (float(prof.get(key) or 0) / n) if n else 0.0


def pitch(prof):
    """How far to reach in an explanation: 'plain', 'steady' or 'stretch'.

    Read off length, vocabulary reach and how often the same thing has had to be
    revisited. It picks a register, not a grade — see the module docstring.
    """
    words_per_msg = _avg(prof, "words")
    long_ratio = (float(prof.get("long_words") or 0) / float(prof.get("words") or 1))
    sticky = len(sticking_points(prof))
    score = 0
    score += 1 if words_per_msg >= 25 else (-1 if words_per_msg <= 8 else 0)
    score += 1 if long_ratio >= 0.12 else (-1 if long_ratio <= 0.04 else 0)
    score -= 1 if sticky >= 2 else 0
    if score >= 2:
        return "stretch"
    if score <= -1:
        return "plain"
    return "steady"


def sticking_points(prof):
    """Gaps this student has come back to, most-repeated first."""
    counts = prof.get("gap_counts") or {}
    if not isinstance(counts, dict):
        return []
    rows = [(g, int(n or 0)) for g, n in counts.items() if int(n or 0) >= PROFILE_GAP_MIN]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return rows[:PROFILE_GAP_SHOWN]


_PITCH_ADVICE = {
    "plain": "Keep explanations plain and short, one idea at a time, and check "
             "they have followed before moving on.",
    "steady": "Explain at a normal level and check understanding as you go.",
    "stretch": "They can handle a fuller explanation and precise terminology; "
               "don't over-simplify.",
}


def describe(prof):
    """The lines that go into the system prompt, or "" when there isn't enough
    evidence to say anything honest."""
    prof = prof or {}
    if int(prof.get("msgs") or 0) < PROFILE_MIN_MSGS:
        return ""

    words_per_msg = _avg(prof, "words")
    words_per_sent = (float(prof.get("words") or 0) / float(prof.get("sents") or 1))
    informal = _avg(prof, "txtspeak") >= 0.5 or _avg(prof, "lower_starts") >= 0.6

    if words_per_msg <= 8:
        length = "very short messages"
    elif words_per_msg <= 20:
        length = "fairly short messages"
    elif words_per_msg <= 45:
        length = "messages of a moderate length"
    else:
        length = "long, detailed messages"
    register = "informally" if informal else "in full, punctuated sentences"

    style = "They tend to write %s, %s." % (length, register)
    if words_per_sent >= 18:
        style += " Their sentences run long."

    lines = ["How this student writes, and how to pitch your answer:", "- " + style]

    sticky = sticking_points(prof)
    if sticky:
        lines.append("- They have come back to the same difficulty more than once: "
                     + "; ".join("%s (%d times)" % (g, n) for g, n in sticky) + ".")

    lines.append("- " + _PITCH_ADVICE[pitch(prof)])
    return "\n".join(lines)


def gap_key(text):
    """Normalize a gap phrase into a counter key.

    Firestore map keys cannot contain `.` (it is the path separator), `/`, `~`,
    `*` or `[`/`]`, and two phrasings of the same confusion should land on one
    key rather than two. Lowercase the words and rejoin them: "What is osmosis?"
    and "what is osmosis" become the same sticking point.
    """
    words = _WORDS.findall(str(text or "").casefold())
    return " ".join(words)[:PROFILE_GAP_CHARS]
