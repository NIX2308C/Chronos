"""Self-check for profanity detection.  Run:  python test_profanity.py

Two corpora, and the second one matters as much as the first. Detection that
only ever gets stricter ends up refusing to answer a biology question about
pricking a finger, or a history question about Scunthorpe — and a student who is
blocked for asking something legitimate learns not to trust the tutor at all.

Pure: imports `profanity` alone, so it runs without app.py's environment.
"""
import profanity as p

# Every entry here was a miss under the old exact-word set. They are the shapes
# students actually type once they discover a filter exists.
EVASIONS = [
    "fuck",                 # the plain case, for a baseline
    "FUCK",                 # case
    "f*ck this",            # censored vowel
    "sh1t",                 # leetspeak
    "b!tch",
    "n1gg4",
    "a$$hole",
    "fuuuuck",              # padding
    "f u c k",              # spaced out
    "f-u-c-k",
    "f.u.c.k",
    "fucc",                 # respelled tail
    "motherfucker",         # compound
    "bullshitting",         # inflection
    "dumbass",
    "you retard",
    "fúck",            # accented
    "wtf is this",
]

# Every entry here is ordinary classroom English that a substring match would
# flag. `class` and `assess` alone would make the tutor unusable.
INNOCENT = [
    "what is osmosis",
    "class", "classic", "the classroom",
    "assess this", "assessment", "assignment", "assist me",
    "assassin", "bass guitar", "pass the test", "embarrass", "massive",
    "analysis", "canvass the results",
    "cockpit", "peacock", "cockroach",
    "shiitake mushrooms",
    "Charles Dickens", "Moby Dick",
    "Titan", "Uranus",
    "prick the skin with a needle",
    "what the hell happened in 1066",
    "the fire retardant",
    "Scunthorpe",
    "a niggling doubt",
    "shut the door", "I shot a photo",
    "hello", "thanks",
]

missed = [t for t in EVASIONS if not p.is_profane(t)]
assert not missed, "evasions slipped through: %r" % missed

flagged = [(t, p.scan(t)) for t in INNOCENT if p.is_profane(t)]
assert not flagged, "innocent text was flagged: %r" % flagged

# Severity is what lets a teacher's feed rank a slur above `damn`, and what a
# per-course strictness setting would switch on later. Blocking uses neither.
assert p.scan("f*ck")["severity"] == "severe"
assert p.scan("damn")["severity"] == "mild"
# A message carrying both reports the worse one.
assert p.scan("damn this fucking thing")["severity"] == "severe"

assert p.scan("") is None and p.scan(None) is None

# Normalization is the whole mechanism, so pin it directly: three transforms,
# each defeating one evasion.
assert p.normalize("FUUUCK") == "fuck"          # padding collapsed
assert p.normalize("sh1t") == "shit"            # leet mapped
assert p.normalize("f u c k") == "fuck"         # separators closed up
# ...but de-spacing only joins single letters. Whole words are left alone, or
# innocent neighbours would fuse into words nobody typed.
assert p.normalize("the class assessment") == "the class assessment"
# Two repeats are untouched: collapsing them would wreck ordinary spelling.
assert p.normalize("assess") == "assess"

print("ok - evasions caught, classroom English left alone, severity ranks")
