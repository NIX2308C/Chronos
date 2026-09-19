"""Profanity detection without a model call.

A word-set lookup is what this replaces, and a word-set lookup only ever catches
the spelling it was given: `fuck` and not `f*ck`, `fuuuck`, `f u c k`, `sh1t`,
`fucc` or `motherfucker`. Students find that boundary in about a minute.

So the text is normalized towards a canonical form first and matched second.
Every transform below exists because it defeats one real evasion, and the
matching is deliberately conservative in the other direction: substring matching
is what turns `class`, `cockpit` and `Scunthorpe` into false accusations, so the
roots that are substrings of innocent English words are matched as whole words
only, and the handful that still collide carry an explicit allowlist.

Pure and dependency-free, so the tests import it directly.
"""
import re
import unicodedata

# Digits and symbols students substitute for letters. Applied before matching, so
# `sh1t`, `b!tch`, `n1gg4` and `a$$hole` all reduce to their plain spelling.
_LEET = str.maketrans({
    "1": "i", "!": "i", "|": "i",
    "3": "e", "4": "a", "@": "a",
    "0": "o", "5": "s", "$": "s",
    "7": "t", "+": "t", "(": "c",
})

# Three or more of the same character in a row is padding, not spelling: no
# English word needs it, and `fuuuuuck` is the whole point. Two is left alone —
# collapsing it would wreck `pass`, `assess` and `bollocks` alike.
_RUNS = re.compile(r"(.)\1{2,}")

# A run of single characters held apart by punctuation or spaces — `f u c k`,
# `f-u-c-k`, `f.u.c.k`. Only this shape is de-spaced; removing whitespace from
# the text wholesale would fuse innocent neighbours into words nobody typed.
_SPACED_OUT = re.compile(r"\b(?:[a-z0-9][^a-z0-9]{1,2}){2,}[a-z0-9]\b")

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_VOWELS = "aeiou"

# Roots matched anywhere in a word, which is what catches the compounds:
# `motherfucker`, `clusterfuck`, `bullshitting`, `dumbasses`.
_SUBSTRING_ROOTS = {
    "fuck": "severe", "fuk": "severe", "fuc": "severe", "phuck": "severe",
    "shit": "severe", "cunt": "severe", "bitch": "severe", "bastard": "severe",
    "whore": "severe", "slut": "severe", "pussy": "severe", "asshole": "severe",
    "arsehole": "severe", "dumbass": "severe", "jackass": "severe",
    "nigg": "severe", "faggot": "severe", "retard": "severe",
    "wanker": "severe", "twat": "severe", "bollock": "severe",
    "motherf": "severe",
    "piss": "mild", "crap": "mild", "aupvibes": "mild",
}

# Roots that ARE innocent English inside other words. `ass` lives in class, pass,
# bass, assess and assignment; `cock` in cockpit, peacock and cockroach; `prick`
# is what you do to a finger in a biology lesson. Whole word or nothing.
# `dick`, `prick` and `hell` are deliberately absent. Moby Dick is on reading
# lists, you prick a finger in a biology lesson, and "what the hell" is in every
# novel a teenager is set. Blocking is outright, so a false positive here
# rejects a real question — the cost of missing a mild swear is far lower.
_WORD_ROOTS = {
    "ass": "mild", "arse": "mild", "cock": "severe",
    "damn": "mild", "wtf": "mild", "stfu": "mild",
    "fag": "severe", "bugger": "mild",
}

# Innocent words the substring pass would otherwise hit. Removed from the text
# before that pass rather than special-cased after it.
_ALLOWED = (
    "scunthorpe", "penistone", "niggle", "niggles", "niggling", "niggard",
    "niggardly", "retardant", "retardants", "shiitake", "assassin",
)
_ALLOWED_RE = re.compile(r"\b(?:%s)\b" % "|".join(_ALLOWED))


def _root_pattern(root):
    """A regex for `root` that tolerates censoring and separators.

    Between any two letters a separator may appear, so `f-u-c-k` reads as `fuck`.
    A vowel may be replaced by a symbol, so `f*ck` and `sh!t` read as themselves
    — but only by a symbol, never by another letter: allowing any vowel there
    would turn `shut the door` and `shot` into profanity.
    """
    sep = r"[^a-z0-9]{0,2}"
    parts = []
    for ch in root:
        parts.append("(?:%s|[^a-z0-9])" % ch if ch in _VOWELS else re.escape(ch))
    return re.compile(sep.join(parts))


_SUBSTRING_RE = [(_root_pattern(r), sev, r) for r, sev in _SUBSTRING_ROOTS.items()]


def normalize(text):
    """Fold accents, undo leetspeak, flatten padding, close up spaced-out words."""
    s = unicodedata.normalize("NFKD", str(text or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().translate(_LEET)
    s = _RUNS.sub(r"\1", s)
    return _SPACED_OUT.sub(lambda m: re.sub(r"[^a-z0-9]", "", m.group(0)), s)


def scan(text):
    """The worst term found, as {"severity", "term"}, or None.

    Severity exists so a teacher's feed can rank a slur above `crap`, and so a
    future per-course strictness setting has something to switch on. Blocking
    treats both the same.
    """
    norm = normalize(text)
    if not norm:
        return None

    hits = []
    for word in _WORDS.findall(norm):
        sev = _WORD_ROOTS.get(word) or _WORD_ROOTS.get(word.rstrip("s"))
        if sev:
            hits.append((sev, word))

    # The allowlist is subtracted before the substring pass, never after: the
    # point is that `Scunthorpe` never produces a match to explain away.
    haystack = _ALLOWED_RE.sub(" ", norm)
    for pattern, sev, root in _SUBSTRING_RE:
        if pattern.search(haystack):
            hits.append((sev, root))

    if not hits:
        return None
    severe = [h for h in hits if h[0] == "severe"]
    sev, term = (severe or hits)[0]
    return {"severity": sev, "term": term}


def is_profane(text):
    return scan(text) is not None
