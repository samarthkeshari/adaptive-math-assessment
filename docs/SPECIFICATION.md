# Adaptive Math Assessment Agent: Specification

| | |
|---|---|
| Document | System specification: what the system must do |
| Companion | [DESIGN.md](DESIGN.md): how it does it |
| Status | Reflects the implementation in this repository |
| Date | 2026-11-26 |

---

## 1. Purpose

The Adaptive Math Assessment Agent assesses a K–12 student's mathematical proficiency
**concept by concept**. It doesn't serve a fixed or random set of questions. It builds a
model of what the student knows, identifies gaps in fundamental concepts and where they
start, and chooses each next problem to learn the most. It changes difficulty and complexity
as it goes, and explains its findings to the student, parent, and teacher.

### 1.1 Goals

1. Measure proficiency on each fundamental concept of a topic, not just a single score.
2. Find gaps and their likely root causes (weak prerequisites, specific misconceptions).
3. Adapt in real time: difficulty, single vs multi-concept complexity, problem type, and
   perspective.
4. Remember each student across sessions and show growth over time.
5. Explain: per-problem solutions, pictorial explanations, a per-session concept report,
   and an overall progress report.
6. Run locally, keep data on the user's machine, and work with several LLM providers.

### 1.2 Non-goals

- Grading free-form written proofs or handwritten work.
- Multi-user accounts, authentication, or classroom/roster management.
- Hosting as a multi-tenant cloud service.
- Certifying alignment with a specific curriculum standard (see §9, known limitations).

---

## 2. Users and roles

| Role | Description | Main needs |
|---|---|---|
| **Student** | Takes sessions in the browser | Clear problems, a fair timer, immediate feedback, explanations they can understand |
| **Parent / teacher** | Reviews results, often beside the student | Concept-level strengths and gaps, misconceptions, progress over time, what to practice next |
| **Operator** | Installs and runs the app locally | Simple start-up, a choice of LLM connection (direct API or company gateway), local data |

There is no login. A student is identified by name (see §8.1).

---

## 3. Glossary

| Term | Meaning |
|---|---|
| **Session** | One adaptive assessment on one topic at one grade (called a *test* in the API) |
| **Item / problem** | One question in a session |
| **Concept map** | The 6–10 fundamental concepts of a topic at a grade, with prerequisite links, plus 3–5 *cross-area* concepts from other strands |
| **Ability (θ)** | Per-concept proficiency estimate on the 1–10 difficulty scale. θ is the difficulty at which the student has a 50% chance of success |
| **Mastery** | Classification of θ: *secure*, *developing*, *gap*, *not assessed* |
| **Mode** | Why an item was chosen: *explore*, *probe*, *root cause*, *confirm*, *stretch* |
| **Dimension** | *Direct concept* (one concept in isolation) or *multi-concept* (topic combined with a concept from another area) |
| **Perspective** | The angle of a problem: procedural, conceptual, application, reverse, error analysis, estimation, representation, pattern |
| **Misconception** | A specific faulty idea inferred from a wrong answer, tied to a concept |
| **Performance memory** | Everything remembered about a student across sessions |

### 3.1 Difficulty scale (relative to the student's grade)

| Level | Meaning |
|---|---|
| 1–2 | Below grade level: a warm-up, single step, friendly numbers |
| 3–4 | On grade level, routine; 1–2 steps |
| 5–6 | On grade level, non-routine; 2–3 steps or a twist |
| 7–8 | Above grade level; multi-step reasoning |
| 9–10 | Challenge / competition style |

---

## 4. Functional requirements

Priority: **M** = must, **S** = should, **C** = could. All M and S requirements listed are
implemented.

### 4.1 Students and sessions

| ID | Requirement | Pri |
|---|---|---|
| F-1.1 | The home screen lists saved students and lets the user open an existing student or create one by typing a name | M |
| F-1.2 | The student dashboard shows **unfinished sessions** (topic, grade, answered/planned, time left if timed) with *Continue*, a **new-session form**, **completed sessions**, and a link to the **progress report** | M |
| F-1.3 | A new session takes: grade (K–12), topic, number of questions (3–25), and an optional session time limit | M |
| F-1.4 | **Topic** can be picked from a predefined K–12 list (filtered to the selected grade, grouped by strand, with an option to show all grades) or entered as a new topic | M |
| F-1.5 | New topics are saved and offered under "Your topics" in later sessions, for all students | S |
| F-1.6 | **Session time limit**: none, preset (10–60 min), or custom (1–180 min) | M |
| F-1.8 | **Re-attempts before showing the answer**: none (default), 1, 2 or 3 per session | M |
| F-1.9 | **Delete a session** (unfinished or completed) from the dashboard or session report, after confirmation. Deletion is permanent: the session's records are removed from storage, the student's memory, progress report and future priors are recalculated without it, and saved coach summaries become stale | M |
| F-1.7 | A student can leave mid-session (*Save & exit* or closing the tab) and continue later from the same question | M |

### 4.2 Adaptive assessment

| ID | Requirement | Pri |
|---|---|---|
| F-2.1 | At session start the system builds (or reuses) a concept map for the topic and grade, with prerequisites | M |
| F-2.2 | Each problem targets a concept chosen by an explicit policy (explore / probe / root cause / confirm / stretch), with a human-readable reason | M |
| F-2.3 | Difficulty adapts to the current ability estimate: harder after success, easier and isolated after a miss | M |
| F-2.4 | Complexity adapts: concepts under diagnosis are tested in isolation; concepts holding up are combined with a concept from a **different area of math** | M |
| F-2.5 | Problems are a balanced mix of **word** and **direct** problems | M |
| F-2.6 | Problems rotate through perspectives so each concept is tested from different angles | M |
| F-2.7 | About a third of problems are multiple choice, with every distractor tied to a specific misconception; the rest are short answer | M |
| F-2.8 | No problem repeats an earlier one in the session | M |
| F-2.9 | When a concept looks weak, its untested prerequisites are checked to find where the gap starts | M |
| F-2.10 | Concepts that build on a shaky prerequisite (directly or further down the chain) are held back | S |
| F-2.11 | A new session starts from what memory knows about the student and revisits earlier gaps first | M |

### 4.3 Answering, timing and feedback

| ID | Requirement | Pri |
|---|---|---|
| F-3.1 | Every problem has its own time limit (30–600 s, set by the LLM for the problem) with a visible countdown that auto-submits at zero | M |
| F-3.2 | If a session limit is set, a session clock is shown; it runs only while a problem is on screen (time spent generating problems and paused time are excluded) | M |
| F-3.3 | Each problem's timer is capped by the session time left. When the session clock runs out, the current answer is submitted and the session ends | M |
| F-3.4 | The server clock is authoritative; a 3-second grace allows for network latency | M |
| F-3.5 | Short answers accept equivalent forms (fractions/decimals/percents, `x = 5`, thousands separators); anything unclear is judged by the LLM | M |
| F-3.6 | Feedback shows correct/incorrect/timed out, the correct answer, time taken, a worked solution, concepts used, the likely misconception, *why this problem was chosen*, *what the agent learned* (ability before → after), and the live concept map | M |
| F-3.7 | **Explain with a picture**: after answering, the student can request 2–4 labeled diagrams that build up the solution, plus a panel showing why a wrong answer can't be right | M |
| F-3.8 | Pictorial explanations are generated once per problem, saved, and shown again in the session report | S |
| F-3.9 | Answer keys and diagnostics are never sent to the browser before the problem is answered | M |
| F-3.10 | **Re-attempts**: a session can allow 0–3 re-attempts per problem. After a wrong answer with tries left, the answer and solution stay hidden; the student gets a hint from the detected misconception (never revealing the answer); multiple-choice options already tried are disabled; the problem's timer keeps running | M |
| F-3.11 | The answer is revealed on a correct answer, when tries run out, or when the problem or session time runs out. Correct after a retry earns partial credit; every wrong attempt's misconception is recorded; reports show first-try vs after-retry results and earlier tries per problem | M |

### 4.4 Reports

| ID | Requirement | Pri |
|---|---|---|
| F-4.1 | **Session report**: score, accuracy, ability estimate, estimated level, difficulty path, session time used/limit (and whether time ran out) | M |
| F-4.2 | **Concept breakdown**: every concept with mastery, confidence, ability, correct/attempts, questions, misconceptions, likely root causes, unchecked prerequisites, a plain-language explanation of the fundamental idea, and what was observed | M |
| F-4.3 | Accuracy by single vs multi-concept, word vs direct, and perspective | M |
| F-4.4 | Question-by-question analysis: why asked, answer vs key, misconception, fundamental concepts, how they connect, diagnosis, solution, pictorial explanation | M |
| F-4.5 | Strengths, areas to improve, misconception insights, timing insights, next steps | M |
| F-4.6 | All numbers in reports are computed by the system; the LLM only writes explanatory text and may not contradict the numbers | M |
| F-4.7 | **Progress report** across all sessions: totals, ability-over-time chart (with table view), concept mastery by strand with growth, strengths/focus areas, misconceptions labeled *still present / improving / likely resolved*, topics table, accuracy by problem kind, timing habits | M |
| F-4.8 | **Coach summary**: an optional LLM-written overview with a practice plan, saved and flagged as stale when newer results exist | S |

### 4.5 Storage

| ID | Requirement | Pri |
|---|---|---|
| F-5.1 | All data is stored locally as append-only JSONL, in **one folder per student** | M |
| F-5.2 | Every step of every session is logged; the full state (including unfinished sessions and paused timers) is rebuilt from the log on start-up | M |
| F-5.3 | Each completed session is also written as one self-contained record (`tests.jsonl`) | M |
| F-5.4 | Concept maps and custom topics are cached and shared across students | S |
| F-5.5 | Deleting a session rewrites the student's files atomically (no partial files after a crash) and leaves other students untouched | M |

### 4.6 LLM connectivity

| ID | Requirement | Pri |
|---|---|---|
| F-6.1 | Claude is the default provider (model `claude-opus-5-5`) | M |
| F-6.2 | Claude can be reached either **directly** (API key) or through a **company AI gateway** (base URL plus a token-helper command, like Claude Code's `apiKeyHelper`), selectable per run | M |
| F-6.3 | Other providers are supported through an OpenAI-compatible adapter: OpenAI, Gemini, DeepSeek, Qwen, and self-hosted open-weight models (Gemma, Qwen, DeepSeek, Llama via Ollama/vLLM) | S |
| F-6.4 | The active provider, model, and connection are shown in the UI | S |

---

## 5. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| N-1 | Latency | Writing a problem: typically 8–20 s. Grading: instant for multiple choice and matching short answers, a few seconds otherwise. Session report: under ~1 min. Pictorial explanation: about 1 min (measured 69 s). Loading screens tell the user what is happening |
| N-2 | Fairness | Generation time and paused time never count against the student's clocks |
| N-3 | Correctness | The LLM is told to solve each problem and double-check the key before finalizing; all structured LLM output is validated against a schema and retried once |
| N-4 | Explainability | Every selection decision carries a stated reason; the selection policy and scoring are deterministic code, not LLM judgment |
| N-5 | Durability | Writes are append-only and flushed per event; a torn final line after a crash is skipped on replay |
| N-6 | Portability | Python 3.10+; macOS/Linux; no database server; a one-command start script |
| N-7 | Privacy | Student data stays on the local machine; only problem content and answers are sent to the configured LLM provider |
| N-8 | Security | LLM-generated SVG is sanitized and rendered in `<img>` (no script execution). Model text is inserted as text, never HTML. Answer keys stay on the server until answered. Direct mode cannot leak an API key to a gateway |
| N-9 | Accessibility | Status is never shown by color alone (labels and icons); the chart has a table view and keyboard navigation; diagrams have alt text |
| N-10 | Provider independence | Assessment logic is identical across providers; only the transport adapter differs |
| N-11 | Testability | An offline test suite runs the whole system against a stub LLM, with no network or key |

---

## 6. Key user flows

### 6.1 Take a new session
1. Home → enter or pick a student → dashboard.
2. Choose grade, topic (list or new), number of questions, session time → **Start session**.
3. The system maps concepts (first time for this topic and grade), then serves problem 1.
4. For each problem: read → answer (or the timer expires) → feedback → optionally **Explain
   with a picture** → **Next problem**.
5. After the last problem, or when the session time runs out → **See my report**.

### 6.2 Continue an unfinished session
1. Dashboard → *Continue where you left off* → **Continue**.
2. The same problem reappears with its remaining time. Both clocks resume.

### 6.3 Review progress
1. Dashboard → **Progress report** → overall view across all sessions.
2. Optionally **Generate / Refresh** the coach summary.

---

## 7. Acceptance criteria (selected)

| ID | Given / when / then | Verified by |
|---|---|---|
| AC-1 | A student misses a concept → the next item is a **probe** of the same concept, isolated, at a lower difficulty, multiple choice | `test_proficiency_and_selector` |
| AC-2 | A student answers everything right → difficulty rises and **stretch** items combine the concept with a different area | `test_proficiency_and_selector` |
| AC-3 | After a miss on concept X, untested concepts that depend on X (even indirectly) are held back | selector traces; `ancestors()` penalty |
| AC-4 | Refreshing during a problem returns the same problem, not a new one | `test_end_to_end` |
| AC-5 | Leaving pauses the clock; continuing restores the remaining time | `test_end_to_end` |
| AC-6 | A session limit caps each problem's timer; when it expires, the answer ends the session and the report records `ended_by_time` | `test_topics_and_session_time_limit` |
| AC-7 | A brand-new topic appears in the picker afterwards, without duplicates | `test_topics_and_session_time_limit` |
| AC-8 | Restarting the server rebuilds identical proficiency, profiles, and in-progress sessions from JSONL | `test_end_to_end` |
| AC-9 | Answer keys and distractor diagnostics never appear in question payloads | `test_end_to_end` |
| AC-10 | Malicious SVG (scripts, handlers, external links, entities) is cleaned or rejected | `test_svg_sanitizer` |
| AC-11 | A picture can't be requested before answering; once generated it is reused, not regenerated | `test_svg_sanitizer` |
| AC-12 | Direct mode removes an inherited gateway URL | `start.sh` mode tests |
| AC-13 | A real session runs end to end on Claude through the company gateway | live run (163 s, 5 items) |
| AC-15 | Deleting a session removes it from history, `events.jsonl` and `tests.jsonl`; progress and memory are rebuilt without its evidence; the coach summary is stale; other students are unaffected; a second delete returns 404; late events for it are ignored; the student remains listed with 0 sessions; restart reproduces the state | `test_delete_session` |
| AC-14 | With re-attempts, a wrong answer with tries left returns no answer key or solution; the tried option is remembered; correct on try 3 scores outcome 0.5 with both misconceptions recorded; time-out ends retries; 0 re-attempts behaves as before; state survives restart | `test_reattempts` |

---

## 8. Constraints and assumptions

### 8.1 Identity
Students are identified by name. The storage key is the name lowercased with
non-alphanumerics replaced by `-` ("Maya Patel" → `maya-patel`), so differently-cased
spellings refer to the same student. There is no authentication; the app is for a trusted
local machine.

### 8.2 Deployment
A single local process (`uvicorn`, one worker). State is held in memory and backed by the
JSONL log, so running several workers isn't supported.

### 8.3 LLM
The system depends on an LLM for concept maps, problems, grading of non-matching short
answers, reports, and pictures. Quality depends on the model; small local models produce
weaker problems.

---

## 9. Known limitations

| Limitation | Impact | Possible mitigation |
|---|---|---|
| Problems aren't checked against grade standards; grade fit relies on prompts and the grade-relative difficulty scale | Above-grade items may bring in next-grade ideas; root-cause probing may use earlier-grade content | Grade lock (difficulty 3–6), or standards tagging with automatic regeneration |
| The answer key is LLM-generated | A wrong key would mis-score a problem | A second-pass solve check; the student can report a problem |
| Pausing lets a student who has seen a problem think about it while away | Timing evidence is weaker | Serve a fresh problem after a long pause |
| No authentication; identity by name | Anyone at the machine can open any student | OS-level access control; add login if networked |
| A picture requested after a session finishes is stored in `events.jsonl` but not in that session's `tests.jsonl` line | The analytics export lacks late pictures | Append an update record, or regenerate the export |
| Single process | No horizontal scaling | Move state to a database if hosting is needed |
| Untested here: a real direct-API session, and providers other than Claude | Paths tested only offline | Run a session with each provider |
