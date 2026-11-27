"""LLM item writer: turns a selector spec into a concrete, validated math problem."""

from backend.llm import LLMProvider

from .selector import PERSPECTIVES

DIFFICULTY_SCALE = """\
Difficulty scale (relative to the student's grade level):
 1-2  : below grade level warm-up; single step, friendly numbers
 3-4  : on grade level, routine; 1-2 steps
 5-6  : on grade level, non-routine; 2-3 steps or a small twist
 7-8  : above grade level; multi-step reasoning, careful setup required
 9-10 : challenge / competition style; deep insight or several concepts chained"""

SYSTEM = """\
You are an expert K-12 math assessment author writing items for a diagnostic adaptive test.
Every item must be mathematically correct, unambiguous, age-appropriate for the stated
grade, and have exactly one correct answer. Solve the problem yourself before finalizing
the answer key, and double-check it.

Formatting: plain text only, no LaTeX or Markdown. Use Unicode math where helpful
(×, ÷, −, ², ³, √, π, ≤, ≥, ≠, °) and write fractions as a/b. If a figure is needed,
describe it fully in words.

Field rules:
- question_text: the full problem statement shown to the student.
- choices: for multiple_choice, exactly 4 options WITHOUT letter prefixes; every wrong option
  must be the result of a specific, common misconception. For short_answer, an empty list.
- choice_diagnostics: for multiple_choice, one entry per choice in the same order: the
  misconception that leads to that choice and the concept_id it reveals a gap in (use
  "" for both on the correct choice). Describe only the mistaken reasoning, never the correct
  answer (it may be shown as a hint before a re-attempt). For short_answer, an empty list.
- correct_answer: for multiple_choice, the single letter A, B, C or D; for short_answer, the
  simplest canonical answer (a number, expression, or short word/phrase).
- accepted_answers: other equivalent forms a student might type (e.g. "0.5", "1/2", "50%");
  empty list for multiple_choice.
- answer_hint: what form to answer in (e.g. "Enter a whole number"). Never reveal the answer.
- solution_steps: a concise worked solution, one step per entry.
- concepts: EVERY fundamental concept the student needs, each with its math area and its
  role in solving this problem.
- time_limit_seconds: a fair time limit for a student at this grade to read and solve this
  item (typically 45-300 seconds; longer for multi-step word problems)."""


def item_schema(concept_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "question_text": {"type": "string"},
            "choices": {"type": "array", "items": {"type": "string"}},
            "choice_diagnostics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "misconception": {"type": "string"},
                        "concept_id": {"type": "string", "enum": [*concept_ids, ""]},
                    },
                    "required": ["misconception", "concept_id"],
                    "additionalProperties": False,
                },
            },
            "correct_answer": {"type": "string"},
            "accepted_answers": {"type": "array", "items": {"type": "string"}},
            "answer_hint": {"type": "string"},
            "solution_steps": {"type": "array", "items": {"type": "string"}},
            "concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "area": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "area", "role"],
                    "additionalProperties": False,
                },
            },
            "time_limit_seconds": {"type": "integer"},
        },
        "required": ["question_text", "choices", "choice_diagnostics", "correct_answer",
                     "accepted_answers", "answer_hint", "solution_steps", "concepts",
                     "time_limit_seconds"],
        "additionalProperties": False,
    }


async def write_item(llm: LLMProvider, topic: str, grade: str, spec: dict, items: list[dict],
                     known_misconceptions: list[str], seed: dict | None = None) -> dict:
    p, secondary = spec["primary"], spec["secondary"]
    if spec["dimension"] == "direct_concept":
        dim_text = (f"DIRECT CONCEPT: test '{p['name']}' on its own, in isolation, so a wrong "
                    "answer clearly points to this concept.")
    else:
        others = "; ".join(f"'{s['name']}' ({s['area']}): {s['description']}" for s in secondary)
        dim_text = (f"MULTI-CONCEPT: the problem must genuinely require BOTH '{p['name']}' and {others}. "
                    "The student should have to connect the ideas, not solve two separate mini-problems.")
    type_text = ("WORD PROBLEM: set the math inside a short, concrete real-world scenario."
                 if spec["question_type"] == "word"
                 else "DIRECT PROBLEM: state the math directly with no story context.")
    concept_lines = "\n".join(f"- {c['id']}: {c['name']} ({c['area']}): {c['description']}"
                              for c in [p, *secondary])
    prior = "\n".join(
        f"- Q{i + 1} [{it['spec']['primary']['name']}, difficulty {it['spec']['difficulty']}]: "
        f"{it['item']['question_text'][:140]}"
        for i, it in enumerate(items)
    ) or "(none yet)"
    misc = "\n".join(f"- {m}" for m in known_misconceptions[:6]) or "(none recorded)"

    prompt = f"""Write question {len(items) + 1} of a diagnostic adaptive math test.

Topic: {topic}
Student grade: {grade}
Target difficulty: {spec['difficulty']} / 10

{DIFFICULTY_SCALE}

Concepts for this item (use these ids in choice_diagnostics):
{concept_lines}

Item design (required):
- {type_text}
- {dim_text}
- Perspective: {spec['perspective'].upper()}: {PERSPECTIVES[spec['perspective']]}
- Answer format: {spec['answer_format']}
- Purpose: {spec['rationale']}

Misconceptions this student has shown on '{p['name']}' (design the item, and any distractors,
so it reveals whether these are still present):
{misc}

Questions already asked in this test (do not repeat them or their numbers/contexts):
{prior}"""
    if seed:
        st = seed["structure"]
        prompt += f"""

MODEL QUESTION (the user's own question; this whole session is built around it):
{seed['question_text']}

Mirror its STRUCTURE while following the item design above: {st['problem_type']} problem,
{st['answer_format'].replace('_', ' ')}, about {st['steps']} solution step(s), representation:
{st['representation']}; context style: {st['context']}; pattern: {st['pattern']}.
Use new numbers and a new specific context so it is a fresh problem, never a copy."""
        if spec["mode"] == "mirror":
            prompt += """
This is the opening item: write a close parallel of the model question that tests the same
concepts at the same difficulty, changing only the numbers and the specific context."""

    ids = [p["id"], *[s["id"] for s in secondary]]
    item = await llm.generate_json(SYSTEM, prompt, item_schema(ids), effort="medium", max_tokens=16000)

    if spec["answer_format"] == "multiple_choice":
        if len(item["choices"]) < 2:
            raise ValueError("multiple-choice item had no choices")
        item["correct_answer"] = item["correct_answer"].strip().upper()[:1]
        if item["correct_answer"] not in "ABCDEF"[:len(item["choices"])]:
            raise ValueError("multiple-choice answer key is not a valid letter")
        diags = item["choice_diagnostics"][:len(item["choices"])]
        diags += [{"misconception": "", "concept_id": ""}] * (len(item["choices"]) - len(diags))
        item["choice_diagnostics"] = diags
    else:
        item["choices"], item["choice_diagnostics"] = [], []
    item["time_limit_seconds"] = max(30, min(600, int(item["time_limit_seconds"])))
    item["answer_format"] = spec["answer_format"]
    return item
