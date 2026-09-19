"""Self-check for the quiz answer key.  Run:  python test_quiz_answers.py

Every generated quiz used to have its correct answer on option A. Two reasons,
both covered here: the validator collapsed any answer key it did not recognise
to index 0, and nothing ever shuffled the options. These call the parser
directly — no Firestore, no model, no network.
"""
import random

# The app lives one directory up, so make it importable when this is run
# from anywhere (tests/, the repo root, or a runner's checkout).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A


def parse(questions, **extra):
    """Run one fabricated model reply through the real validator."""
    payload = dict({"type": "quiz", "title": "Osmosis", "questions": questions}, **extra)
    return A._parse_tool_result(A.json.dumps(payload), "quiz")


def correct_text(question):
    return question["options"][question["answer"]]


def test_answer_keys_models_actually_emit():
    """Each of these is ordinary model output for a multiple-choice key, and each
    one used to silently become option A."""
    opts = ["water", "salt", "sugar", "protein"]
    cases = [
        ({"answer": 2}, "sugar", "a plain index broke"),
        ({"answer": "2"}, "sugar", "a quoted digit fell back to A"),
        ({"answer": 2.0}, "sugar", "an integral float fell back to A"),
        ({"answer": "C"}, "sugar", "an uppercase letter fell back to A"),
        ({"answer": "c)"}, "sugar", "a letter with punctuation fell back to A"),
        ({"answer": "(D)"}, "protein", "a bracketed letter fell back to A"),
        ({"answer": "Sugar"}, "sugar", "the answer's own text fell back to A"),
        ({"correct_index": 1}, "salt", "a renamed key fell back to A"),
        ({"answer_index": 3}, "protein", "a renamed key fell back to A"),
        ({"correct_answer": "salt"}, "salt", "a renamed key fell back to A"),
    ]
    for key, expected, message in cases:
        result = parse([dict({"prompt": "which one", "options": list(opts),
                              "explanation": "because"}, **key)])
        assert result, f"{key} produced no quiz at all"
        assert correct_text(result["questions"][0]) == expected, f"{message}: {key}"


def test_unusable_keys_are_dropped_not_guessed():
    """A key that cannot be resolved is a dropped question, never a confident
    wrong answer — a student who is told A is right when it is not has been
    taught the wrong thing."""
    opts = ["water", "salt", "sugar", "protein"]
    bad = [
        {"answer": True},          # isinstance(True, int) — used to mean option B
        {"answer": "the second"},  # matches no option and no index
        {"answer": None},
        {"answer": 9},             # out of range
        {"answer": 2.5},
        {},                        # no key at all
    ]
    for key in bad:
        q = dict({"prompt": "which one", "options": list(opts)}, **key)
        assert parse([q]) is None, f"an unusable answer key was guessed at: {key}"

    # One bad question among good ones costs only that question.
    result = parse([
        {"prompt": "good", "options": list(opts), "answer": 1},
        {"prompt": "bad", "options": list(opts), "answer": "the second"},
    ])
    assert result and len(result["questions"]) == 1, "a bad question took the whole quiz down"
    assert correct_text(result["questions"][0]) == "salt"


def test_fifth_option_does_not_orphan_the_answer():
    """Only four options are stored. The bound check used to run against the
    untruncated list, so answer 4 pointed past the end and the UI marked nothing
    correct however the student clicked."""
    five = ["a", "b", "c", "d", "e"]
    result = parse([{"prompt": "q", "options": five, "answer": 4}])
    assert result is None, "an answer outside the four stored options was kept"

    result = parse([{"prompt": "q", "options": five, "answer": 1}])
    q = result["questions"][0]
    assert len(q["options"]) == 4, q["options"]
    assert 0 <= q["answer"] < len(q["options"]), "the stored index is out of range"
    assert correct_text(q) == "b"


def test_the_answer_is_not_always_option_a():
    """The symptom. A model that puts the answer first every single time must
    still produce a quiz whose answers are spread across the options."""
    random.seed(11)
    positions = set()
    for i in range(40):
        result = parse([{"prompt": f"q{i}", "options": ["right", "w1", "w2", "w3"],
                         "answer": 0}])
        q = result["questions"][0]
        assert correct_text(q) == "right", "shuffling lost track of the correct option"
        positions.add(q["answer"])
    assert len(positions) > 1, "every answer still landed in the same position"
    assert positions == {0, 1, 2, 3}, f"answers only ever reached {sorted(positions)}"


def test_prompt_no_longer_shows_zero_as_the_example():
    """The generator's only exemplar used to be "answer":0, at temperature 0.2 —
    the model was being taught the bias as well."""
    prompt = A._tool_prompt({"type": "quiz", "topic": "osmosis"}, "", [])
    assert '"answer":0' not in prompt, "the schema example still hands the model index 0"
    assert "0-based" in prompt, "the prompt never says what the index is based on"


if __name__ == "__main__":
    test_answer_keys_models_actually_emit()
    test_unusable_keys_are_dropped_not_guessed()
    test_fifth_option_does_not_orphan_the_answer()
    test_the_answer_is_not_always_option_a()
    test_prompt_no_longer_shows_zero_as_the_example()
    print("ok — answer keys resolve, unusable ones are dropped, the fifth option "
          "can't orphan the key, and the correct answer is no longer always A")
