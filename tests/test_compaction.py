"""History compaction: the newest turns are untouched, older ones shorten before any drop."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as A


def turns(n, size):
    return [{"role": "user" if i % 2 == 0 else "model", "parts": [{"text": ("w%d " % i) * size}]} for i in range(n)]


def total(ts):
    return sum(len(t["parts"][0]["text"]) for t in ts)


def test_nothing_changes_when_it_fits():
    ts = turns(10, 20)
    assert A.trim_history(ts, 10 ** 6) == ts
    print("ok - a replay that fits is left alone")


def test_older_turns_shorten_before_any_drop():
    ts = turns(30, 400)                       # ~1,300 characters each
    limit = total(ts) - 5000
    out = A.trim_history(ts, limit)
    assert len(out) == 30 and total(out) <= limit, (len(out), total(out), limit)
    assert out[-A.HISTORY_RECENT_MESSAGES:] == ts[-A.HISTORY_RECENT_MESSAGES:]
    assert out[0]["parts"][0]["text"].endswith("…") and ts[0]["parts"][0]["text"] != out[0]["parts"][0]["text"]
    assert total(ts) > limit                  # the input was not modified
    print("ok - older turns are shortened first and the newest stay verbatim")


def test_drops_only_after_shortening_and_starts_on_user():
    ts = turns(40, 400)
    limit = A.HISTORY_RECENT_MESSAGES * 1400 + 3000
    out = A.trim_history(ts, limit)
    assert 0 < len(out) < 40 and out[0]["role"] == "user" and total(out) <= limit
    assert out[-A.HISTORY_RECENT_MESSAGES:] == ts[-A.HISTORY_RECENT_MESSAGES:]
    print("ok - oldest turns drop last, and the replay starts on a student turn")


class _Doc:
    def __init__(self, i): self.i = i
    def to_dict(self): return {"role": "student" if self.i % 2 == 0 else "teacher", "content": "m%d" % self.i}


class _Ref:
    def __init__(self, n): self.n = n
    def order_by(self, *a, **k): return self
    def limit(self, k): self.k = k; return self
    def stream(self): return iter([_Doc(i) for i in range(self.n - self.k, self.n)][::-1])


def test_summarized_turns_are_not_replayed():
    ref = _Ref(50)
    full = A.load_history(ref, limit=20)
    skipped = A.load_history(_Ref(50), limit=20, skip_through=40, total=50)
    texts = [t["parts"][0]["text"] for t in skipped]
    assert texts[0] == "m40" and texts[-1] == "m49" and len(skipped) == 10, texts
    assert len(full) == 20
    print("ok - turns already in the summary are not replayed")


if __name__ == "__main__":
    test_nothing_changes_when_it_fits()
    test_older_turns_shorten_before_any_drop()
    test_drops_only_after_shortening_and_starts_on_user()
    test_summarized_turns_are_not_replayed()
