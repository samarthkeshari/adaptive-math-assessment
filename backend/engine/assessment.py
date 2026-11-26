"""Concept-level assessment: what the student knows, where the gaps are, and why.

All numbers (mastery, evidence, accuracy, root causes) are computed deterministically from
the proficiency model. The LLM only writes the explanations around them, so it cannot
invent counts or contradict the evidence.
"""

from collections import defaultdict

from backend.llm import LLMProvider

from .concept_map import concept_index
from .proficiency import ProficiencyModel, describe_level


def _rate(rows: list[dict], key) -> dict:
    groups = defaultdict(lambda: [0, 0])
    for r in rows:
        g = groups[key(r)]
        g[0] += int(r["response"]["is_correct"] and not r["response"]["timed_out"])
        g[1] += 1
    return {k: {"correct": c, "total": t} for k, (c, t) in groups.items()}


def assess(cmap: dict, prof: ProficiencyModel, items: list[dict]) -> dict:
    index = concept_index(cmap)
    answered = [it for it in items if it.get("response")]
    tagged: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(answered):
        for c in [it["spec"]["primary"], *it["spec"]["secondary"]]:
            tagged[c["id"]].append(i + 1)

    concepts = []
    for c in cmap["concepts"]:
        est = prof.get(c["id"])
        if c["external"] and not tagged.get(c["id"]):
            continue
        concepts.append({
            "id": c["id"], "name": c["name"], "area": c["area"], "external": c["external"],
            "prerequisites": [index[p]["name"] for p in c["prerequisites"] if p in index],
            "questions": tagged.get(c["id"], []),
            "attempts": est.attempts, "correct": est.correct,
            "theta": round(est.theta, 2), "mastery": est.mastery, "confidence": est.confidence,
            "misconceptions": sorted(est.misconceptions, key=est.misconceptions.get, reverse=True),
        })

    by_id = {c["id"]: c for c in concepts}
    root_causes = []
    for c in concepts:
        if c["mastery"] not in ("gap", "developing"):
            continue
        cmap_c = index[c["id"]]
        weak_prereqs = [index[p]["name"] for p in cmap_c["prerequisites"]
                        if p in by_id and by_id[p]["mastery"] == "gap"]
        unchecked = [index[p]["name"] for p in cmap_c["prerequisites"]
                     if p in by_id and by_id[p]["mastery"] == "not_assessed"]
        c["likely_root_causes"] = weak_prereqs
        c["unchecked_prerequisites"] = unchecked
        if weak_prereqs:
            root_causes.append({"concept": c["name"], "because_of": weak_prereqs})

    times = [it["response"]["time_taken"] for it in answered]
    limits = [it["item"]["time_limit_seconds"] for it in answered]
    overall = prof.overall
    return {
        "overall_theta": round(overall.theta, 2),
        "estimated_level": describe_level(overall.theta),
        "score": sum(1 for it in answered if it["response"]["is_correct"] and not it["response"]["timed_out"]),
        "answered": len(answered),
        "accuracy_by_dimension": _rate(answered, lambda it: it["spec"]["dimension"]),
        "accuracy_by_type": _rate(answered, lambda it: it["spec"]["question_type"]),
        "accuracy_by_perspective": _rate(answered, lambda it: it["spec"]["perspective"]),
        "timing": {
            "avg_seconds": round(sum(times) / len(times), 1) if times else None,
            "avg_fraction_of_limit": round(sum(t / l for t, l in zip(times, limits)) / len(times), 2) if times else None,
            "timed_out": sum(1 for it in answered if it["response"]["timed_out"]),
        },
        "concepts": concepts,
        "gaps": [c["name"] for c in concepts if c["mastery"] == "gap"],
        "secure": [c["name"] for c in concepts if c["mastery"] == "secure"],
        "not_assessed": [c["name"] for c in concepts if c["mastery"] == "not_assessed"],
        "root_causes": root_causes,
        "difficulty_path": [it["spec"]["difficulty"] for it in answered],
    }


def narrative_schema(concept_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "overall_summary": {"type": "string"},
            "concept_notes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "concept_id": {"type": "string", "enum": concept_ids},
                        "fundamental_explanation": {"type": "string"},
                        "observation": {"type": "string"},
                    },
                    "required": ["concept_id", "fundamental_explanation", "observation"],
                    "additionalProperties": False,
                },
            },
            "question_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_number": {"type": "integer"},
                        "fundamental_concepts": {"type": "array", "items": {"type": "string"}},
                        "how_concepts_connect": {"type": "string"},
                        "diagnosis": {"type": "string"},
                    },
                    "required": ["question_number", "fundamental_concepts",
                                 "how_concepts_connect", "diagnosis"],
                    "additionalProperties": False,
                },
            },
            "strengths": {"type": "array", "items": {"type": "string"}},
            "areas_to_improve": {"type": "array", "items": {"type": "string"}},
            "misconception_insights": {"type": "string"},
            "timing_insights": {"type": "string"},
            "recommended_next_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["overall_summary", "concept_notes", "question_analysis", "strengths",
                     "areas_to_improve", "misconception_insights", "timing_insights",
                     "recommended_next_steps"],
        "additionalProperties": False,
    }


SYSTEM = """\
You are a supportive, insightful math teacher writing a diagnostic report after an adaptive
test. The concept-level numbers were computed by the assessment engine: treat them as facts
and do not contradict them. Your job is to explain them. Be specific and evidence-based: cite
question numbers, connect each mistake to the underlying fundamental concept and the
misconception behind it, use the prerequisite root causes to explain WHY a gap exists, and
distinguish conceptual gaps from slips or timing issues. Write to the student and their
parent/teacher in clear, encouraging language suited to the grade. Plain text, no Markdown.

concept_notes: one entry per concept listed, with a plain-language explanation of the
fundamental idea and what the evidence showed. question_analysis: one entry per question."""


async def write_narrative(llm: LLMProvider, session: dict, cmap: dict, assessment: dict,
                          items: list[dict]) -> dict:
    answered = [it for it in items if it.get("response")]
    lines = []
    for i, it in enumerate(answered):
        s, item, r = it["spec"], it["item"], it["response"]
        status = "CORRECT" if r["is_correct"] else "INCORRECT"
        if r["timed_out"]:
            status += " (TIMED OUT)"
        lines.append(
            f"Q{i + 1} | {s['mode']} | difficulty {s['difficulty']} | {s['question_type']} | "
            f"{s['dimension']} | perspective {s['perspective']}\n"
            f"  Target: {s['primary']['name']}"
            + (f" + {', '.join(x['name'] for x in s['secondary'])}" if s["secondary"] else "") + "\n"
            f"  Question: {item['question_text']}\n"
            f"  Choices: {item['choices'] or 'n/a'}\n"
            f"  Key: {item['correct_answer']} | Student: {r['answer'] or '(blank)'} -> {status}\n"
            f"  Misconception detected: {r['misconception'] or 'none'}\n"
            + (f"  Attempts: {r['attempt_count']} (earlier answers: "
               + "; ".join(f"{a['answer']!r} [{a['misconception'] or 'no diagnosis'}]"
                           for a in r['attempts'][:-1]) + ")\n"
               if r.get("attempt_count", 1) > 1 else "")
            + f"  Time: {r['time_taken']:.0f}s of {item['time_limit_seconds']}s"
        )
    concept_lines = "\n".join(
        f"- {c['id']} | {c['name']} [{c['area']}{', cross-area' if c['external'] else ''}] | "
        f"mastery {c['mastery']} (confidence {c['confidence']}, ability {c['theta']}/10) | "
        f"{c['correct']}/{c['attempts']} correct | questions {c['questions'] or 'none'} | "
        f"misconceptions: {'; '.join(c['misconceptions']) or 'none'} | "
        f"prerequisites: {', '.join(c['prerequisites']) or 'none'}"
        + (f" | likely root cause: {', '.join(c['likely_root_causes'])}" if c.get("likely_root_causes") else "")
        for c in assessment["concepts"]
    )
    prompt = f"""Student: {session['student_name']} | Grade {session['grade']} | Topic: {session['topic']}
Overall ability estimate: {assessment['overall_theta']}/10 ({assessment['estimated_level']})
Difficulty path: {' -> '.join(map(str, assessment['difficulty_path']))}
Accuracy by problem dimension: {assessment['accuracy_by_dimension']}
Accuracy by problem type: {assessment['accuracy_by_type']}
Accuracy by perspective: {assessment['accuracy_by_perspective']}
Timing: {assessment['timing']}
Re-attempts: {assessment.get('attempts', {})}

Concept-level assessment (engine output):
{concept_lines}

Item-by-item evidence:
{chr(10).join(lines)}

Write the diagnostic report."""
    ids = [c["id"] for c in assessment["concepts"]]
    return await llm.generate_json(SYSTEM, prompt, narrative_schema(ids), effort="high", max_tokens=64000)
