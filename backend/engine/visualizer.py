"""Pictorial explanations: the LLM draws a short sequence of labeled SVG diagrams that walk
the student through the solution (fraction bars, number lines, area models, tape diagrams,
arrays, shapes, graphs...).

LLM-written SVG is untrusted. It is sanitized here (allow-listed elements and attributes,
no scripts, event handlers, external references or entities), and the client additionally
renders it inside <img>, where SVG cannot run scripts or fetch resources.
"""

import re
import xml.etree.ElementTree as ET

from backend.llm import LLMProvider

SVG_NS = "http://www.w3.org/2000/svg"
MAX_SVG_CHARS = 60_000

ALLOWED_TAGS = {
    "svg", "g", "defs", "title", "desc", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "path", "text", "tspan", "marker", "pattern", "clipPath", "linearGradient",
    "radialGradient", "stop", "use",
}
ALLOWED_ATTRS = {
    "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "d", "points", "fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity", "opacity", "transform",
    "font-size", "font-family", "font-weight", "font-style", "text-anchor",
    "dominant-baseline", "dx", "dy", "id", "class", "marker-start", "marker-mid", "marker-end",
    "markerWidth", "markerHeight", "refX", "refY", "orient", "markerUnits",
    "patternUnits", "patternTransform", "clip-path", "offset", "stop-color", "stop-opacity",
    "gradientUnits", "preserveAspectRatio", "href", "role", "aria-label", "letter-spacing",
}
_EXTERNAL_URL = re.compile(r"url\(\s*['\"]?\s*(?!#)", re.I)


class SvgRejected(ValueError):
    pass


def sanitize_svg(svg: str) -> str:
    """Return a safe, self-contained SVG string or raise SvgRejected."""
    svg = svg.strip()
    if not svg or len(svg) > MAX_SVG_CHARS:
        raise SvgRejected("empty or too large")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", svg, re.I):
        raise SvgRejected("doctype/entities are not allowed")
    svg = re.sub(r"^<\?xml[^>]*\?>", "", svg).strip()
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        raise SvgRejected(f"invalid XML: {e}") from e
    if _local(root.tag) != "svg":
        raise SvgRejected("root element must be <svg>")

    def clean(el: ET.Element) -> None:
        for child in list(el):
            if _local(child.tag) not in ALLOWED_TAGS:
                el.remove(child)
            else:
                clean(child)
        for attr in list(el.attrib):
            name = _local(attr)
            value = el.attrib[attr]
            keep = name in ALLOWED_ATTRS and not name.lower().startswith("on")
            if name == "href" and not value.startswith("#"):
                keep = False  # only internal references (markers, patterns, <use>)
            if keep and _EXTERNAL_URL.search(value):
                keep = False
            if not keep:
                del el.attrib[attr]
            elif attr != name:  # normalise xlink:href etc. to plain attribute names
                del el.attrib[attr]
                el.attrib[name] = value
        el.tag = f"{{{SVG_NS}}}{_local(el.tag)}"

    clean(root)
    if "viewBox" not in root.attrib:
        w, h = root.attrib.get("width", "480"), root.attrib.get("height", "260")
        root.attrib["viewBox"] = f"0 0 {_num(w, 480)} {_num(h, 260)}"
    root.attrib.pop("width", None)
    root.attrib.pop("height", None)
    ET.register_namespace("", SVG_NS)
    return ET.tostring(root, encoding="unicode")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _num(v: str, default: int) -> int:
    m = re.match(r"\s*([\d.]+)", v)
    return int(float(m.group(1))) if m else default


VISUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "intro": {"type": "string"},
        "panels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "svg": {"type": "string"},
                    "caption": {"type": "string"},
                    "alt": {"type": "string"},
                },
                "required": ["title", "svg", "caption", "alt"],
                "additionalProperties": False,
            },
        },
        "key_idea": {"type": "string"},
    },
    "required": ["intro", "panels", "key_idea"],
    "additionalProperties": False,
}

SYSTEM = """\
You are a math teacher who explains with pictures. You draw clear, simple SVG diagrams that
make a concept visible to a K-12 student: fraction bars and strips, number lines, area
models and grids, tape/bar diagrams for word problems, arrays and equal groups, place-value
blocks, balance scales for equations, labeled geometric figures, coordinate graphs, tables.

Drawing rules:
- Each panel's "svg" is ONE complete, standalone <svg> element with
  xmlns="http://www.w3.org/2000/svg" and viewBox="0 0 480 260". No width/height attributes.
- Only basic shapes: rect, circle, ellipse, line, polyline, polygon, path, text, tspan, g,
  defs, marker. No scripts, no <style>, no foreignObject, no images, no external links,
  no CSS classes; use presentation attributes (fill, stroke, font-size, ...).
- Draw to scale wherever quantities are compared (e.g. 2/3 of a bar really is 2/3 of it).
- Every number in the drawings must match the problem and the worked solution exactly.
- Label directly: text at font-size 14-18, font-family "Helvetica, Arial, sans-serif",
  dark ink #1f2330 for text. Keep all text inside the viewBox with comfortable margins.
- Colors: #3b5bdb (main quantity), #f08c00 (second quantity / what changes),
  #2b8a3e (result / correct), #c92a2a (only to mark an error), light fills like #dbe4ff,
  #ffe8cc, #d3f9d8. Never rely on color alone: also label or pattern it.
- Keep it uncluttered: one idea per panel, generous white space.

Structure: 2 to 4 panels that build on each other (set up -> key step -> result). If the
student answered incorrectly and a misconception is given, add a final panel that shows
visually why that answer cannot be right. Captions are 1-2 short sentences for the student
(plain text, no LaTeX). "alt" describes the picture for screen readers. "key_idea" is the
one-sentence takeaway."""


async def explain_visually(llm: LLMProvider, topic: str, grade: str, spec: dict, item: dict,
                           response: dict) -> dict:
    status = "correct" if response["is_correct"] else "incorrect"
    choices = "\n".join(f"  {'ABCDEF'[i]}. {c}" for i, c in enumerate(item["choices"])) or "  (short answer)"
    prompt = f"""Grade {grade} student, topic: {topic}.
Concept focus: {spec['primary']['name']}{(' + ' + ', '.join(c['name'] for c in spec['secondary'])) if spec['secondary'] else ''}

Problem:
{item['question_text']}
Choices:
{choices}
Correct answer: {item['correct_answer']}
Worked solution:
{chr(10).join(f'  {i + 1}. {s}' for i, s in enumerate(item['solution_steps']))}

The student answered: {response['answer'] or '(blank)'} ({status})
Misconception detected: {response.get('misconception') or 'none'}

Create the pictorial explanation."""
    raw = await llm.generate_json(SYSTEM, prompt, VISUAL_SCHEMA, effort="medium", max_tokens=32000)
    panels = []
    for p in raw["panels"][:5]:
        try:
            panels.append({**p, "svg": sanitize_svg(p["svg"])})
        except SvgRejected:
            continue
    if not panels:
        raise ValueError("no usable diagrams were produced")
    return {"intro": raw["intro"], "panels": panels, "key_idea": raw["key_idea"],
            "llm": llm.describe()}
