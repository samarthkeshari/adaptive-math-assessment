"""Student performance memory, persisted locally as append-only JSONL, one folder per student.

data/
  concept_maps.jsonl                 cached concept maps, one per (topic, grade), shared
  custom_topics.jsonl                topics students added beyond the predefined list, shared
  students/<student>/
    student.json                     display name + created date
    events.jsonl                     every step of every session (source of truth; replayed on
                                     startup, so incomplete sessions can be continued later)
    tests.jsonl                      one consolidated record per completed session
    progress_reports.jsonl           saved holistic progress summaries

In-memory state rebuilt from the logs:
  sessions   sessions with items, responses, timers and a live ProficiencyModel
  students   long-run, cross-session concept estimates and misconception history
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path

from backend.engine.proficiency import DEFAULT_THETA, ConceptEstimate, ProficiencyModel


def student_key(name: str) -> str:
    """Folder-safe key: 'Maya Patel' -> 'maya-patel'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "student"


class JsonlFile:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def append(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

    def read(self) -> list[dict]:
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # tolerate a torn final line after a crash
        return out

    def rewrite(self, records: list[dict]) -> None:
        """Atomically replace the file's contents (write a temp file, then swap it in)."""
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


def time_used(it: dict, now: float | None = None) -> float:
    """Seconds spent on an item, excluding paused periods."""
    running = (now or time.time()) - it["active_since"] if it["active_since"] is not None else 0.0
    return it["elapsed"] + running


def response_misconceptions(r: dict) -> list[tuple[str, str]]:
    """(misconception, concept id) for every wrong attempt on a problem."""
    if "attempts" in r:
        return [(a["misconception"], a["error_concept"]) for a in r["attempts"]
                if not a["is_correct"] and a["misconception"]]
    return [(r["misconception"], r["error_concept"])] if r.get("misconception") else []


def session_time_used(s: dict, now: float | None = None) -> float:
    """Session clock: time spent with a question on screen (generation and pauses excluded)."""
    total = 0.0
    for it in s["items"]:
        total += it["response"]["time_taken"] if it["response"] else time_used(it, now)
    return total


def session_time_left(s: dict, now: float | None = None) -> float | None:
    """Seconds left on the session clock, or None when the session has no time limit."""
    if not s["time_limit_minutes"]:
        return None
    return s["time_limit_minutes"] * 60 - session_time_used(s, now)


class Memory:
    def __init__(self, data_dir: Path):
        self.root = data_dir
        self.students_dir = data_dir / "students"
        self.students_dir.mkdir(parents=True, exist_ok=True)
        self.maps_file = JsonlFile(data_dir / "concept_maps.jsonl")
        self.topics_file = JsonlFile(data_dir / "custom_topics.jsonl")
        self.custom_topics: list[dict] = self.topics_file.read()
        self.write_lock = asyncio.Lock()
        self.sessions: dict[str, dict] = {}
        self.students: dict[str, dict] = {}
        self.concept_maps: dict[str, dict] = {m["key"]: m for m in self.maps_file.read()}
        for folder in sorted(p for p in self.students_dir.iterdir() if p.is_dir()):
            self._load_student(folder.name)

    def _file(self, key: str, kind: str) -> JsonlFile:
        return JsonlFile(self.students_dir / key / f"{kind}.jsonl")

    def _load_student(self, key: str) -> None:
        """(Re)build one student's sessions and memory from their event log."""
        meta = self.students_dir / key / "student.json"
        if meta.exists():  # listed even with no sessions left
            self._student(key, json.loads(meta.read_text()).get("name", key))
        for event in self._file(key, "events").read():
            self._apply(event)

    # ------------------------------------------------------------------ writes
    async def record(self, event: dict) -> None:
        if event["type"] != "session_started" and event["session_id"] not in self.sessions:
            return  # the session was deleted while this request was in flight
        key = event.get("student_key") or self.sessions[event["session_id"]]["student_key"]
        event = {"ts": time.time(), "student_key": key, **event}
        async with self.write_lock:
            if event["type"] == "session_started":
                meta = self.students_dir / key / "student.json"
                if not meta.exists():
                    meta.parent.mkdir(parents=True, exist_ok=True)
                    meta.write_text(json.dumps({"name": event["student_name"], "key": key,
                                                "created_at": event["ts"]}, indent=2))
            self._file(key, "events").append(event)
            self._apply(event)

    async def delete_session(self, session_id: str) -> None:
        """Permanently remove a session: its events and its tests.jsonl record are rewritten
        out of the student's files, then the student's sessions and cross-session memory are
        rebuilt from what remains, so the session leaves no trace in estimates or reports."""
        async with self.write_lock:
            key = self.sessions[session_id]["student_key"]
            events = self._file(key, "events")
            events.rewrite([e for e in events.read() if e.get("session_id") != session_id])
            tests = self._file(key, "tests")
            tests.rewrite([t for t in tests.read() if t.get("test_id") != session_id])
            for sid in self.students[key]["sessions"]:
                self.sessions.pop(sid, None)
            del self.students[key]
            self._load_student(key)

    async def save_concept_map(self, cmap: dict) -> None:
        async with self.write_lock:
            self.maps_file.append(cmap)
            self.concept_maps[cmap["key"]] = cmap

    async def add_custom_topic(self, topic: str, grade: str, student_name: str) -> None:
        """Remember a new topic so it is offered in the topic picker from now on."""
        if any(t["topic"].lower() == topic.lower() for t in self.custom_topics):
            return
        record = {"topic": topic, "grade": grade, "added_by": student_name, "added_at": time.time()}
        async with self.write_lock:
            self.topics_file.append(record)
            self.custom_topics.append(record)

    async def save_test_record(self, key: str, record: dict) -> None:
        async with self.write_lock:
            self._file(key, "tests").append(record)

    async def save_progress_report(self, key: str, report: dict) -> None:
        async with self.write_lock:
            self._file(key, "progress_reports").append(report)

    def latest_progress_report(self, key: str) -> dict | None:
        if not (self.students_dir / key / "progress_reports.jsonl").exists():
            return None
        reports = self._file(key, "progress_reports").read()
        return reports[-1] if reports else None

    # ------------------------------------------------------------------ replay
    def _student(self, key: str, name: str) -> dict:
        if key not in self.students:
            self.students[key] = {"key": key, "name": name, "concepts": {}, "concept_info": {},
                                  "misconceptions": [], "sessions": []}
        self.students[key]["name"] = name  # latest spelling wins
        return self.students[key]

    def _apply(self, e: dict) -> None:
        t = e["type"]
        if t == "session_started":
            st = self._student(e["student_key"], e["student_name"])
            st["sessions"].append(e["session_id"])
            self.sessions[e["session_id"]] = {
                "id": e["session_id"], "student_key": e["student_key"],
                "student_name": e["student_name"], "topic": e["topic"], "grade": e["grade"],
                "num_questions": e["num_questions"], "concept_map_key": e["concept_map_key"],
                "time_limit_minutes": e.get("time_limit_minutes"),
                "reattempts": e.get("reattempts", 0),
                "timer_mode": e.get("timer_mode", "auto"),
                "seconds_per_question": e.get("seconds_per_question"),
                "llm": e["llm"], "started_at": e["ts"], "last_activity": e["ts"],
                "status": "in_progress", "items": [], "prior": e["prior"],
                "proficiency": ProficiencyModel(e["prior"], e["prior_overall"]),
                "assessment": None, "narrative": None, "finished_at": None,
            }
            return
        s = self.sessions[e["session_id"]]
        s["last_activity"] = e["ts"]
        if t == "item_served":
            s["items"].append({"spec": e["spec"], "item": e["item"], "served_at": e["ts"],
                               "elapsed": 0.0, "active_since": e["ts"], "response": None,
                               "attempts": []})
        elif t == "attempt_recorded":  # a wrong try with re-attempts left
            s["items"][e["idx"]]["attempts"].append(e["attempt"])
        elif t == "item_paused":
            it = s["items"][e["idx"]]
            if it["active_since"] is not None:
                it["elapsed"] += e["ts"] - it["active_since"]
                it["active_since"] = None
        elif t == "item_resumed":
            it = s["items"][e["idx"]]
            if it["active_since"] is None:
                it["active_since"] = e["ts"]
        elif t == "response_recorded":
            it = s["items"][e["idx"]]
            r = e["response"]
            it["response"] = r
            it["active_since"] = None
            it["deltas"] = s["proficiency"].apply(
                r["weights"], it["spec"]["difficulty"], r["outcome"], response_misconceptions(r))
            it["proficiency_after"] = s["proficiency"].snapshot()
            self._update_student(s, it, r, e["ts"])
        elif t == "visual_added":
            s["items"][e["idx"]]["visual"] = e["visual"]
        elif t == "session_completed":
            s.update(status="completed", finished_at=e["ts"],
                     assessment=e["assessment"], narrative=e["narrative"])

    def _update_student(self, s: dict, it: dict, r: dict, ts: float) -> None:
        st = self.students[s["student_key"]]
        names = {c["id"]: c for c in [it["spec"]["primary"], *it["spec"]["secondary"]]}
        for cid, w in r["weights"].items():
            est = st["concepts"].get(cid)
            if est is None:
                est = st["concepts"][cid] = ConceptEstimate(theta=DEFAULT_THETA)
            est.update(it["spec"]["difficulty"], r["outcome"], w)
            info = st["concept_info"].setdefault(cid, {"name": names[cid]["name"],
                                                      "area": names[cid]["area"], "topics": []})
            if s["topic"] not in info["topics"]:
                info["topics"].append(s["topic"])
            info["last_seen"] = ts
        for misconception, error_concept in response_misconceptions(r):
            target = error_concept if error_concept in r["weights"] else it["spec"]["primary"]["id"]
            st["concepts"][target].misconceptions[misconception] = (
                st["concepts"][target].misconceptions.get(misconception, 0) + 1)
            st["misconceptions"].append({"session_id": s["id"], "concept_id": target,
                                         "misconception": misconception, "ts": ts})

    # ------------------------------------------------------------------ reads
    def priors_for(self, key: str, cmap: dict) -> tuple[dict, float]:
        """Starting estimates for a new session, from what we remember about this student.

        Remembered ability carries over, but its evidence is discounted so the new session
        re-verifies it rather than trusting old results blindly.
        """
        st = self.students.get(key)
        if not st:
            return {}, DEFAULT_THETA
        prior = {}
        for c in cmap["concepts"]:
            est = st["concepts"].get(c["id"])
            if est and est.attempts:
                prior[c["id"]] = {"theta": round(est.theta, 2), "evidence": round(min(est.evidence, 2.0) * 0.5, 2)}
        if prior:
            overall = sum(p["theta"] for p in prior.values()) / len(prior)
        else:
            seen = [e.theta for e in st["concepts"].values() if e.attempts]
            overall = sum(seen) / len(seen) if seen else DEFAULT_THETA
        return prior, round(overall, 2)

    def remembered_misconceptions(self, key: str) -> dict[str, list[str]]:
        st = self.students.get(key)
        if not st:
            return {}
        return {cid: sorted(est.misconceptions, key=est.misconceptions.get, reverse=True)
                for cid, est in st["concepts"].items() if est.misconceptions}

    def student_sessions(self, key: str) -> list[dict]:
        st = self.students.get(key)
        if not st:
            return []
        return [self.sessions[sid] for sid in reversed(st["sessions"])]

    def list_students(self) -> list[dict]:
        out = []
        for key, st in self.students.items():
            sessions = [self.sessions[sid] for sid in st["sessions"]]
            out.append({
                "key": key, "name": st["name"],
                "completed": sum(s["status"] == "completed" for s in sessions),
                "in_progress": sum(s["status"] == "in_progress" for s in sessions),
                "last_activity": max((s["last_activity"] for s in sessions), default=None),
            })
        return sorted(out, key=lambda x: -(x["last_activity"] or 0))
