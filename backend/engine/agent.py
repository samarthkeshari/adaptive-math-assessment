"""AssessmentAgent: the loop that ties the components together.

    start   -> concept map (cached) + priors from student memory
    next    -> selector decides concept/difficulty/complexity -> LLM writes the item
    submit  -> grade + diagnose -> update proficiency + memory -> (next selection adapts)
    finish  -> concept-level assessment (deterministic) + narrative (LLM) -> tests.jsonl
"""

import asyncio
import time
import uuid
from collections import defaultdict

from backend.llm import LLMError, LLMProvider
from backend.storage.memory import Memory, session_time_left, session_time_used, student_key, time_used

from . import assessment, grader, item_writer, progress, selector, topics, visualizer
from .concept_map import build_concept_map, map_key
from .proficiency import outcome_value, response_weights

TIMER_GRACE_SECONDS = 3  # network latency allowance


class AgentError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class AssessmentAgent:
    def __init__(self, llm: LLMProvider, memory: Memory):
        self.llm = llm
        self.memory = memory
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def _llm_call(self, coro):
        try:
            return await coro
        except LLMError as e:
            raise AgentError(str(e), 502) from e
        except (ValueError, KeyError) as e:
            raise AgentError(f"The model produced an unusable result ({e}). Please retry.", 502) from e

    def session(self, session_id: str) -> dict:
        s = self.memory.sessions.get(session_id)
        if not s:
            raise AgentError("Test not found", 404)
        return s

    # ------------------------------------------------------------------ start
    async def start(self, student_name: str, topic: str, grade: str, num_questions: int,
                    time_limit_minutes: int | None = None, reattempts: int = 0,
                    timer_mode: str = "auto", seconds_per_question: int | None = None) -> str:
        """timer_mode: "auto" (AI picks each question's time), "fixed" (seconds_per_question
        for every question) or "off" (untimed: no per-question limit, no session limit)."""
        if timer_mode == "off":
            time_limit_minutes = None
        if timer_mode != "fixed":
            seconds_per_question = None
        key = map_key(topic, grade)
        async with self._locks[f"map:{key}"]:
            cmap = self.memory.concept_maps.get(key)
            if cmap is None:
                cmap = await self._llm_call(build_concept_map(self.llm, topic, grade))
                await self.memory.save_concept_map(cmap)

        if not topics.is_predefined(topic):
            await self.memory.add_custom_topic(topic, grade, student_name)
        skey = student_key(student_name)
        prior, prior_overall = self.memory.priors_for(skey, cmap)
        session_id = uuid.uuid4().hex[:12]
        await self.memory.record({
            "type": "session_started", "session_id": session_id, "student_key": skey,
            "student_name": student_name, "topic": topic, "grade": grade,
            "num_questions": num_questions, "time_limit_minutes": time_limit_minutes,
            "reattempts": reattempts, "timer_mode": timer_mode,
            "seconds_per_question": seconds_per_question,
            "concept_map_key": key, "llm": self.llm.describe(),
            "prior": prior, "prior_overall": prior_overall,
        })
        return session_id

    # ------------------------------------------------------------------ next item
    async def next_item(self, session_id: str) -> dict:
        async with self._locks[session_id]:
            s = self.session(session_id)
            if s["status"] == "completed":
                return {"done": True}
            items = s["items"]
            if items and items[-1]["response"] is None:
                if items[-1]["active_since"] is None:  # continuing a paused session
                    await self.memory.record({"type": "item_resumed", "session_id": session_id,
                                              "idx": len(items) - 1})
                return {"done": False, "question": self._public(s, len(items) - 1)}
            if len(items) >= s["num_questions"]:
                return {"done": True}
            left = session_time_left(s)
            if left is not None and left <= TIMER_GRACE_SECONDS:
                return {"done": True, "ended_by_time": True}

            cmap = self.memory.concept_maps[s["concept_map_key"]]
            remembered = self.memory.remembered_misconceptions(s["student_key"])
            spec = selector.select_next(cmap, s["proficiency"], items, remembered)
            pid = spec["primary"]["id"]
            known = list(dict.fromkeys(
                list(s["proficiency"].get(pid).misconceptions) + remembered.get(pid, [])))
            item = await self._llm_call(
                item_writer.write_item(self.llm, s["topic"], s["grade"], spec, items, known))
            # Session timing choice overrides the AI's suggested limit (kept for reference).
            item["suggested_time_limit_seconds"] = item["time_limit_seconds"]
            if s["timer_mode"] == "fixed":
                item["time_limit_seconds"] = s["seconds_per_question"]
            elif s["timer_mode"] == "off":
                item["time_limit_seconds"] = None
            await self.memory.record({"type": "item_served", "session_id": session_id,
                                      "idx": len(items), "spec": spec, "item": item})
            return {"done": False, "question": self._public(s, len(items) - 1)}

    def _public(self, s: dict, idx: int) -> dict:
        """The question as shown to the student: no answer key or diagnostics."""
        it = s["items"][idx]
        spec, item = it["spec"], it["item"]
        limit = item["time_limit_seconds"]                       # None = untimed session
        remaining = limit - time_used(it) if limit is not None else None
        session_left = session_time_left(s)
        if session_left is not None and remaining is not None:
            remaining = min(remaining, session_left)  # the session clock caps every question
        return {
            "idx": idx, "number": idx + 1, "total": s["num_questions"],
            "difficulty": spec["difficulty"], "question_type": spec["question_type"],
            "dimension": spec["dimension"], "perspective": spec["perspective"],
            "focus": spec["primary"]["name"],
            "also": [c["name"] for c in spec["secondary"]],
            "answer_format": item["answer_format"], "question_text": item["question_text"],
            "choices": item["choices"], "answer_hint": item["answer_hint"],
            "time_limit_seconds": item["time_limit_seconds"],
            "remaining_seconds": max(0, round(remaining)) if remaining is not None else None,
            "timer_mode": s["timer_mode"],
            "session_time_limit_seconds": s["time_limit_minutes"] * 60 if s["time_limit_minutes"] else None,
            "session_remaining_seconds": max(0, round(session_left)) if session_left is not None else None,
            "attempts_allowed": 1 + s["reattempts"],
            "attempts_used": len(it["attempts"]),
            "previous_answers": [a["answer"] for a in it["attempts"]],
        }

    # ------------------------------------------------------------------ submit
    async def submit(self, session_id: str, idx: int, answer: str) -> dict:
        async with self._locks[session_id]:
            s = self.session(session_id)
            if not (0 <= idx < len(s["items"])):
                raise AgentError("Question not found", 404)
            it = s["items"][idx]
            if it["response"] is not None:
                raise AgentError("Question already answered", 409)
            spec, item = it["spec"], it["item"]

            # The server clock is authoritative for the timer (paused time excluded).
            if it["active_since"] is None:
                raise AgentError("This question is paused. Continue the session first.", 409)
            elapsed = time_used(it)
            limit = item["time_limit_seconds"]                   # None = untimed session
            timed_out = limit is not None and elapsed > limit + TIMER_GRACE_SECONDS
            time_taken = min(elapsed, float(limit)) if timed_out else elapsed

            verdict = await self._llm_call(grader.grade(self.llm, spec, item, answer))
            attempt_no = len(it["attempts"]) + 1
            attempt = {"answer": answer, "is_correct": verdict["is_correct"],
                       "misconception": verdict["misconception"],
                       "error_concept": verdict["error_concept"], "note": verdict["note"],
                       "time": round(time_taken, 1)}
            session_left = session_time_left(s)
            session_over = session_left is not None and session_left <= TIMER_GRACE_SECONDS
            attempts_allowed = 1 + s["reattempts"]

            # Wrong, with re-attempts and time left: keep the answer hidden and let them retry.
            if (not verdict["is_correct"] and not timed_out and not session_over
                    and answer.strip() and attempt_no < attempts_allowed):
                await self.memory.record({"type": "attempt_recorded", "session_id": session_id,
                                          "idx": idx, "attempt": attempt})
                return {
                    "retry": True,
                    "attempts_left": attempts_allowed - attempt_no,
                    "note": verdict["note"] or "Not quite.",
                    "hint": verdict["misconception"],
                    "question": self._public(s, idx),
                }

            attempts = [*it["attempts"], attempt]
            outcome = outcome_value(verdict["is_correct"], timed_out, attempt_no)
            last_wrong = next((a for a in reversed(attempts) if not a["is_correct"]), None)
            weights = response_weights(spec["primary"]["id"], [c["id"] for c in spec["secondary"]],
                                       outcome, last_wrong["error_concept"] if last_wrong else "")
            note = ("Time ran out. " if timed_out else "") + verdict["note"]
            if verdict["is_correct"] and attempt_no > 1:
                note = f"Correct on attempt {attempt_no}. " + note
            before = {cid: s["proficiency"].get(cid).to_dict() for cid in weights}
            await self.memory.record({
                "type": "response_recorded", "session_id": session_id, "idx": idx,
                "response": {
                    "answer": answer, "is_correct": verdict["is_correct"], "timed_out": timed_out,
                    "time_taken": round(time_taken, 1), "outcome": outcome,
                    "misconception": verdict["misconception"] or (last_wrong or {}).get("misconception", ""),
                    "error_concept": verdict["error_concept"] or (last_wrong or {}).get("error_concept", ""),
                    "note": note, "weights": weights,
                    "attempts": attempts, "attempt_count": attempt_no,
                },
            })
            session_left = session_time_left(s)
            session_over = session_left is not None and session_left <= TIMER_GRACE_SECONDS
            names = {c["id"]: c["name"] for c in [spec["primary"], *spec["secondary"]]}
            learned = [
                {"concept": names[cid], "before": before[cid]["theta"],
                 "after": round(s["proficiency"].get(cid).theta, 2),
                 "mastery": s["proficiency"].get(cid).mastery}
                for cid in weights
            ]
            return {
                "retry": False,
                "is_correct": verdict["is_correct"], "timed_out": timed_out,
                "time_taken": round(time_taken, 1), "note": note,
                "misconception": it["response"]["misconception"],
                "attempts": attempts, "attempt_count": attempt_no,
                "attempts_allowed": attempts_allowed,
                "correct_answer": item["correct_answer"], "choices": item["choices"],
                "solution_steps": item["solution_steps"], "concepts": item["concepts"],
                "why_this_question": spec["rationale"], "mode": spec["mode"],
                "learned": learned,
                "profile": self.live_profile(s),
                "session_remaining_seconds": max(0, round(session_left)) if session_left is not None else None,
                "session_over": session_over,
                "is_last": len(s["items"]) >= s["num_questions"] or session_over,
            }

    async def pause(self, session_id: str) -> dict:
        """Stop the clock on the open question (student left; session can be continued later)."""
        async with self._locks[session_id]:
            s = self.session(session_id)
            items = s["items"]
            if items and items[-1]["response"] is None and items[-1]["active_since"] is not None:
                await self.memory.record({"type": "item_paused", "session_id": session_id,
                                          "idx": len(items) - 1})
            return {"paused": True}

    async def visual(self, session_id: str, idx: int) -> dict:
        """Pictorial explanation of an answered question (generated once, then reused)."""
        async with self._locks[f"visual:{session_id}:{idx}"]:
            s = self.session(session_id)
            if not (0 <= idx < len(s["items"])):
                raise AgentError("Question not found", 404)
            it = s["items"][idx]
            if it["response"] is None:
                raise AgentError("Answer the question first; the picture shows the solution.", 409)
            if it.get("visual"):
                return it["visual"]
            visual = await self._llm_call(visualizer.explain_visually(
                self.llm, s["topic"], s["grade"], it["spec"], it["item"], it["response"]))
            await self.memory.record({"type": "visual_added", "session_id": session_id,
                                      "idx": idx, "visual": visual})
            return visual

    async def delete(self, session_id: str) -> dict:
        """Permanently delete a session (in progress or completed)."""
        async with self._locks[session_id]:  # waits for any in-flight next/answer/finish
            s = self.session(session_id)
            await self.memory.delete_session(session_id)
            return {"deleted": True, "test_id": session_id, "student_name": s["student_name"]}

    def live_profile(self, s: dict) -> list[dict]:
        cmap = self.memory.concept_maps[s["concept_map_key"]]
        out = []
        for c in cmap["concepts"]:
            est = s["proficiency"].get(c["id"])
            if c["external"] and not est.attempts:
                continue
            out.append({"name": c["name"], "theta": round(est.theta, 2), "mastery": est.mastery,
                        "attempts": est.attempts, "external": c["external"]})
        return out

    # ------------------------------------------------------------------ finish
    async def finish(self, session_id: str) -> dict:
        async with self._locks[session_id]:
            s = self.session(session_id)
            if s["status"] == "completed":
                return self.detail(s)
            if not any(it["response"] for it in s["items"]):
                raise AgentError("No answered questions yet", 400)
            cmap = self.memory.concept_maps[s["concept_map_key"]]
            result = assessment.assess(cmap, s["proficiency"], s["items"])
            answered = sum(1 for it in s["items"] if it["response"])
            left = session_time_left(s)
            result["timing"].update(
                session_limit_seconds=s["time_limit_minutes"] * 60 if s["time_limit_minutes"] else None,
                session_used_seconds=round(session_time_used(s)),
                ended_by_time=bool(left is not None and left <= TIMER_GRACE_SECONDS
                                   and answered < s["num_questions"]),
                planned_questions=s["num_questions"],
            )
            answered_items = [it for it in s["items"] if it["response"]]
            result["attempts"] = {
                "reattempts_allowed": s["reattempts"],
                "first_try_correct": sum(1 for it in answered_items
                                         if it["response"]["is_correct"] and it["response"].get("attempt_count", 1) == 1),
                "correct_after_retry": sum(1 for it in answered_items
                                           if it["response"]["is_correct"] and it["response"].get("attempt_count", 1) > 1),
            }
            narrative = await self._llm_call(
                assessment.write_narrative(self.llm, s, cmap, result, s["items"]))
            await self.memory.record({"type": "session_completed", "session_id": session_id,
                                      "assessment": result, "narrative": narrative})
            await self.memory.save_test_record(s["student_key"], self.test_record(s))
            return self.detail(s)

    def test_record(self, s: dict) -> dict:
        """The consolidated, self-contained line written to tests.jsonl."""
        return {
            "test_id": s["id"], "student_key": s["student_key"], "student_name": s["student_name"],
            "topic": s["topic"], "grade": s["grade"], "llm": s["llm"],
            "started_at": s["started_at"], "finished_at": s["finished_at"],
            "num_questions": s["num_questions"], "time_limit_minutes": s["time_limit_minutes"],
            "reattempts": s["reattempts"], "timer_mode": s["timer_mode"],
            "seconds_per_question": s["seconds_per_question"],
            "session_time_used": round(session_time_used(s)),
            "concept_map_key": s["concept_map_key"], "prior": s["prior"],
            "items": [
                {"number": i + 1, "spec": it["spec"], "item": it["item"],
                 "response": it["response"], "proficiency_after": it.get("proficiency_after"),
                 "visual": it.get("visual")}
                for i, it in enumerate(s["items"]) if it["response"]
            ],
            "final_proficiency": s["proficiency"].snapshot(),
            "assessment": s["assessment"],
            "narrative": s["narrative"],
        }

    def answered_questions(self, s: dict) -> list[dict]:
        """Every *finished* question of a session with its answer key and explanations.
        The open question (and one mid-retry) is excluded, so no answer leaks early."""
        analysis = {a["question_number"]: a for a in (s["narrative"] or {}).get("question_analysis", [])}
        return [
            {"number": i + 1, "idx": i, "visual": it.get("visual"),
             "mode": it["spec"]["mode"], "rationale": it["spec"]["rationale"],
             "difficulty": it["spec"]["difficulty"], "question_type": it["spec"]["question_type"],
             "dimension": it["spec"]["dimension"], "perspective": it["spec"]["perspective"],
             "focus": it["spec"]["primary"]["name"],
             "also": [c["name"] for c in it["spec"]["secondary"]],
             "question_text": it["item"]["question_text"], "choices": it["item"]["choices"],
             "correct_answer": it["item"]["correct_answer"],
             "solution_steps": it["item"]["solution_steps"], "concepts": it["item"]["concepts"],
             "time_limit_seconds": it["item"]["time_limit_seconds"],
             "analysis": analysis.get(i + 1), **it["response"]}
            for i, it in enumerate(s["items"]) if it["response"]
        ]

    def question_history(self, name: str) -> dict:
        """All answered questions across a student's sessions (newest session first)."""
        key = student_key(name)
        if key not in self.memory.students:
            raise AgentError("No sessions saved for this student yet", 404)
        sessions = []
        for s in self.memory.student_sessions(key):
            questions = self.answered_questions(s)
            if questions:
                sessions.append({
                    "test_id": s["id"], "topic": s["topic"], "grade": s["grade"],
                    "status": s["status"], "started_at": s["started_at"],
                    "finished_at": s["finished_at"], "num_questions": s["num_questions"],
                    "questions": questions,
                })
        return {"student_name": self.memory.students[key]["name"], "sessions": sessions,
                "total_questions": sum(len(x["questions"]) for x in sessions)}

    def detail(self, s: dict) -> dict:
        return {
            "test_id": s["id"], "student_name": s["student_name"], "topic": s["topic"],
            "grade": s["grade"], "status": s["status"], "started_at": s["started_at"],
            "finished_at": s["finished_at"], "num_questions": s["num_questions"], "llm": s["llm"],
            "time_limit_minutes": s["time_limit_minutes"], "session_time_used": round(session_time_used(s)),
            "reattempts": s["reattempts"], "timer_mode": s["timer_mode"],
            "seconds_per_question": s["seconds_per_question"],
            "assessment": s["assessment"], "narrative": s["narrative"],
            "questions": self.answered_questions(s),
        }

    def history(self, name: str) -> list[dict]:
        return [
            {"id": s["id"], "topic": s["topic"], "grade": s["grade"], "status": s["status"],
             "started_at": s["started_at"], "last_activity": s["last_activity"],
             "finished_at": s["finished_at"], "num_questions": s["num_questions"],
             "time_limit_minutes": s["time_limit_minutes"], "timer_mode": s["timer_mode"],
             "seconds_per_question": s["seconds_per_question"],
             "session_time_left": (round(max(0, session_time_left(s)))
                                   if s["time_limit_minutes"] else None),
             "answered": sum(1 for it in s["items"] if it["response"]),
             "score": s["assessment"]["score"] if s["assessment"] else None,
             "overall_theta": round(s["proficiency"].overall.theta, 1)}
            for s in self.memory.student_sessions(student_key(name))
        ]

    # ------------------------------------------------------------------ progress
    def progress(self, name: str) -> dict:
        key = student_key(name)
        report = progress.build_progress(self.memory, key)
        if report is None:
            raise AgentError("No sessions saved for this student yet", 404)
        saved = self.memory.latest_progress_report(key)
        report["coach_summary"] = saved
        report["coach_summary_stale"] = bool(saved) and saved["evidence_count"] != report["evidence_count"]
        return report

    async def write_progress_summary(self, name: str) -> dict:
        key = student_key(name)
        async with self._locks[f"progress:{key}"]:
            report = self.progress(name)
            if not report["evidence_count"]:
                raise AgentError("Answer some questions first", 400)
            summary = await self._llm_call(progress.write_summary(self.llm, report))
            saved = {"generated_at": time.time(), "evidence_count": report["evidence_count"],
                     "llm": self.llm.describe(), "summary": summary}
            await self.memory.save_progress_report(key, saved)
            report["coach_summary"], report["coach_summary_stale"] = saved, False
            return report
