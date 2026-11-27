"""Holistic progress report: everything we know about a student, across all sessions.

Numbers are computed deterministically from the performance memory; the optional coach
summary is written by the LLM on top of them and saved to progress_reports.jsonl.
"""

import time
from collections import defaultdict

from backend.llm import LLMProvider
from backend.storage.memory import Memory

from .proficiency import describe_level

MASTERY_ORDER = {"gap": 0, "developing": 1, "secure": 2, "not_assessed": 3}


def _rate(items: list[dict], key) -> dict:
    groups = defaultdict(lambda: [0, 0])
    for it in items:
        g = groups[key(it)]
        g[0] += int(it["response"]["is_correct"] and not it["response"]["timed_out"])
        g[1] += 1
    return {k: {"correct": c, "total": t} for k, (c, t) in groups.items()}


def build_progress(memory: Memory, key: str) -> dict | None:
    st = memory.students.get(key)
    if not st:
        return None
    sessions = sorted((memory.sessions[sid] for sid in st["sessions"]), key=lambda s: s["started_at"])
    answered = [(s, it) for s in sessions for it in s["items"] if it["response"]]
    items = [it for _, it in answered]
    completed = [s for s in sessions if s["status"] == "completed"]

    # Ability over time: one point per completed session.
    trend = [{
        "session_id": s["id"], "date": s["finished_at"], "topic": s["topic"], "grade": s["grade"],
        "ability": round(s["proficiency"].overall.theta, 2),
        "score": s["assessment"]["score"], "answered": s["assessment"]["answered"],
    } for s in completed]

    # Per-concept history: the session estimate after each session that tested the concept.
    history: dict[str, list[dict]] = defaultdict(list)
    for s in sessions:
        for cid, est in s["proficiency"].concepts.items():
            if est.attempts:
                history[cid].append({"date": s["last_activity"], "topic": s["topic"],
                                     "theta": round(est.theta, 2)})
    last_correct: dict[str, float] = {}
    for s, it in answered:
        if it["response"]["is_correct"]:
            for c in [it["spec"]["primary"], *it["spec"]["secondary"]]:
                last_correct[c["id"]] = max(last_correct.get(c["id"], 0), it["served_at"])

    concepts = []
    for cid, est in st["concepts"].items():
        if not est.attempts:
            continue
        info = st["concept_info"].get(cid, {})
        h = history.get(cid, [])
        concepts.append({
            "id": cid, "name": info.get("name", cid), "area": info.get("area", "Other"),
            "topics": info.get("topics", []), "last_seen": info.get("last_seen"),
            "theta": round(est.theta, 2), "mastery": est.mastery, "confidence": est.confidence,
            "attempts": est.attempts, "correct": est.correct,
            "sessions": len(h),
            "growth": round(h[-1]["theta"] - h[0]["theta"], 2) if len(h) >= 2 else None,
            "misconceptions": sorted(est.misconceptions, key=est.misconceptions.get, reverse=True),
        })
    concepts.sort(key=lambda c: (MASTERY_ORDER[c["mastery"]], c["theta"]))

    areas = defaultdict(list)
    for c in concepts:
        areas[c["area"]].append(c)
    area_summary = sorted(({
        "area": a, "avg_theta": round(sum(c["theta"] for c in cs) / len(cs), 2),
        "secure": sum(c["mastery"] == "secure" for c in cs),
        "developing": sum(c["mastery"] == "developing" for c in cs),
        "gap": sum(c["mastery"] == "gap" for c in cs),
        "concepts": len(cs),
    } for a, cs in areas.items()), key=lambda x: x["avg_theta"])

    topics = defaultdict(list)
    for s in sessions:
        topics[(s["topic"].strip().lower(), s["grade"])].append(s)
    topic_summary = []
    for (_, grade), ss in topics.items():
        done = [s for s in ss if s["status"] == "completed"]
        topic_summary.append({
            "topic": ss[-1]["topic"], "grade": grade, "sessions": len(ss), "completed": len(done),
            "first_ability": round(done[0]["proficiency"].overall.theta, 2) if done else None,
            "latest_ability": round(done[-1]["proficiency"].overall.theta, 2) if done else None,
            "latest_gaps": done[-1]["assessment"]["gaps"] if done else [],
            "last_activity": max(s["last_activity"] for s in ss),
        })
    topic_summary.sort(key=lambda t: -t["last_activity"])

    names = {c["id"]: c["name"] for c in concepts}
    misc = {}
    for m in st["misconceptions"]:
        rec = misc.setdefault(m["misconception"], {"misconception": m["misconception"],
                                                   "concept": names.get(m["concept_id"], m["concept_id"]),
                                                   "concept_id": m["concept_id"], "count": 0,
                                                   "sessions": set(), "last_seen": 0})
        rec["count"] += 1
        rec["sessions"].add(m["session_id"])
        rec["last_seen"] = max(rec["last_seen"], m["ts"])
    misconceptions = []
    for rec in misc.values():
        est = st["concepts"].get(rec["concept_id"])
        improving = last_correct.get(rec["concept_id"], 0) > rec["last_seen"]
        status = ("likely resolved" if improving and est and est.mastery == "secure"
                  else "improving" if improving else "still present")
        misconceptions.append({**rec, "sessions": len(rec["sessions"]), "status": status})
    misconceptions.sort(key=lambda r: (r["status"] != "still present", -r["count"]))

    times = [it["response"]["time_taken"] for it in items]
    fracs = [it["response"]["time_taken"] / it["item"]["time_limit_seconds"] for it in items
             if it["item"]["time_limit_seconds"]]                # skip untimed questions
    score = sum(1 for it in items if it["response"]["is_correct"] and not it["response"]["timed_out"])
    latest = trend[-1]["ability"] if trend else None
    return {
        "student_name": st["name"],
        "generated_at": time.time(),
        "summary": {
            "sessions_completed": len(completed),
            "sessions_in_progress": sum(s["status"] == "in_progress" for s in sessions),
            "questions_answered": len(items),
            "correct_in_time": score,
            "accuracy": round(score / len(items), 3) if items else None,
            "minutes_practiced": round(sum(times) / 60, 1),
            "topics": len(topic_summary),
            "concepts_assessed": len(concepts),
            "secure": sum(c["mastery"] == "secure" for c in concepts),
            "developing": sum(c["mastery"] == "developing" for c in concepts),
            "gaps": sum(c["mastery"] == "gap" for c in concepts),
            "latest_ability": latest,
            "latest_level": describe_level(latest) if latest is not None else None,
            "first_activity": sessions[0]["started_at"] if sessions else None,
            "last_activity": max((s["last_activity"] for s in sessions), default=None),
        },
        "ability_trend": trend,
        "topics": topic_summary,
        "areas": area_summary,
        "concepts": concepts,
        "strengths": [c["name"] for c in sorted(concepts, key=lambda c: -c["theta"]) if c["mastery"] == "secure"][:6],
        "focus_areas": [c["name"] for c in concepts if c["mastery"] == "gap"][:6],
        "misconceptions": misconceptions[:12],
        "accuracy_by_dimension": _rate(items, lambda it: it["spec"]["dimension"]),
        "accuracy_by_type": _rate(items, lambda it: it["spec"]["question_type"]),
        "accuracy_by_perspective": _rate(items, lambda it: it["spec"]["perspective"]),
        "timing": {
            "avg_seconds": round(sum(times) / len(times), 1) if times else None,
            "avg_fraction_of_limit": round(sum(fracs) / len(fracs), 2) if fracs else None,
            "timed_out": sum(1 for it in items if it["response"]["timed_out"]),
        },
        "evidence_count": len(items),  # used to tell whether a saved coach summary is stale
    }


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "overall_progress": {"type": "string"},
        "growth_highlights": {"type": "array", "items": {"type": "string"}},
        "persistent_gaps": {"type": "array", "items": {"type": "string"}},
        "misconception_patterns": {"type": "string"},
        "learning_habits": {"type": "string"},
        "practice_plan": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string"},
                    "why": {"type": "string"},
                    "activity": {"type": "string"},
                },
                "required": ["focus", "why", "activity"],
                "additionalProperties": False,
            },
        },
        "encouragement": {"type": "string"},
    },
    "required": ["overall_progress", "growth_highlights", "persistent_gaps", "misconception_patterns",
                 "learning_habits", "practice_plan", "encouragement"],
    "additionalProperties": False,
}

SYSTEM = """\
You are an experienced math coach reviewing a student's whole learning history across many
adaptive assessment sessions. The numbers come from the assessment engine: treat them as facts
and do not contradict them. Look for the big picture: growth over time, concepts that stay weak
across sessions and topics, misconceptions that recur or have been resolved, how the student
handles word vs direct and single vs multi-concept problems, and timing habits. Be specific,
cite concepts and topics by name, and write in warm, clear language for the student and their
parent/teacher. The practice plan should have 3-5 concrete, prioritized steps, prerequisites
first. Plain text only, no Markdown."""


async def write_summary(llm: LLMProvider, progress: dict) -> dict:
    s = progress["summary"]
    concept_lines = "\n".join(
        f"- {c['name']} [{c['area']}] | {c['mastery']} (confidence {c['confidence']}, ability {c['theta']}/10)"
        f" | {c['correct']}/{c['attempts']} correct over {c['sessions']} session(s)"
        + (f" | growth {c['growth']:+.1f}" if c["growth"] is not None else "")
        + (f" | misconceptions: {'; '.join(c['misconceptions'])}" if c["misconceptions"] else "")
        + f" | topics: {', '.join(c['topics'])}"
        for c in progress["concepts"]
    )
    trend = "\n".join(f"- {t['topic']} (grade {t['grade']}): ability {t['ability']}/10, {t['score']}/{t['answered']}"
                      for t in progress["ability_trend"]) or "(no completed sessions)"
    misc = "\n".join(f"- {m['misconception']} [{m['concept']}]: seen {m['count']}x in {m['sessions']} session(s), {m['status']}"
                     for m in progress["misconceptions"]) or "(none recorded)"
    prompt = f"""Student: {progress['student_name']}
Sessions completed: {s['sessions_completed']} | in progress: {s['sessions_in_progress']}
Questions answered: {s['questions_answered']} | correct in time: {s['correct_in_time']} | minutes practiced: {s['minutes_practiced']}
Latest ability: {s['latest_ability']} ({s['latest_level']})

Ability by session (oldest first):
{trend}

Concept mastery across all sessions:
{concept_lines}

Strand averages: {[(a['area'], a['avg_theta']) for a in progress['areas']]}
Accuracy single vs multi-concept: {progress['accuracy_by_dimension']}
Accuracy word vs direct: {progress['accuracy_by_type']}
Accuracy by perspective: {progress['accuracy_by_perspective']}
Timing: {progress['timing']}

Misconceptions:
{misc}

Write the holistic progress summary."""
    return await llm.generate_json(SYSTEM, prompt, SUMMARY_SCHEMA, effort="high", max_tokens=32000)
