"""Study mode: the tutor quizzes one question at a time and only moves on after mastery.

The model never decides mastery on its own say-so. It grades each answer with the
grade_answer tool (correct / partial / wrong); this module counts consecutive fully
correct answers per topic, and a topic is mastered only at MASTERY_STREAK in a row. A
partial or wrong answer resets the count. For a whole-course session the plan is the
course outline's topics, studied in order; the next topic opens only once the current
one is mastered.

State lives on the chat document (`study` field) so progress survives reloads and
devices. Pure functions here; app.py reads and writes Firestore.
"""
import os

import prompts

MASTERY_STREAK = max(1, int(os.getenv("STUDY_MASTERY_STREAK", "3")))
RESULTS = ("correct", "partial", "wrong")
SCOPES = ("topic", "course")
MAX_PLAN = 40
TOPIC_CHARS = 80


def _clean(text):
    return " ".join(str(text or "").split()).strip("\"'“”.")[:TOPIC_CHARS]


def normalize(raw):
    """A well-formed state from whatever was stored (or nothing)."""
    raw = raw if isinstance(raw, dict) else {}
    as_list = lambda v: [t for t in (_clean(x) for x in (v if isinstance(v, list) else [])) if t][:MAX_PLAN]
    scope = raw.get("scope") if raw.get("scope") in SCOPES else ""
    streak = raw.get("streak") if isinstance(raw.get("streak"), int) else 0
    return {"on": bool(raw.get("on")), "scope": scope, "topic": _clean(raw.get("topic")),
            "plan": as_list(raw.get("plan")), "mastered": as_list(raw.get("mastered")),
            "streak": max(0, streak), "complete": bool(raw.get("complete"))}


def _is_mastered(state, topic):
    return topic.lower() in {t.lower() for t in state["mastered"]}


def _next_in_plan(state, after=None):
    """First plan topic not yet mastered (skipping `after`), or None."""
    for t in state["plan"]:
        if not _is_mastered(state, t) and (after is None or t.lower() != after.lower()):
            return t
    return None


def set_topic(state, topic, scope, outline_topics):
    """The study_topic tool: choose one topic, or the whole course (outline order)."""
    state = dict(state)
    topic = _clean(topic)
    plan = [t for t in (_clean(x) for x in outline_topics or []) if t][:MAX_PLAN]
    if scope == "course" and plan:
        state["scope"], state["plan"] = "course", plan
        match = next((t for t in plan if t.lower() == topic.lower()), None)
        new = match or _next_in_plan(state) or plan[0]
    else:
        if not topic:
            return state
        state["scope"], state["plan"] = "topic", []
        new = topic
    if new.lower() != state["topic"].lower():
        state["streak"] = 0
    state["topic"], state["complete"] = new, False
    return state


def grade(state, result):
    """The grade_answer tool. Returns (new state, True if this answer completed a topic)."""
    state = dict(state)
    if not state["topic"] or state["complete"] or result not in RESULTS:
        return state, False
    state["streak"] = state["streak"] + 1 if result == "correct" else 0
    if state["streak"] < MASTERY_STREAK:
        return state, False
    done = state["topic"]
    if not _is_mastered(state, done):
        state["mastered"] = (state["mastered"] + [done])[-MAX_PLAN:]
    state["streak"] = 0
    if state["scope"] == "course":
        nxt = _next_in_plan(state)
        if nxt:
            state["topic"] = nxt
        else:
            state["topic"], state["complete"] = "", True
    return state, True


def public(state):
    """What the client shows: the chip above the composer."""
    return {"on": state["on"], "scope": state["scope"] or None, "topic": state["topic"] or None,
            "streak": state["streak"], "needed": MASTERY_STREAK, "mastered": state["mastered"],
            "plan": state["plan"], "complete": state["complete"]}


def prompt_block(state):
    """The STUDY MODE section of the tutor's system prompt: the rules, then where things stand."""
    lines = [prompts.fill("tutor/study/rules", needed=MASTERY_STREAK)]
    topic = state["topic"]
    if state["complete"]:
        lines.append(prompts.get("tutor/study/course_done"))
    elif not topic:
        lines.append(prompts.get("tutor/study/no_topic"))
    elif state["scope"] == "topic" and _is_mastered(state, topic):
        lines.append(prompts.fill("tutor/study/pick_next", topic=topic))
    else:
        lines.append(prompts.fill("tutor/study/topic", topic=topic, streak=state["streak"],
                                  needed=MASTERY_STREAK))
        if state["scope"] == "course":
            lines.append(prompts.fill("tutor/study/plan", plan=", ".join(state["plan"]),
                                      done=", ".join(state["mastered"]) or "none yet"))
        if state["streak"] == MASTERY_STREAK - 1:
            if state["scope"] == "course":
                nxt = _next_in_plan(state, after=topic)
                lines.append(prompts.fill("tutor/study/last_step_course", topic=topic, next=nxt) if nxt
                             else prompts.fill("tutor/study/last_step_final", topic=topic))
            else:
                lines.append(prompts.fill("tutor/study/last_step_topic", topic=topic))
    if state["scope"] == "topic" and state["mastered"]:
        lines.append(prompts.fill("tutor/study/mastered", done=", ".join(state["mastered"])))
    return "\n".join(lines)
