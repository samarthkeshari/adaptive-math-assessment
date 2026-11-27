"""Seed questions: analyze a question the user wrote or uploaded, so a whole adaptive session
can be built around its concepts and structure.

The analysis (transcription, concepts with prerequisites, structure, difficulty) is shown to
the user for confirmation, sent back when the session starts, validated, and stored with the
session as its "seed". The uploaded file itself is not stored.
"""

import base64
import hashlib

from backend.llm import LLMProvider
from backend.llm.base import validate

from .concept_map import normalize_concept_map, slugify
from .proficiency import clamp_difficulty

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
VISION_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
TEXT_TYPES = {"text/plain"}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "readable": {"type": "boolean"},
        "problem": {"type": "string"},
        "question_text": {"type": "string"},
        "topic": {"type": "string"},
        "suggested_grade": {"type": "string"},
        "topic_summary": {"type": "string"},
        "primary_concept": {"type": "string"},
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "description": {"type": "string"},
                    "prerequisites": {"type": "array", "items": {"type": "string"}},
                    "in_question": {"type": "boolean"},
                },
                "required": ["name", "area", "description", "prerequisites", "in_question"],
                "additionalProperties": False,
            },
        },
        "connected_areas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "connection": {"type": "string"},
                },
                "required": ["name", "area", "connection"],
                "additionalProperties": False,
            },
        },
        "structure": {
            "type": "object",
            "properties": {
                "problem_type": {"type": "string", "enum": ["word", "direct"]},
                "answer_format": {"type": "string", "enum": ["multiple_choice", "short_answer"]},
                "steps": {"type": "integer"},
                "representation": {"type": "string"},
                "context": {"type": "string"},
                "pattern": {"type": "string"},
                "solution_strategy": {"type": "string"},
            },
            "required": ["problem_type", "answer_format", "steps", "representation", "context",
                         "pattern", "solution_strategy"],
            "additionalProperties": False,
        },
        "difficulty": {"type": "integer"},
        "answer": {"type": "string"},
        "solution_steps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["readable", "problem", "question_text", "topic", "suggested_grade", "topic_summary",
                 "primary_concept", "concepts", "connected_areas", "structure", "difficulty",
                 "answer", "solution_steps"],
    "additionalProperties": False,
}

SYSTEM = """\
You are a K-12 mathematics assessment designer. You are given ONE math question (typed, or in an
attached image or PDF). Analyze it so that an adaptive assessment can test the same concepts
with problems of the same structure.

- readable: false if there's no legible math question (then explain in "problem" and fill the
  other fields minimally); otherwise true and "problem" is "".
- question_text: the full question transcribed exactly, in plain text with Unicode math
  (×, ÷, −, ², √, π, ≤, ≥) and fractions as a/b; describe any figure or table in words. If several
  questions are present, use the first complete one.
- topic: a short name for what it practises (e.g. "Adding fractions with unlike denominators").
- suggested_grade: the grade it best fits: "K" or "1".."12".
- concepts: 4-8 fundamental concepts, ordered from most foundational to most advanced. Include
  every concept the question itself uses (in_question = true) and the key prerequisites a student
  needs (in_question = false). prerequisites lists EXACT names from this list.
- primary_concept: the EXACT name of the main concept the question assesses.
- connected_areas: 2-4 concepts from other strands of math that combine naturally with it.
- structure: problem_type (word = real-world context; direct = math stated directly);
  answer_format (multiple_choice if options are given, else short_answer); steps (number of
  solution steps); representation (e.g. "none", "table", "number line", "bar model", "diagram
  described in words", "graph"); context (the kind of scenario, or "none"); pattern (what is
  given and what is asked, as a reusable template); solution_strategy.
- difficulty: 1-10 relative to the stated grade (1-2 below grade, 3-4 routine, 5-6 non-routine,
  7-8 above grade, 9-10 challenge).
- answer and solution_steps: solve it and double-check."""


class UploadError(Exception):
    """A problem with what the user supplied (shown to them as-is). Deliberately not a
    ValueError, which the agent treats as an unusable model response."""


def prepare_attachment(attachment: dict | None) -> tuple[list[dict], str]:
    """Validate an upload {name, media_type, data(base64)}. Returns (vision attachments, extra text)."""
    if not attachment:
        return [], ""
    media_type = (attachment.get("media_type") or "").lower()
    try:
        raw = base64.b64decode(attachment.get("data") or "", validate=True)
    except (ValueError, TypeError) as e:
        raise UploadError("The uploaded file couldn't be read.") from e
    if not raw:
        raise UploadError("The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise UploadError("The uploaded file is larger than 10 MB.")
    if media_type in TEXT_TYPES:
        try:
            return [], raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UploadError("Text files must be UTF-8.") from e
    if media_type not in VISION_TYPES:
        raise UploadError("Upload an image (PNG, JPEG, GIF, WebP), a PDF, or a .txt file.")
    return [{"media_type": media_type, "data": attachment["data"]}], ""


async def analyze_question(llm: LLMProvider, grade: str, question_text: str,
                           attachment: dict | None) -> dict:
    attachments, file_text = prepare_attachment(attachment)
    typed = "\n\n".join(t for t in (question_text.strip(), file_text.strip()) if t)
    if not typed and not attachments:
        raise UploadError("Type a question or upload a file.")
    prompt = (f"Student grade: {grade}\n\n"
              + (f"Question (typed):\n{typed}\n\n" if typed else "")
              + ("The question is in the attached file.\n" if attachments else "")
              + "Analyze this question.")
    a = await llm.generate_json(SYSTEM, prompt, ANALYSIS_SCHEMA, effort="medium", max_tokens=16000,
                                attachments=attachments or None)
    if not a["readable"]:
        raise UploadError(a["problem"] or "No readable math question was found.")
    if not a["concepts"]:
        raise ValueError("no concepts were identified")
    names = {c["name"].strip().lower(): c["name"] for c in a["concepts"]}
    if a["primary_concept"].strip().lower() not in names:
        a["primary_concept"] = next((c["name"] for c in a["concepts"] if c["in_question"]),
                                    a["concepts"][-1]["name"])
    a["difficulty"] = clamp_difficulty(a["difficulty"])
    a["structure"]["steps"] = max(1, min(12, int(a["structure"]["steps"])))
    a["source"] = {"kind": ("pdf" if attachments and attachments[0]["media_type"] == "application/pdf"
                            else "image" if attachments else "file" if file_text else "text"),
                   "file_name": (attachment or {}).get("name", "")}
    return a


def check_seed(seed: dict) -> dict:
    """Validate an analysis sent back by the client when the session starts."""
    errors = validate({k: v for k, v in seed.items() if k != "source"}, ANALYSIS_SCHEMA)
    if errors:
        raise ValueError("invalid question analysis: " + "; ".join(errors[:3]))
    if not seed["readable"] or not seed["question_text"].strip() or not seed["concepts"]:
        raise ValueError("the question analysis is incomplete")
    return seed


def seed_concept_map(seed: dict, grade: str, llm_info: dict) -> dict:
    """Concept map for a seeded session, built from the analysis (no extra LLM call)."""
    digest = hashlib.sha1(f"{grade}|{seed['question_text']}".encode()).hexdigest()[:12]
    cmap = normalize_concept_map(seed, seed["topic"], grade, f"seed-{digest}", llm_info)
    in_question = {slugify(c["name"]) for c in seed["concepts"] if c["in_question"]}
    cmap["seed"] = {"primary_concept_id": slugify(seed["primary_concept"]),
                    "question_concept_ids": sorted(in_question)}
    return cmap


def seed_summary(seed: dict, cmap: dict) -> dict:
    """What the session keeps about its seed question (no file data)."""
    return {
        "question_text": seed["question_text"],
        "topic": seed["topic"],
        "structure": seed["structure"],
        "difficulty": seed["difficulty"],
        "answer": seed["answer"],
        "solution_steps": seed["solution_steps"],
        "primary_concept_id": cmap["seed"]["primary_concept_id"],
        "question_concept_ids": cmap["seed"]["question_concept_ids"],
        "source": seed.get("source", {"kind": "text", "file_name": ""}),
    }
