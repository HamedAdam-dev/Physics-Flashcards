"""Spaced-repetition scheduling (a simplified SM-2, the algorithm behind Anki).

Each card has a state:
    reps      successful reviews in a row
    interval  days until the next review
    ease      how fast the interval grows (starts 2.5, min 1.3)
    due       ISO timestamp of the next review (None = new card)
    lapses    times you have forgotten it
    suspended True = never shown (for bad or duplicate cards)
    history   list of {"t": time, "grade": 1-4, "secs": seconds taken}

Grades: 1 Again (forgot), 2 Hard, 3 Good, 4 Easy.
Speed rule: if you ran out of time, Good/Easy are capped at Hard, because the
goal is instant recall, not slow reconstruction.
"""

import datetime as dt

GRADES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}
MIN_EASE = 1.3
RELEARN_MINUTES = 1  # an "Again" card comes back in the same session


def new_state():
    return {"reps": 0, "interval": 0.0, "ease": 2.5, "due": None, "lapses": 0,
            "suspended": False, "history": []}


def next_interval(state, grade):
    """Days until the next review if this grade is given (no side effects)."""
    reps, interval, ease = state["reps"], state["interval"], state["ease"]
    if grade == 1:
        return RELEARN_MINUTES / (24 * 60)
    if grade == 2:
        return max(1.0, interval * 1.2) if reps else 0.5
    if grade == 3:
        return 1.0 if reps == 0 else 3.0 if reps == 1 else interval * ease
    return 4.0 if reps == 0 else interval * ease * 1.3


def apply_grade(state, grade, secs, timed_out, now=None):
    """Update state in place. Returns the grade actually applied."""
    now = now or dt.datetime.now()
    if timed_out and grade >= 3:
        grade = 2
    days = next_interval(state, grade)
    if grade == 1:
        state["reps"] = 0
        state["lapses"] += 1
        state["ease"] = max(MIN_EASE, state["ease"] - 0.2)
        state["interval"] = 0.0
    else:
        state["reps"] += 1
        state["interval"] = days
        if grade == 2:
            state["ease"] = max(MIN_EASE, state["ease"] - 0.15)
        elif grade == 4:
            state["ease"] += 0.15
    state["due"] = (now + dt.timedelta(days=days)).isoformat(timespec="seconds")
    state["history"].append({"t": now.isoformat(timespec="seconds"), "grade": grade,
                             "secs": round(secs, 1)})
    return grade


def is_due(state, now=None):
    now = now or dt.datetime.now()
    return state["due"] is not None and dt.datetime.fromisoformat(state["due"]) <= now


def describe(days):
    if days < 1 / 24:
        return f"{max(1, round(days * 24 * 60))}m"
    if days < 1:
        return f"{round(days * 24)}h"
    if days < 60:
        return f"{days:.0f}d" if days >= 2 else f"{days:.1f}d"
    return f"{days / 30:.0f}mo"
