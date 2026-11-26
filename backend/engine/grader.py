"""Grading + error diagnosis.

Multiple choice: the chosen distractor's diagnostic tells us the misconception and the
concept it implicates. Short answer: local equivalence check first; otherwise the LLM
judges correctness and diagnoses the likely misconception and implicated concept.
"""

import re
from fractions import Fraction

from backend.llm import LLMProvider

LETTERS = "ABCDEF"


def _normalize(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("−", "-").replace("×", "*").replace("÷", "/").replace("$", "")
    s = re.sub(r"\s+", "", s).rstrip(".")
    if re.match(r"^[a-z]=", s):
        s = s[2:]
    return s


def _to_number(s: str) -> Fraction | None:
    s = s.replace(",", "")
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            val = Fraction(Fraction(num), Fraction(den))
        else:
            val = Fraction(s)
    except (ValueError, ZeroDivisionError):
        return None
    return val / 100 if pct else val


def local_match(answer: str, keys: list[str]) -> bool:
    a = _normalize(answer)
    if not a:
        return False
    a_num = _to_number(a)
    for key in keys:
        k = _normalize(key)
        if a == k:
            return True
        k_num = _to_number(k)
        if a_num is not None and k_num is not None and abs(float(a_num) - float(k_num)) < 1e-9:
            return True
    return False


def judge_schema(concept_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "is_correct": {"type": "boolean"},
            "misconception": {"type": "string"},
            "error_concept": {"type": "string", "enum": [*concept_ids, ""]},
            "note": {"type": "string"},
        },
        "required": ["is_correct", "misconception", "error_concept", "note"],
        "additionalProperties": False,
    }


async def grade(llm: LLMProvider, spec: dict, item: dict, answer: str) -> dict:
    """Returns {is_correct, misconception, error_concept, note}."""
    answer = (answer or "").strip()
    result = {"is_correct": False, "misconception": "", "error_concept": "", "note": ""}
    if not answer:
        result["note"] = "No answer submitted."
        return result

    if item["answer_format"] == "multiple_choice":
        letter = answer.upper()[:1]
        if letter == item["correct_answer"]:
            return {**result, "is_correct": True, "note": "Correct choice."}
        idx = LETTERS.find(letter)
        diag = item["choice_diagnostics"][idx] if 0 <= idx < len(item["choice_diagnostics"]) else {}
        return {**result,
                "misconception": diag.get("misconception", ""),
                "error_concept": diag.get("concept_id", ""),
                "note": "That choice isn't right."}

    if local_match(answer, [item["correct_answer"], *item["accepted_answers"]]):
        return {**result, "is_correct": True, "note": "Matches the answer key."}

    concepts = [spec["primary"], *spec["secondary"]]
    concept_lines = "\n".join(f"- {c['id']}: {c['name']}" for c in concepts)
    prompt = f"""Decide whether the student's answer is mathematically equivalent to the answer key
and satisfies what the question asked for. Accept equivalent forms and reasonable rounding the
question allows. Reject answers that are wrong, incomplete, or in an explicitly prohibited form.

If it is wrong, diagnose the most likely misconception or error that produces exactly this
answer (be specific, e.g. "added numerators and denominators separately"), and which concept
id it reveals a gap in. If it looks like a slip (arithmetic typo) say so. If correct, leave
misconception and error_concept empty.

Question: {item['question_text']}
Required form: {item['answer_hint']}
Answer key: {item['correct_answer']} (also accepted: {', '.join(item['accepted_answers']) or 'none'})
Worked solution: {' | '.join(item['solution_steps'])}
Concepts:
{concept_lines}
Student answer: {answer}

"note": one short, encouraging sentence addressed to the student.
The student may get another try, so neither "note" nor "misconception" may state or hint at the
correct answer or its value; describe only the mistaken reasoning."""
    verdict = await llm.generate_json(
        "You are a precise, fair math grader and diagnostician.", prompt,
        judge_schema([c["id"] for c in concepts]), effort="low", max_tokens=4000,
    )
    if verdict["is_correct"]:
        verdict["misconception"] = verdict["error_concept"] = ""
    return verdict
