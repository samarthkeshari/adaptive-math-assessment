"""Offline tests: the whole agent runs against a stub LLM, so no API key is needed.

Run from the project root:  python3 -m backend.tests.test_offline
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # also allow: python3 backend/tests/test_offline.py

from fastapi.testclient import TestClient

from backend import main as server
from backend.engine import selector
from backend.engine.agent import AssessmentAgent
from backend.engine.proficiency import ProficiencyModel, response_weights
from backend.llm import LLMError, LLMProvider, get_provider
from backend.llm.base import extract_json, validate
from backend.storage.memory import Memory

CONCEPT_MAP = {
    "topic_summary": "Fractions for grade 5.",
    "concepts": [
        {"name": "Fraction meaning", "area": "Number & Operations", "description": "parts of a whole", "prerequisites": []},
        {"name": "Equivalent fractions", "area": "Number & Operations", "description": "same value", "prerequisites": ["Fraction meaning"]},
        {"name": "Adding unlike fractions", "area": "Number & Operations", "description": "common denominators", "prerequisites": ["Equivalent fractions"]},
    ],
    "connected_areas": [
        {"name": "Area of rectangles", "area": "Geometry", "connection": "fractional side lengths"},
    ],
}


class StubLLM(LLMProvider):
    name = "stub"

    def __init__(self):
        super().__init__("stub-model")
        self.calls = []

    async def _complete_json(self, system, prompt, schema, *, effort, max_tokens):
        props = schema["properties"]
        self.calls.append(next(iter(props)))
        if "connected_areas" in props:
            out = CONCEPT_MAP
        elif "choice_diagnostics" in props:
            ids = props["choice_diagnostics"]["items"]["properties"]["concept_id"]["enum"]
            mc = "Answer format: multiple_choice" in prompt
            out = {
                "question_text": "What is 1/2 + 1/4?",
                "choices": ["3/4", "2/6", "1/6", "2/4"] if mc else [],
                "choice_diagnostics": ([{"misconception": "", "concept_id": ""}]
                                       + [{"misconception": "adds numerators and denominators", "concept_id": ids[0]}] * 3)
                                      if mc else [],
                "correct_answer": "A" if mc else "3/4",
                "accepted_answers": [] if mc else ["0.75"],
                "answer_hint": "Choose one" if mc else "Fraction in simplest form",
                "solution_steps": ["2/4 + 1/4 = 3/4"],
                "concepts": [{"name": "Equivalent fractions", "area": "Number & Operations", "role": "rewrite 1/2"}],
                "time_limit_seconds": 90,
            }
        elif "error_concept" in props:
            ids = props["error_concept"]["enum"]
            out = {"is_correct": False, "misconception": "adds numerators and denominators",
                   "error_concept": ids[0], "note": "Not quite."}
        elif "concept_notes" in props:
            ids = props["concept_notes"]["items"]["properties"]["concept_id"]["enum"]
            out = {"overall_summary": "Summary.", "misconception_insights": "", "timing_insights": "",
                   "concept_notes": [{"concept_id": i, "fundamental_explanation": "x", "observation": "y"} for i in ids],
                   "question_analysis": [], "strengths": [], "areas_to_improve": [], "recommended_next_steps": []}
        elif "panels" in props:
            good = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 260" onload="alert(1)">'
                    '<script>alert(1)</script><rect x="10" y="10" width="300" height="40" fill="#3b5bdb"/>'
                    '<a href="https://evil.example"><text x="10" y="90">evil</text></a>'
                    '<text x="10" y="120" font-size="16">3/4</text></svg>')
            out = {"intro": "Look at the bar.", "key_idea": "Same-size parts add up.",
                   "panels": [{"title": "Bar", "svg": good, "caption": "1/2 + 1/4", "alt": "a bar"},
                              {"title": "Broken", "svg": "<svg><rect", "caption": "x", "alt": "x"}]}
        elif "practice_plan" in props:
            out = {"overall_progress": "Growing.", "growth_highlights": [], "persistent_gaps": [],
                   "misconception_patterns": "", "learning_habits": "", "encouragement": "Keep going!",
                   "practice_plan": [{"focus": "Equivalent fractions", "why": "gap", "activity": "fraction strips"}]}
        else:
            raise AssertionError("unexpected schema")
        return "<think>reasoning</think>\n```json\n" + json.dumps(out) + "\n```"


def test_llm_layer():
    assert extract_json('<think>x</think>{"a": 1}') == {"a": 1}
    schema = {"type": "object", "properties": {"n": {"type": "integer"}, "m": {"type": "string", "enum": ["a"]}},
              "required": ["n", "m"]}
    assert validate({"n": 3, "m": "a"}, schema) == []
    assert validate({"n": "3", "m": "b"}, schema)
    assert validate({"m": "a"}, schema) == ["$: missing 'n'"]

    class Flaky(LLMProvider):
        name = "flaky"
        n = 0
        async def _complete_json(self, *a, **k):
            self.n += 1
            return '{"n": "bad", "m": "a"}' if self.n == 1 else '{"n": 1, "m": "a"}'
    f = Flaky("m")
    assert asyncio.run(f.generate_json("s", "p", schema)) == {"n": 1, "m": "a"} and f.n == 2

    env = dict(os.environ)
    try:
        os.environ.update(LLM_PROVIDER="openai")
        os.environ.pop("LLM_MODEL", None)
        try:
            get_provider()
            raise AssertionError("expected missing-model error")
        except LLMError:
            pass
        os.environ.update(LLM_PROVIDER="ollama", LLM_MODEL="qwen3")
        p = get_provider()
        assert p.describe() == {"provider": "ollama", "model": "qwen3"}
        assert str(p.client.base_url).startswith("http://localhost:11434")
        os.environ.update(LLM_PROVIDER="deepseek", LLM_MODEL="deepseek-chat", LLM_API_KEY="k")
        assert get_provider().json_mode == "object"
    finally:
        os.environ.clear()
        os.environ.update(env)


def test_anthropic_key_helper_and_fallback_retry():
    import anthropic, httpx
    from backend.llm.anthropic_provider import AnthropicProvider
    env = dict(os.environ)
    try:
        os.environ["ANTHROPIC_API_KEY_HELPER"] = "echo first-line; echo dummy-key-123"
        prov = AnthropicProvider("claude-opus-5-5")
        client = asyncio.run(prov._get_client())
        assert client.api_key == "dummy-key-123"
        assert client.default_headers["Authorization"] == "Bearer dummy-key-123"
        assert asyncio.run(prov._get_client()) is client, "key is cached within the TTL"

        calls = []
        class Msg:
            stop_reason = "end_turn"
            content = [type("B", (), {"type": "text", "text": '{"ok": true}'})()]
        async def fake_stream(client, kwargs, with_fallbacks):
            calls.append(with_fallbacks)
            if with_fallbacks:
                req = httpx.Request("POST", "https://gw/v1/messages")
                raise anthropic.BadRequestError("unknown beta", response=httpx.Response(400, request=req), body=None)
            return Msg()
        prov._stream = fake_stream
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        assert asyncio.run(prov.generate_json("s", "p", schema)) == {"ok": True}
        assert calls == [True, False] and prov.use_fallbacks is False

        os.environ["ANTHROPIC_API_KEY_HELPER"] = "exit 3"
        try:
            asyncio.run(AnthropicProvider("claude-opus-5-5")._get_client())
            raise AssertionError("expected helper failure")
        except LLMError:
            pass
    finally:
        os.environ.clear()
        os.environ.update(env)


def test_proficiency_and_selector():
    m = ProficiencyModel()
    m.apply({"a": 1.0}, 7, 1.0)
    assert m.get("a").theta > 5.5, "a hard item answered correctly should raise ability a lot"
    seed = m.theta_for("b")
    assert seed > 4.5, "an untested concept should borrow the student's overall ability"
    m.apply({"b": 1.0}, 2, 0.0)
    assert m.get("b").theta < seed - 2, "an easy item missed should lower ability a lot"
    m.apply({"c": 1.0}, 1, 1.0)
    m.get("c").theta = 3.0
    assert m.get("c").mastery == "developing", "no misses means not (yet) a gap"
    assert response_weights("p", ["s"], 0.0, "s") == {"p": 0.25, "s": 1.0}
    assert response_weights("p", ["s"], 1.0, "") == {"p": 1.0, "s": 0.5}

    from backend.engine.concept_map import slugify
    cmap = {"concepts": [
        {"id": "meaning", "name": "Meaning", "area": "N", "description": "", "prerequisites": [], "external": False},
        {"id": "equiv", "name": "Equiv", "area": "N", "description": "", "prerequisites": ["meaning"], "external": False},
        {"id": "add", "name": "Add", "area": "N", "description": "", "prerequisites": ["equiv"], "external": False},
        {"id": "area", "name": "Area", "area": "Geometry", "description": "", "prerequisites": [], "external": True},
    ]}
    assert slugify("Adding Unlike Fractions!") == "adding-unlike-fractions"
    prof = ProficiencyModel()
    items = []

    def take(outcome):
        spec = selector.select_next(cmap, prof, items, {})
        items.append({"spec": spec, "item": {}, "response": {}})
        weights = response_weights(spec["primary"]["id"], [s["id"] for s in spec["secondary"]], outcome, "")
        prof.apply(weights, spec["difficulty"], outcome)
        return spec

    s1 = take(1.0)
    assert s1["mode"] == "explore" and s1["primary"]["id"] == "meaning" and s1["dimension"] == "direct_concept"
    s2 = take(0.0)
    assert s2["mode"] == "explore" and s2["primary"]["id"] == "equiv"
    s3 = take(0.0)
    assert s3["mode"] == "probe" and s3["primary"]["id"] == "equiv", s3
    assert s3["difficulty"] < s2["difficulty"] and s3["dimension"] == "direct_concept"
    assert s3["answer_format"] == "multiple_choice"

    # A strong student gets stretched with multi-concept problems from another area.
    prof2, items2 = ProficiencyModel(), []
    for _ in range(6):
        spec = selector.select_next(cmap, prof2, items2, {})
        items2.append({"spec": spec, "item": {}, "response": {}})
        prof2.apply(response_weights(spec["primary"]["id"], [s["id"] for s in spec["secondary"]], 1.0, ""),
                    spec["difficulty"], 1.0)
    assert any(it["spec"]["mode"] == "stretch" for it in items2)
    multi = [it["spec"] for it in items2 if it["spec"]["dimension"] == "multi_concept"]
    assert multi and all(s["secondary"][0]["area"] == "Geometry" for s in multi)
    assert items2[-1]["spec"]["difficulty"] > items2[0]["spec"]["difficulty"]


def test_end_to_end():
    data_dir = Path(tempfile.mkdtemp())
    llm = StubLLM()
    server.app.state.agent = AssessmentAgent(llm, Memory(data_dir))
    with TestClient(server.app) as c:
        assert c.get("/api/config").json() == {"provider": "stub", "model": "stub-model"}
        tid = c.post("/api/tests", json={"student_name": "Maya", "topic": "Fractions",
                                         "grade": "5", "num_questions": 4}).json()["test_id"]
        for i in range(4):
            q = c.post(f"/api/tests/{tid}/next").json()["question"]
            assert "correct_answer" not in q and "choice_diagnostics" not in q
            assert c.post(f"/api/tests/{tid}/next").json()["question"]["idx"] == q["idx"]  # resume, no duplicate
            correct = i % 2 == 0
            ans = ("A" if correct else "B") if q["answer_format"] == "multiple_choice" else ("0.75" if correct else "5/6")
            fb = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": ans}).json()
            assert fb["is_correct"] == correct, fb
            if not correct:
                assert fb["misconception"] == "adds numerators and denominators"
            assert fb["learned"] and fb["why_this_question"]
            assert c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "A"}).status_code == 409
        assert c.post(f"/api/tests/{tid}/next").json()["done"] is True
        d = c.post(f"/api/tests/{tid}/finish").json()
        a = d["assessment"]
        assert d["status"] == "completed" and a["score"] == 2 and a["answered"] == 4
        assert {x["name"] for x in a["concepts"]} >= {"Fraction meaning", "Equivalent fractions"}
        assert d["narrative"]["overall_summary"] == "Summary."

        hist = c.get("/api/students/MAYA/tests").json()["tests"]
        assert len(hist) == 1 and hist[0]["score"] == 2
        prog = c.get("/api/students/maya/progress").json()
        assert prog["summary"]["sessions_completed"] == 1 and prog["summary"]["questions_answered"] == 4
        assert prog["concepts"] and len(prog["ability_trend"]) == 1 and prog["topics"][0]["completed"] == 1
        assert prog["misconceptions"][0]["misconception"] == "adds numerators and denominators"
        assert prog["coach_summary"] is None
        prog = c.post("/api/students/maya/progress/summary").json()
        assert prog["coach_summary"]["summary"]["encouragement"] == "Keep going!" and not prog["coach_summary_stale"]
        assert c.get("/api/students/nobody/progress").status_code == 404
        assert c.get("/").status_code == 200

        # Everything for a student lives in their own folder.
        folder = data_dir / "students" / "maya"
        assert json.loads((folder / "student.json").read_text())["name"] == "Maya"
        tests = [json.loads(l) for l in (folder / "tests.jsonl").read_text().splitlines()]
        assert len(tests) == 1 and len(tests[0]["items"]) == 4 and tests[0]["assessment"]["score"] == 2
        events = [json.loads(l) for l in (folder / "events.jsonl").read_text().splitlines()]
        assert [e["type"] for e in events][:2] == ["session_started", "item_served"]
        assert all(e["student_key"] == "maya" for e in events)
        assert len((folder / "progress_reports.jsonl").read_text().splitlines()) == 1
        assert len((data_dir / "concept_maps.jsonl").read_text().splitlines()) == 1

        # Second test: concept map is reused (no new LLM call) and memory seeds the priors.
        n_maps = llm.calls.count("topic_summary")
        tid2 = c.post("/api/tests", json={"student_name": "maya", "topic": "fractions",
                                          "grade": "5", "num_questions": 3}).json()["test_id"]
        assert llm.calls.count("topic_summary") == n_maps
        s2 = server.app.state.agent.memory.sessions[tid2]
        assert s2["prior"], "priors should come from the student's earlier test"
        q = c.post(f"/api/tests/{tid2}/next").json()["question"]

        # Leaving pauses the clock; continuing resumes the same question with time preserved.
        it = s2["items"][0]
        it["active_since"] -= 30  # pretend 30s were spent before leaving
        assert c.post(f"/api/tests/{tid2}/pause").json() == {"paused": True}
        assert it["active_since"] is None and 29 <= it["elapsed"] <= 32
        assert c.post(f"/api/tests/{tid2}/answer", json={"idx": 0, "answer": "A"}).status_code == 409
        resumed = c.post(f"/api/tests/{tid2}/next").json()["question"]
        assert resumed["idx"] == q["idx"] and 57 <= resumed["remaining_seconds"] <= 61
        assert it["active_since"] is not None

        # A second student gets a separate folder; the student list shows both.
        c.post("/api/tests", json={"student_name": "Leo Park", "topic": "Fractions", "grade": "5", "num_questions": 3})
        students = {x["key"]: x for x in c.get("/api/students").json()["students"]}
        assert students["maya"]["completed"] == 1 and students["maya"]["in_progress"] == 1
        assert students["leo-park"]["name"] == "Leo Park" and (data_dir / "students" / "leo-park" / "events.jsonl").exists()
        assert c.get("/api/students/leo%20park/tests").json()["tests"][0]["status"] == "in_progress"

    # Replaying the event log rebuilds identical state (including the in-progress test).
    original = server.app.state.agent.memory
    replayed = Memory(data_dir)
    assert replayed.sessions[tid]["proficiency"].snapshot() == original.sessions[tid]["proficiency"].snapshot()
    r2 = replayed.sessions[tid2]
    assert r2["status"] == "in_progress" and len(r2["items"]) == 1
    # (The 30s above was injected in memory only, so the replayed clock just shows the real pause.)
    assert r2["items"][0]["active_since"] is not None and r2["items"][0]["elapsed"] >= 0
    from backend.engine.progress import build_progress
    a1, a2 = build_progress(original, "maya"), build_progress(replayed, "maya")
    a1.pop("generated_at"), a2.pop("generated_at")
    assert a1 == a2
    path = " -> ".join(str(x) for x in a["difficulty_path"])
    print(f"difficulty path: {path}; final ability {a['overall_theta']}")


def test_topics_and_session_time_limit():
    from backend.engine import topics
    g5 = {t for strand in topics.predefined_for("5") for t in strand["topics"]}
    assert "Fractions" in g5 and "Quadratic equations" not in g5
    assert "Counting and cardinality" in {t for st in topics.predefined_for("K") for t in st["topics"]}
    assert topics.is_predefined("fractions") and not topics.is_predefined("Tessellations")

    data_dir = Path(tempfile.mkdtemp())
    server.app.state.agent = AssessmentAgent(StubLLM(), Memory(data_dir))
    with TestClient(server.app) as c:
        listing = c.get("/api/topics", params={"grade": "5"}).json()
        assert listing["custom"] == [] and any(st["strand"] == "Algebra" for st in listing["predefined"])
        assert len(c.get("/api/topics").json()["predefined"]) == len(topics.PREDEFINED)

        # A brand-new topic is saved and offered from then on; predefined ones are not duplicated.
        c.post("/api/tests", json={"student_name": "Ann", "topic": "  Tessellations  ", "grade": "5", "num_questions": 3})
        c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5", "num_questions": 3})
        c.post("/api/tests", json={"student_name": "Bo", "topic": "tessellations", "grade": "4", "num_questions": 3})
        assert c.get("/api/topics", params={"grade": "5"}).json()["custom"] == ["Tessellations"]
        assert len((data_dir / "custom_topics.jsonl").read_text().splitlines()) == 1

        # Session time limit: the session clock caps each question's timer...
        tid = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5",
                                         "num_questions": 5, "time_limit_minutes": 1}).json()["test_id"]
        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        assert q["session_time_limit_seconds"] == 60 and 58 <= q["session_remaining_seconds"] <= 60
        assert q["remaining_seconds"] <= 60 < q["time_limit_seconds"]
        hist = {t["id"]: t for t in c.get("/api/students/Ann/tests").json()["tests"]}
        assert hist[tid]["time_limit_minutes"] == 1

        # ...and when it runs out, the answer ends the session and the report says so.
        s = server.app.state.agent.memory.sessions[tid]
        s["items"][0]["active_since"] -= 70
        assert c.post(f"/api/tests/{tid}/next").json()["question"]["remaining_seconds"] == 0
        fb = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "A"}).json()
        assert fb["session_over"] and fb["is_last"] and fb["session_remaining_seconds"] == 0
        assert c.post(f"/api/tests/{tid}/next").json() == {"done": True, "ended_by_time": True}
        timing = c.post(f"/api/tests/{tid}/finish").json()["assessment"]["timing"]
        assert timing["ended_by_time"] and timing["session_limit_seconds"] == 60 and timing["planned_questions"] == 5

        # No limit: unchanged behaviour.
        tid2 = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5", "num_questions": 3}).json()["test_id"]
        q2 = c.post(f"/api/tests/{tid2}/next").json()["question"]
        assert q2["session_time_limit_seconds"] is None and q2["remaining_seconds"] == q2["time_limit_seconds"]

    replayed = Memory(data_dir)
    assert replayed.sessions[tid]["time_limit_minutes"] == 1 and replayed.sessions[tid2]["time_limit_minutes"] is None
    assert [t["topic"] for t in replayed.custom_topics] == ["Tessellations"]


def test_svg_sanitizer():
    from backend.engine.visualizer import SvgRejected, sanitize_svg
    dirty = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
             'width="400" height="200" onload="steal()">'
             '<script>steal()</script><style>*{}</style><foreignObject><div>x</div></foreignObject>'
             '<rect x="1" y="2" width="3" height="4" fill="url(https://evil/x)" onclick="steal()"/>'
             '<use xlink:href="https://evil/sprite.svg#a"/><use href="#ok"/>'
             '<image href="https://evil/pixel.png"/><circle cx="5" cy="5" r="4" fill="url(#grad)"/>'
             '<text x="5" y="20" font-size="14">1/2</text></svg>')
    clean = sanitize_svg(dirty)
    for bad in ("script", "steal", "style", "foreignObject", "evil", "image", "onload", "onclick"):
        assert bad not in clean, (bad, clean)
    root_tag = clean[:clean.index(">")]
    assert "width" not in root_tag and "height" not in root_tag, "root scales via viewBox only"
    assert 'viewBox="0 0 400 200"' in clean and 'href="#ok"' in clean and 'fill="url(#grad)"' in clean
    assert "1/2" in clean and "<rect" in clean
    for rejected in ('<!DOCTYPE svg [<!ENTITY a "aaaa">]><svg>&a;</svg>', "<svg><rect", "<div>not svg</div>", ""):
        try:
            sanitize_svg(rejected)
            raise AssertionError(f"should reject: {rejected}")
        except SvgRejected:
            pass

    data_dir = Path(tempfile.mkdtemp())
    llm = StubLLM()
    server.app.state.agent = AssessmentAgent(llm, Memory(data_dir))
    with TestClient(server.app) as c:
        tid = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5", "num_questions": 3}).json()["test_id"]
        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        assert c.post(f"/api/tests/{tid}/items/{q['idx']}/visual").status_code == 409  # not before answering
        c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "B"})
        v = c.post(f"/api/tests/{tid}/items/{q['idx']}/visual").json()
        assert len(v["panels"]) == 1, "the unparseable panel is dropped"
        svg = v["panels"][0]["svg"]
        assert "script" not in svg and "onload" not in svg and "evil" not in svg and "3/4" in svg
        n = llm.calls.count("intro")
        assert c.post(f"/api/tests/{tid}/items/{q['idx']}/visual").json() == v and llm.calls.count("intro") == n
        assert c.post(f"/api/tests/{tid}/items/9/visual").status_code == 404
        assert c.get(f"/api/tests/{tid}").json()["questions"][0]["visual"] == v
    assert Memory(data_dir).sessions[tid]["items"][0]["visual"] == v  # persisted in events.jsonl


def test_reattempts():
    data_dir = Path(tempfile.mkdtemp())
    server.app.state.agent = AssessmentAgent(StubLLM(), Memory(data_dir))
    with TestClient(server.app) as c:
        tid = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5",
                                         "num_questions": 4, "reattempts": 2}).json()["test_id"]
        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        assert q["answer_format"] == "multiple_choice" and q["attempts_allowed"] == 3 and q["attempts_used"] == 0

        # Wrong with tries left: no answer revealed, hint given, wrong choice remembered.
        r = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "B"}).json()
        assert r["retry"] and r["attempts_left"] == 2 and r["hint"] == "adds numerators and denominators"
        leak = json.dumps(r)
        assert "correct_answer" not in leak and "solution_steps" not in leak and "3/4" not in r["note"]
        assert r["question"]["previous_answers"] == ["B"] and r["question"]["attempts_used"] == 1

        # Continuing after a restart keeps the attempt state.
        replay = Memory(data_dir).sessions[tid]["items"][0]
        assert [a["answer"] for a in replay["attempts"]] == ["B"] and replay["response"] is None

        r = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "C"}).json()
        assert r["retry"] and r["attempts_left"] == 1
        r = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "A"}).json()
        assert not r["retry"] and r["is_correct"] and r["attempt_count"] == 3
        assert [a["answer"] for a in r["attempts"]] == ["B", "C", "A"] and r["correct_answer"] == "A"
        assert r["note"].startswith("Correct on attempt 3")
        s = server.app.state.agent.memory.sessions[tid]
        assert s["items"][0]["response"]["outcome"] == 0.5, "correct after retries = partial credit"
        pid = s["items"][0]["spec"]["primary"]["id"]
        assert s["proficiency"].get(pid).misconceptions["adds numerators and denominators"] == 2

        # Out of tries: the answer is revealed after the last wrong attempt.
        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        wrong = "B" if q["answer_format"] == "multiple_choice" else "5/6"
        assert c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": wrong}).json()["retry"]
        assert c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": wrong}).json()["retry"]
        r = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": wrong}).json()
        assert not r["retry"] and not r["is_correct"] and r["attempt_count"] == 3 and "correct_answer" in r

        # Time ran out: no further tries even if some are left.
        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        s["items"][q["idx"]]["active_since"] -= 1000
        r = c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "B"}).json()
        assert not r["retry"] and r["timed_out"]

        q = c.post(f"/api/tests/{tid}/next").json()["question"]
        c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": "A" if q["answer_format"] == "multiple_choice" else "0.75"})
        att = c.post(f"/api/tests/{tid}/finish").json()["assessment"]["attempts"]
        assert att["reattempts_allowed"] == 2 and att["correct_after_retry"] == 1 and att["first_try_correct"] == 1

        # Default (no re-attempts): a wrong answer is final immediately.
        tid0 = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5", "num_questions": 3}).json()["test_id"]
        q = c.post(f"/api/tests/{tid0}/next").json()["question"]
        assert q["attempts_allowed"] == 1
        r = c.post(f"/api/tests/{tid0}/answer", json={"idx": q["idx"], "answer": "B"}).json()
        assert not r["retry"] and not r["is_correct"]
        assert c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5",
                                          "num_questions": 3, "reattempts": 4}).status_code == 422

    replayed = Memory(data_dir)
    assert replayed.sessions[tid]["reattempts"] == 2
    assert replayed.sessions[tid]["proficiency"].snapshot() == server.app.state.agent.memory.sessions[tid]["proficiency"].snapshot()


def test_delete_session():
    data_dir = Path(tempfile.mkdtemp())
    server.app.state.agent = AssessmentAgent(StubLLM(), Memory(data_dir))
    mem = lambda: server.app.state.agent.memory

    def run(c, name, answers, finish=True):
        tid = c.post("/api/tests", json={"student_name": name, "topic": "Fractions", "grade": "5",
                                         "num_questions": max(3, len(answers))}).json()["test_id"]
        for ok in answers:
            q = c.post(f"/api/tests/{tid}/next").json()["question"]
            mc = q["answer_format"] == "multiple_choice"
            ans = ("A" if ok else "B") if mc else ("0.75" if ok else "5/6")
            c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": ans})
        if finish:
            c.post(f"/api/tests/{tid}/finish")
        return tid

    with TestClient(server.app) as c:
        a = run(c, "Ann", [False, False, False])          # a weak session...
        b = run(c, "Ann", [True, True, True])             # ...a strong one
        live = run(c, "Ann", [True], finish=False)        # and one in progress
        bo = run(c, "Bo", [True, False, True])
        c.post("/api/students/Ann/progress/summary")
        folder = data_dir / "students" / "ann"
        assert any(json.loads(l).get("session_id") == a for l in (folder / "events.jsonl").read_text().splitlines())

        r = c.delete(f"/api/tests/{a}")
        assert r.status_code == 200 and r.json()["deleted"]
        ids = {t["id"] for t in c.get("/api/students/Ann/tests").json()["tests"]}
        assert ids == {b, live}
        assert not any(json.loads(l).get("session_id") == a for l in (folder / "events.jsonl").read_text().splitlines())
        assert [json.loads(l)["test_id"] for l in (folder / "tests.jsonl").read_text().splitlines()] == [b]
        assert not list(folder.glob("*.tmp")), "atomic rewrite leaves no temp files"

        # Memory and reports are rebuilt without the deleted session's evidence.
        prog = c.get("/api/students/Ann/progress").json()
        assert prog["summary"]["sessions_completed"] == 1 and prog["summary"]["questions_answered"] == 4
        assert prog["coach_summary_stale"], "summary written before the delete is now stale"
        attempts = sum(est.attempts for est in mem().students["ann"]["concepts"].values())
        assert attempts == sum(len(it["response"]["weights"]) for sid in (b, live)
                               for it in mem().sessions[sid]["items"] if it["response"])
        assert a not in mem().sessions and len(mem().sessions[bo]["items"]) == 3  # Bo untouched

        # Errors and races.
        assert c.delete(f"/api/tests/{a}").status_code == 404
        assert c.get(f"/api/tests/{a}").status_code == 404
        asyncio.run(mem().record({"type": "visual_added", "session_id": a, "idx": 0, "visual": {}}))

        # In-progress sessions can be deleted; a student with none left is still listed.
        assert c.delete(f"/api/tests/{live}").status_code == 200
        assert c.delete(f"/api/tests/{b}").status_code == 200
        students = {x["key"]: x for x in c.get("/api/students").json()["students"]}
        assert students["ann"]["completed"] == 0 and students["ann"]["in_progress"] == 0
        assert c.get("/api/students/Ann/tests").json()["tests"] == []
        assert students["bo"]["completed"] == 1

    replayed = Memory(data_dir)
    assert set(replayed.sessions) == {bo} and "ann" in replayed.students
    assert replayed.sessions[bo]["proficiency"].snapshot() == mem().sessions[bo]["proficiency"].snapshot()


def test_question_history():
    data_dir = Path(tempfile.mkdtemp())
    server.app.state.agent = AssessmentAgent(StubLLM(), Memory(data_dir))
    with TestClient(server.app) as c:
        def answer(tid, ok=True):
            q = c.post(f"/api/tests/{tid}/next").json()["question"]
            mc = q["answer_format"] == "multiple_choice"
            c.post(f"/api/tests/{tid}/answer", json={"idx": q["idx"], "answer": ("A" if ok else "B") if mc else ("0.75" if ok else "5/6")})
            return q

        done = c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5", "num_questions": 3}).json()["test_id"]
        for ok in (True, False, True):
            answer(done, ok)
        c.post(f"/api/tests/{done}/finish")
        c.post(f"/api/tests/{done}/items/0/visual")

        live = c.post("/api/tests", json={"student_name": "Ann", "topic": "Decimals", "grade": "5",
                                          "num_questions": 4, "reattempts": 1}).json()["test_id"]
        answer(live, True)                                     # one finished question
        q = c.post(f"/api/tests/{live}/next").json()["question"]
        r = c.post(f"/api/tests/{live}/answer", json={"idx": q["idx"], "answer": "B" if q["answer_format"] == "multiple_choice" else "5/6"}).json()
        assert r["retry"]                                      # second question is mid-retry

        h = c.get("/api/students/ann/questions").json()
        assert [x["test_id"] for x in h["sessions"]] == [live, done], "newest session first"
        assert h["total_questions"] == 4
        live_qs = h["sessions"][0]["questions"]
        assert [x["number"] for x in live_qs] == [1], "open / mid-retry question is not revealed"
        assert h["sessions"][0]["status"] == "in_progress" and h["sessions"][1]["status"] == "completed"
        first = h["sessions"][1]["questions"][0]
        for field in ("question_text", "correct_answer", "answer", "solution_steps", "concepts", "rationale", "attempts"):
            assert field in first, field
        assert first["visual"]["panels"], "saved picture explanation is included"
        assert [x["is_correct"] for x in h["sessions"][1]["questions"]] == [True, False, True]

        assert c.get("/api/students/nobody/questions").status_code == 404
        c.delete(f"/api/tests/{done}")
        assert [x["test_id"] for x in c.get("/api/students/ann/questions").json()["sessions"]] == [live]


def test_timer_modes():
    data_dir = Path(tempfile.mkdtemp())
    server.app.state.agent = AssessmentAgent(StubLLM(), Memory(data_dir))
    mem = lambda: server.app.state.agent.memory
    start = lambda **kw: c.post("/api/tests", json={"student_name": "Ann", "topic": "Fractions", "grade": "5",
                                                    "num_questions": 3, **kw})
    with TestClient(server.app) as c:
        # Validation
        assert start(timer_mode="fixed").status_code == 422
        assert start(timer_mode="off", time_limit_minutes=10).status_code == 422
        assert start(timer_mode="fixed", seconds_per_question=10).status_code == 422
        assert start(timer_mode="sometimes").status_code == 422

        # Fixed: every question gets the chosen limit; the AI's suggestion is kept.
        fixed = start(timer_mode="fixed", seconds_per_question=45).json()["test_id"]
        q = c.post(f"/api/tests/{fixed}/next").json()["question"]
        assert q["timer_mode"] == "fixed" and q["time_limit_seconds"] == 45 and 43 <= q["remaining_seconds"] <= 45
        it = mem().sessions[fixed]["items"][0]
        assert it["item"]["suggested_time_limit_seconds"] == 90
        # Pause and continue: the remaining time is preserved.
        it["active_since"] -= 20
        c.post(f"/api/tests/{fixed}/pause")
        resumed = c.post(f"/api/tests/{fixed}/next").json()["question"]
        assert resumed["idx"] == q["idx"] and 23 <= resumed["remaining_seconds"] <= 26
        it["active_since"] -= 60                                # now well past 45 s
        r = c.post(f"/api/tests/{fixed}/answer", json={"idx": q["idx"], "answer": "A"}).json()
        assert r["timed_out"] and r["time_taken"] == 45

        # Off: no limits, never times out, and reports still work.
        off = start(timer_mode="off").json()["test_id"]
        for i in range(3):
            q = c.post(f"/api/tests/{off}/next").json()["question"]
            assert q["time_limit_seconds"] is None and q["remaining_seconds"] is None
            assert q["session_time_limit_seconds"] is None
            mem().sessions[off]["items"][i]["active_since"] -= 5000
            mc = q["answer_format"] == "multiple_choice"
            r = c.post(f"/api/tests/{off}/answer", json={"idx": q["idx"], "answer": "A" if mc else "0.75"}).json()
            assert not r["timed_out"] and r["is_correct"] and r["time_taken"] > 4000
        d = c.post(f"/api/tests/{off}/finish").json()
        assert d["timer_mode"] == "off" and d["assessment"]["score"] == 3
        assert d["assessment"]["timing"]["untimed"] == 3 and d["assessment"]["timing"]["avg_fraction_of_limit"] is None
        assert all(x["time_limit_seconds"] is None for x in d["questions"])
        prog = c.get("/api/students/Ann/progress").json()
        assert prog["summary"]["questions_answered"] == 4
        hist = {t["id"]: t for t in c.get("/api/students/Ann/tests").json()["tests"]}
        assert hist[fixed]["seconds_per_question"] == 45 and hist[off]["timer_mode"] == "off"

        # Default stays AI-chosen.
        auto = start().json()["test_id"]
        assert c.post(f"/api/tests/{auto}/next").json()["question"]["timer_mode"] == "auto"

    replayed = Memory(data_dir)
    assert replayed.sessions[fixed]["timer_mode"] == "fixed" and replayed.sessions[fixed]["seconds_per_question"] == 45
    assert replayed.sessions[off]["timer_mode"] == "off"


if __name__ == "__main__":
    test_llm_layer()
    test_anthropic_key_helper_and_fallback_retry()
    test_proficiency_and_selector()
    test_end_to_end()
    test_topics_and_session_time_limit()
    test_svg_sanitizer()
    test_reattempts()
    test_delete_session()
    test_question_history()
    test_timer_modes()
    print("All offline tests passed.")
