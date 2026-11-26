"""Concept map: the fundamental concepts behind a topic at a grade, with prerequisites.

Built once per (topic, grade) by the LLM and cached in data/concept_maps.jsonl, so concept
ids stay stable across a student's tests and the performance memory can accumulate.
"""

import re
import time

from backend.llm import LLMProvider

CONCEPT_MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "topic_summary": {"type": "string"},
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string"},
                    "description": {"type": "string"},
                    "prerequisites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "area", "description", "prerequisites"],
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
    },
    "required": ["topic_summary", "concepts", "connected_areas"],
    "additionalProperties": False,
}

SYSTEM = """\
You are a K-12 mathematics curriculum expert. You decompose a topic into the fundamental
concepts a student must understand, the way a diagnostic assessment designer would.
Plain text only."""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "concept"


def map_key(topic: str, grade: str) -> str:
    return f"{slugify(topic)}|grade-{slugify(grade)}"


async def build_concept_map(llm: LLMProvider, topic: str, grade: str) -> dict:
    prompt = f"""Topic: {topic}
Student grade: {grade}

1. concepts: list 6-10 fundamental concepts that make up this topic for a grade {grade}
   student, ordered from most foundational to most advanced. Each must be a distinct,
   testable idea (not a vague theme). "area" is its strand, e.g. "Number & Operations",
   "Algebra", "Geometry", "Measurement", "Statistics & Probability", "Ratios & Proportions".
   "prerequisites" lists the EXACT names of other concepts in this list that must be
   understood first (empty for the most basic ones). Include 1-2 grade-appropriate
   foundational concepts from earlier grades if the topic depends on them.
2. connected_areas: 3-5 concepts from DIFFERENT strands of math that naturally combine
   with this topic in multi-concept problems (e.g. fractions + area of rectangles), with
   the connection explained."""
    raw = await llm.generate_json(SYSTEM, prompt, CONCEPT_MAP_SCHEMA, effort="medium", max_tokens=16000)

    concepts, by_name = [], {}
    for c in raw["concepts"]:
        cid = slugify(c["name"])
        if cid in by_name.values():
            continue
        by_name[c["name"].strip().lower()] = cid
        concepts.append({"id": cid, "name": c["name"].strip(), "area": c["area"].strip(),
                         "description": c["description"].strip(), "prerequisites": c["prerequisites"],
                         "external": False})
    # Resolve prerequisite names to ids; drop anything that doesn't match a concept.
    for c in concepts:
        c["prerequisites"] = [by_name[p.strip().lower()] for p in c["prerequisites"]
                              if p.strip().lower() in by_name and by_name[p.strip().lower()] != c["id"]]
    if not concepts:
        raise ValueError("Concept map came back empty")

    seen = {c["id"] for c in concepts}
    for a in raw["connected_areas"]:
        cid = slugify(a["name"])
        if cid in seen:
            continue
        seen.add(cid)
        concepts.append({"id": cid, "name": a["name"].strip(), "area": a["area"].strip(),
                         "description": a["connection"].strip(), "prerequisites": [],
                         "external": True})

    return {
        "key": map_key(topic, grade),
        "topic": topic,
        "grade": grade,
        "summary": raw["topic_summary"],
        "concepts": concepts,
        "created_at": time.time(),
        "llm": llm.describe(),
    }


def core_concepts(cmap: dict) -> list[dict]:
    return [c for c in cmap["concepts"] if not c["external"]]


def concept_index(cmap: dict) -> dict[str, dict]:
    return {c["id"]: c for c in cmap["concepts"]}
