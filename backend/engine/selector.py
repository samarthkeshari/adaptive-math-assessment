"""Adaptive item selection: decide WHAT to ask next and WHY, before any text is written.

The policy works like a tutor running a diagnostic:
  explore    - a concept with no evidence yet (foundational concepts first)
  root_cause - a prerequisite of a concept that looks shaky (find where the gap starts)
  probe      - re-test a concept the student just missed, easier and isolated, to confirm a gap
  confirm    - a developing concept seen from a new perspective
  stretch    - a secure concept, harder and combined with another area

Then it sets difficulty around the concept's ability estimate (where an item is most
informative), and picks single vs multi-concept, word vs direct, the perspective, and
the answer format, keeping the overall test balanced.
"""

import random
from collections import Counter

from .concept_map import core_concepts
from .proficiency import GAP_THETA, ProficiencyModel, clamp_difficulty

PERSPECTIVES = {
    "procedural": "Carry out a computation or procedure fluently.",
    "conceptual": "Explain or identify WHY something is true; understanding over computation.",
    "application": "Apply the concept to a realistic situation.",
    "reverse": "Work backwards: the result is given, find the missing input or condition.",
    "error_analysis": "A fictional student made a mistake; identify or correct it.",
    "estimation": "Estimate or judge reasonableness without exact computation.",
    "representation": "Translate between representations (words, numbers, tables, number lines, described diagrams, equations).",
    "pattern": "Spot, extend, or generalize a pattern or relationship.",
}
DIAGNOSTIC_PERSPECTIVES = ["error_analysis", "conceptual", "representation"]
WORD_LEANING = {"application", "estimation"}


def _public(c: dict) -> dict:
    return {k: c[k] for k in ("id", "name", "area", "description")}


def select_next(cmap: dict, prof: ProficiencyModel, items: list[dict],
                memory_gaps: dict[str, list[str]], seed: dict | None = None) -> dict:
    """memory_gaps: concept_id -> misconceptions recorded for this student in earlier tests.
    seed: for sessions built from the user's own question (see question_analyzer)."""
    core = core_concepts(cmap)
    if seed and not items:
        return _mirror_spec(cmap, prof, seed)
    order = {c["id"]: i for i, c in enumerate(core)}
    last_primary = items[-1]["spec"]["primary"]["id"] if items else None
    suspected = {
        c["id"] for c in core
        if prof.get(c["id"]).attempts and (prof.get(c["id"]).theta < GAP_THETA
                                           or (prof.get(c["id"]).last_outcome or 0) < 1)
    }
    prereqs = {c["id"]: c["prerequisites"] for c in core}

    def ancestors(cid: str, seen=None) -> set[str]:
        seen = set() if seen is None else seen
        for p in prereqs.get(cid, []):
            if p not in seen:
                seen.add(p)
                ancestors(p, seen)
        return seen

    prereq_of_suspected = {
        p: s for s in suspected for p in next(c for c in core if c["id"] == s)["prerequisites"]
    }

    best = None
    for c in core:
        est = prof.get(c["id"])
        cid = c["id"]
        if est.attempts == 0 and cid in prereq_of_suspected:
            dependent = next(x for x in core if x["id"] == prereq_of_suspected[cid])
            score, mode = 4.0, "root_cause"
            why = (f"'{dependent['name']}' looks shaky, so checking its prerequisite "
                   f"'{c['name']}' to find where the gap starts.")
        elif est.attempts == 0:
            score, mode = 3.0 - 0.15 * order[cid], "explore"
            why = f"No evidence yet on '{c['name']}', so exploring it."
            if cid in memory_gaps:
                score += 0.8
                why = f"'{c['name']}' was a difficulty in an earlier test, so checking it again."
        elif (est.last_outcome or 0) < 1 and est.attempts < 2:
            score, mode = 2.6, "probe"
            why = (f"Missed '{c['name']}' last time, so re-testing it in isolation at an easier "
                   "level to tell a real gap from a slip.")
        elif est.mastery == "gap" and est.confidence == "low":
            score, mode = 2.2, "probe"
            why = f"'{c['name']}' may be a gap; gathering more evidence."
        elif est.mastery == "gap":
            # Confirmed gap: revisit only occasionally, from a new angle, once other concepts are covered.
            score, mode = 0.7, "probe"
            why = f"Revisiting the gap in '{c['name']}' from a different angle."
        elif est.mastery == "secure":
            score, mode = (1.2 if est.attempts < 3 else 0.6), "stretch"
            why = f"'{c['name']}' looks secure, so stretching it with a harder, combined problem."
        else:
            score, mode = 1.8 - 0.2 * est.attempts, "confirm"
            why = f"'{c['name']}' is developing; confirming it from a new perspective."
        if cid == last_primary:
            # Probing right after a miss is useful (slip or gap?); otherwise vary the concept.
            score -= 0.5 if mode == "probe" else 2.0
        if mode in ("explore", "confirm", "stretch") and ancestors(cid) & suspected:
            score -= 1.5  # don't build on a foundation that currently looks shaky
        score += 0.3 / (1.0 + est.evidence)
        if seed and cid in seed["question_concept_ids"]:
            score += 0.4  # stay close to the concepts the user's question actually uses
        if best is None or score > best[0]:
            best = (score, c, mode, why)

    _, primary, mode, rationale = best
    theta = prof.theta_for(primary["id"])
    if not prof.get(primary["id"]).attempts:
        # Untested: don't start far above its weakest tested prerequisite.
        tested = [prof.get(p).theta for p in ancestors(primary["id"]) if prof.get(p).attempts]
        if tested:
            theta = min(theta, min(tested) + 1.0)

    # Difficulty: near theta (most informative), shifted by intent.
    last_d = next((it["spec"]["difficulty"] for it in reversed(items)
                   if it["spec"]["primary"]["id"] == primary["id"]), None)
    if mode == "probe":
        difficulty = clamp_difficulty(min(theta, last_d if last_d is not None else theta) - 1)
    elif mode == "root_cause":
        difficulty = clamp_difficulty(theta - 0.5)
    elif mode == "stretch":
        difficulty = clamp_difficulty(theta + 1.5)
    else:
        difficulty = clamp_difficulty(theta)

    # Complexity: isolate when diagnosing; combine areas when the concept is holding up.
    recent_multi = any(it["spec"]["dimension"] == "multi_concept" for it in items[-3:])
    if mode in ("probe", "root_cause"):
        dimension = "direct_concept"
    elif mode == "stretch" or (mode == "confirm" and theta >= 4.5):
        dimension = "multi_concept"
    elif len(items) >= 2 and not recent_multi and theta >= 4:
        dimension = "multi_concept"
    else:
        dimension = "direct_concept"

    secondary = []
    if dimension == "multi_concept":
        used = Counter(s["id"] for it in items for s in it["spec"]["secondary"])
        others = [c for c in cmap["concepts"] if c["id"] != primary["id"]
                  and (c["external"] or c["area"] != primary["area"])]
        if not others:
            others = [c for c in core if c["id"] != primary["id"] and prof.get(c["id"]).attempts]
        if others:
            others.sort(key=lambda c: (used[c["id"]], not c["external"], random.random()))
            secondary = [others[0]]
        else:
            dimension = "direct_concept"

    # Perspective: least-used for this concept, then overall; diagnostic angles when probing.
    used_here = Counter(it["spec"]["perspective"] for it in items
                        if it["spec"]["primary"]["id"] == primary["id"])
    used_all = Counter(it["spec"]["perspective"] for it in items)
    pool = DIAGNOSTIC_PERSPECTIVES if mode in ("probe", "root_cause") else list(PERSPECTIVES)
    perspective = min(pool, key=lambda p: (used_here[p], used_all[p], random.random()))

    # Word vs direct: keep the test balanced, with a lean for applied perspectives.
    n_word = sum(it["spec"]["question_type"] == "word" for it in items)
    n_direct = len(items) - n_word
    if perspective in WORD_LEANING and n_word <= n_direct + 1:
        question_type = "word"
    elif n_word != n_direct:
        question_type = "word" if n_word < n_direct else "direct"
    else:
        question_type = random.choice(["word", "direct"])

    # Multiple choice lets distractors diagnose misconceptions; keep it ~1/3 of items.
    n_mc = sum(it["spec"]["answer_format"] == "multiple_choice" for it in items)
    answer_format = ("multiple_choice"
                     if mode == "probe" or n_mc < (len(items) + 1) / 3 else "short_answer")

    if seed:
        # Keep the structure of the user's question; probes may still use diagnostic choices.
        question_type = seed["structure"]["problem_type"]
        if seed["structure"]["answer_format"] == "multiple_choice":
            answer_format = "multiple_choice"

    return {
        "mode": mode,
        "rationale": rationale,
        "primary": _public(primary),
        "secondary": [_public(s) for s in secondary],
        "difficulty": difficulty,
        "dimension": dimension,
        "question_type": question_type,
        "perspective": perspective,
        "answer_format": answer_format,
        "theta_before": round(theta, 2),
    }


def _mirror_spec(cmap: dict, prof: ProficiencyModel, seed: dict) -> dict:
    """First item of a seeded session: a close parallel of the user's question."""
    by_id = {c["id"]: c for c in cmap["concepts"]}
    primary = by_id.get(seed["primary_concept_id"]) or core_concepts(cmap)[-1]
    others = [by_id[c] for c in seed["question_concept_ids"] if c in by_id and c != primary["id"]]
    structure = seed["structure"]
    return {
        "mode": "mirror",
        "rationale": "Starting with a problem built like your question, to see how it goes "
                     "before adapting.",
        "primary": _public(primary),
        "secondary": [_public(c) for c in others[:1]],
        "difficulty": clamp_difficulty(seed["difficulty"]),
        "dimension": "multi_concept" if others else "direct_concept",
        "question_type": structure["problem_type"],
        "perspective": "application" if structure["problem_type"] == "word" else "procedural",
        "answer_format": structure["answer_format"],
        "theta_before": round(prof.theta_for(primary["id"]), 2),
    }
