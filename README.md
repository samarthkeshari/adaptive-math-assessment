# Adaptive Math Assessment Agent

An agent that works out a student's math proficiency **concept by concept**. It doesn't
pick random questions: every problem is chosen to learn the most about what the student
does and doesn't understand. It probes possible gaps, follows prerequisite chains to find
where a gap starts, and stretches strengths with harder multi-concept problems. Every
test is stored as JSONL, and the student's performance memory carries over from one test
to the next.

```
┌─ frontend/ ────────────────┐                  ┌─ backend/ ───────────────────────────────────────┐
│ Student interface          │   HTTP / JSON    │ main.py   REST API (FastAPI); serves frontend/   │
│ index.html · app.js        │ ──────────────▶  │                                                  │
│ styles.css                 │ ◀──────────────  │ engine/   AssessmentAgent (agent.py)             │
│                            │                  │   concept_map → selector → item_writer           │
│ home · dashboard · timer   │                  │   grader → proficiency → assessment              │
│ feedback · pictures        │                  │   progress · visualizer · topics                 │
│ session & progress reports │                  │ storage/  memory.py: per-student JSONL memory    │
└────────────────────────────┘                  │ llm/      Claude (direct or gateway) · OpenAI    │
                                                │           Gemini · DeepSeek · Qwen · Gemma…      │
                                                │ tests/    offline suite (stub LLM)               │
                                                └──────────────┬───────────────────┬───────────────┘
                                                               │                   │
                                                         data/ (JSONL)       LLM providers
```

The LLM writes the problems, diagnoses wrong answers, builds the concept map, and writes
the report's explanations. The decisions (which concept to test next, at what difficulty,
and how mastery is scored) are deterministic Python, so they are explainable and the
same whichever model is plugged in.

## Documentation

- [docs/SPECIFICATION.md](docs/SPECIFICATION.md): goals, users, functional and non-functional requirements, acceptance criteria, known limitations
- [docs/DESIGN.md](docs/DESIGN.md): architecture, data model, algorithms, request flows, LLM layer, security, testing, design decisions

## Start the app locally

Requirements: Python 3.10+, and access to Claude in **one or both** of these ways:

| | Direct Anthropic API | Company AI gateway |
|---|---|---|
| Use when | you have your own Anthropic API key | your organization routes Claude through a gateway (as Claude Code does here) |
| Credential | `ANTHROPIC_API_KEY` (from console.anthropic.com) | a token helper command that prints a short-lived token |
| Start with | `./start.sh direct` | `./start.sh gateway` |

`./start.sh` sets up the virtual environment (via `./setup.sh`), checks the connection
settings, and starts the server. Then open **http://127.0.0.1:8000** (Ctrl+C to stop). The footer of the
home page shows which connection is active, e.g. *via direct API* or
*via gateway (ai-gateway…)*.

### Virtual environment

The app runs in a Python virtual environment, `.venv` in the project root (git-ignored).
`./setup.sh` manages it, and `./start.sh` runs it automatically before every start:

- **First run:** creates `.venv` with your `python3` (3.10+ required) and installs
  `backend/requirements.txt` (FastAPI, Uvicorn, the Anthropic and OpenAI SDKs).
- **Later runs:** reinstalls only if `backend/requirements.txt` changed (it keeps a
  fingerprint in `.venv/.requirements.sha256`), so starting is quick.
- **Old Python:** a `.venv` made with Python older than 3.10 is rebuilt automatically.

```bash
./setup.sh                       # set up ahead of time (optional; start.sh does it too)
./setup.sh --force               # reinstall all dependencies
PYTHON=python3.12 ./setup.sh     # create the venv with a specific Python
rm -rf .venv                     # remove it; the next start recreates it
```

### Option A: direct Anthropic API

```bash
cd adaptive-math-assessment
export ANTHROPIC_API_KEY=sk-ant-...
./start.sh direct
```

Direct mode **clears `ANTHROPIC_BASE_URL`** for the app, so a leftover gateway URL in your
shell can never receive your API key.

### Option B: company AI gateway

```bash
cd adaptive-math-assessment
export GATEWAY_BASE_URL=https://your-ai-gateway.example.com
export GATEWAY_TOKEN_HELPER="~/.claude/your-token-helper --quiet"
./start.sh gateway
```

How it works: the app runs the helper to get a token, sends it in both the `X-Api-Key` and
`Authorization: Bearer` headers (like Claude Code's `apiKeyHelper`), and re-runs the helper
every 5 minutes (`ANTHROPIC_API_KEY_HELPER_TTL`) or right after an authentication error. If
the gateway rejects the refusal-fallback beta, the app retries without it. Claude Code's own
variable names also work: `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY_HELPER`.

To find your gateway values, look in `~/.claude/settings.json`: `apiKeyHelper` is the
helper command and `env.ANTHROPIC_BASE_URL` is the gateway URL.

### `.env` file: API keys and settings

Put your keys and settings in a file named `.env` in the project root, next to `start.sh`.
`./start.sh` loads it on every run. It's in `.gitignore`, so it's never committed. Values
already exported in your shell take precedence over `.env`.

Template (copy it into `.env`, then replace the placeholders):

```bash
# Environment for ./start.sh (variables exported in your shell take precedence).
# Holds secrets: git-ignored, never commit it.
# Choose per run:  ./start.sh direct  |  ./start.sh gateway  |  ./start.sh (auto)

# --- Direct Anthropic API (key from https://console.anthropic.com/)
ANTHROPIC_API_KEY=sk-ant-your-key-here

# --- Company AI gateway
# Values are in ~/.claude/settings.json: env.ANTHROPIC_BASE_URL and apiKeyHelper.
GATEWAY_BASE_URL=https://your-ai-gateway.example.com
GATEWAY_TOKEN_HELPER="~/.claude/your-token-helper --quiet"
# Seconds between gateway token refreshes:
# ANTHROPIC_API_KEY_HELPER_TTL=300

# --- Model / provider (Claude is the default)
# LLM_MODEL=claude-opus-5-5
# Other providers: openai, gemini, deepseek, qwen, ollama, vllm, openai_compatible
# LLM_PROVIDER=anthropic
# OPENAI_API_KEY=
# GEMINI_API_KEY=
# DEEPSEEK_API_KEY=
# DASHSCOPE_API_KEY=
# LLM_BASE_URL=
# LLM_API_KEY=
# LLM_JSON_MODE=schema

# --- Server / storage
# PORT=8000
# DATA_DIR=./data
```

Format rules: one `KEY=value` per line, no spaces around `=`, quotes optional. Put
comments on **their own line**: text after a value (`KEY=value  # note`) becomes part of
the value. You can fill in both the direct and the gateway sections and choose per run:

```bash
./start.sh direct     # uses ANTHROPIC_API_KEY (gateway settings are ignored and cleared)
./start.sh gateway    # uses GATEWAY_BASE_URL + GATEWAY_TOKEN_HELPER
./start.sh            # auto: gateway if a token helper is configured, otherwise direct
```

The footer of the home page shows the active connection (*via direct API* or
*via gateway (…)*), so you can confirm which one is in use.

### Other options

- `PORT=9000 ./start.sh direct` uses another port.
- `DATA_DIR=/path ./start.sh` stores student data elsewhere.
- `LLM_MODEL=claude-sonnet-5-5 ./start.sh …` picks a different Claude model. On a gateway,
  use a model ID the gateway exposes.
- Manual start, without `start.sh` (from the project root):

  ```bash
  ./setup.sh                               # or: python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
  source .venv/bin/activate                # activate the virtual environment
  export ANTHROPIC_API_KEY=sk-ant-...      # or ANTHROPIC_BASE_URL + ANTHROPIC_API_KEY_HELPER
  uvicorn backend.main:app --port 8000     # one worker (memory is in-process)
  deactivate                               # when done
  ```
- Run the tests inside the venv: `.venv/bin/python -m backend.tests.test_offline`.

Offline tests (stub LLM, no key needed), from the project root: `python3 -m backend.tests.test_offline`

## Using the app

1. **Home**: type a student's name, or pick one from the list of saved students.
2. **Student dashboard**:
   - **Continue where you left off**: sessions that aren't finished, showing how many
     questions are answered. Continue picks up at the same question.
   - **Start a new session**:
     - **Topic:** pick from the predefined K–12 list, which shows the topics for the selected
       grade grouped by strand (tick *Show topics for all grades* to see everything). Or choose
       **➕ Add a new topic…** and type any topic. New topics are saved to
       `data/custom_topics.jsonl` and appear under *Your topics* from then on.
     - **Number of questions:** 3–25.
     - **Session time:** no limit, 10–60 minutes, or a custom value (1–180 minutes). The
       session clock runs only while a problem is on screen, so time spent waiting for the AI
       to write problems doesn't count, and it pauses when the student leaves. Each problem's
       own timer is capped by the session time left. When the session clock reaches 0, the
       current answer is submitted, the session ends, and the report covers the questions
       answered and says time ran out.
     - **Timer per question:** *AI-chosen* (default: a limit that fits each problem,
       30 s–10 min), *Fixed time per question* (30 s, 45 s, 1, 1.5, 2, 3, 5 or 10 minutes, the
       same for every question), or *No timer* (untimed practice: no countdown, no automatic
       submit, and no session time limit). With a session time limit, each question's timer
       is also capped by the session time left.
     - **Re-attempts before showing the answer:** none (default), 1, 2 or 3. After a wrong
       answer with tries left, the answer and solution stay hidden. The student sees
       "Not quite, try again (N tries left)" with a hint based on the detected mistake, and a
       wrong multiple-choice option already picked is struck out. The problem's timer keeps
       running. The answer is revealed when they get it right, run out of tries, or run out
       of time. Correct after a retry counts as partial credit, and every wrong attempt's
       misconception is recorded.
   - **Completed sessions**: opens each session's concept report.
   - **Delete**: every session (unfinished or completed) has a Delete button, and so does the
     session report. After a confirmation, the session is removed **permanently** from the
     student's `events.jsonl` and `tests.jsonl`. The student's long-term memory and progress
     report are recalculated without it, and a saved coach summary is marked out of date.
     The student stays listed even if no sessions are left.
   - **Progress report**: the overall view across all sessions.
   - **All questions & explanations**: every question answered in any session, grouped by
     session (newest first). Each shows the question, the student's answer and earlier
     tries, the correct answer, the worked solution, concepts, the misconception, why it was
     asked, the report's diagnosis, and the picture explanation (generated on request).
     You can filter by session, topic or result (correct, incorrect, timed out, needed
     re-attempts) and search by text. Unfinished sessions have a **Review** button that opens
     this screen for just that session. A question that's still open, or mid-retry, is never
     shown, so its answer can't be seen early.
3. **During a session**: **⏸ Pause** stops both clocks and hides the problem until the
   student clicks **Resume**, so they can't keep working on it with the clock stopped.
   **Save & exit** (or just closing the tab) also pauses, so the session can be continued
   later with the same time left. Untimed sessions have no Pause button, since there's no
   clock to stop.
4. **Explain with a picture**: after answering, the student can ask for a pictorial
   explanation. The AI draws 2–4 labeled diagrams that build up the solution (fraction bars,
   number lines, area models, tape diagrams, arrays, shapes, graphs). After a wrong answer it
   adds a panel showing why that answer can't be right. Each explanation is generated once,
   saved with the session, and also shown in the session report (the button is in each
   question there too). Generating one takes about a minute.
5. **Progress report**:
   - headline numbers and an *ability over time* chart (with a table view);
   - concept mastery grouped by strand, with growth since each concept was first assessed;
   - strengths and focus areas;
   - misconceptions labeled *still present*, *improving*, or *likely resolved*;
   - a table of topics, accuracy by problem kind, and timing habits;
   - an optional AI **coach summary** with a practice plan. It's saved, and flagged as
     stale when newer results exist.

## Choosing the LLM

Set these environment variables before starting the server:

| Provider | Settings | Notes |
|---|---|---|
| Claude (default) | `ANTHROPIC_API_KEY` | Native Anthropic SDK. Default model is `claude-opus-5-5`; change it with `LLM_MODEL`. Uses structured outputs, effort levels, and server-side refusal fallback. |
| OpenAI | `LLM_PROVIDER=openai LLM_MODEL=<model> OPENAI_API_KEY=…` | Strict JSON schema. |
| Google Gemini | `LLM_PROVIDER=gemini LLM_MODEL=<model> GEMINI_API_KEY=…` | Uses Gemini's OpenAI-compatible endpoint. |
| DeepSeek | `LLM_PROVIDER=deepseek LLM_MODEL=<model> DEEPSEEK_API_KEY=…` | JSON mode, with the schema included in the prompt. |
| Qwen (DashScope) | `LLM_PROVIDER=qwen LLM_MODEL=<model> DASHSCOPE_API_KEY=…` | International endpoint. Override it with `LLM_BASE_URL` if needed. |
| Gemma / Qwen / DeepSeek / Llama, self-hosted | `LLM_PROVIDER=ollama LLM_MODEL=gemma3` (or `vllm`) | Runs locally. Default URLs are `localhost:11434` (Ollama) and `localhost:8000` (vLLM). Run vLLM on a different port from this app. |
| Any other OpenAI-compatible server | `LLM_PROVIDER=openai_compatible LLM_BASE_URL=… LLM_MODEL=… LLM_API_KEY=…` | Covers LM Studio, Together, Groq, OpenRouter, and similar services. |

`LLM_API_KEY` overrides the provider's usual key variable. If a server rejects
`json_schema` response formats, set `LLM_JSON_MODE=object`. Whatever the provider,
every response is checked against its schema and retried once if it's invalid. Leading
`<think>` blocks from reasoning models are removed before parsing. To add another provider,
subclass `llm.base.LLMProvider` and implement `_complete_json`.

Small local models tend to write weaker problems and answer keys. Use the largest model
you can for item writing.

## How the agent builds an understanding of the student

**1. Concept map.** For each topic and grade, the LLM lists 6–10 fundamental concepts
with their prerequisites, plus 3–5 related concepts from other areas of math (for
example, fractions combined with rectangle area). The map is cached, so concept IDs stay
the same across tests and memory can build up.

**2. Proficiency model** (`backend/engine/proficiency.py`). Each concept has an ability estimate
θ on the same 1–10 scale as problem difficulty. θ is the difficulty at which the student
has a 50% chance of success. After each answer, θ is updated in the style of Elo or a
one-parameter IRT model: `θ += K·w·(outcome − P(correct))`.
- Solving a problem harder than expected raises θ a lot. Missing an easy one lowers it a lot.
- `K` shrinks as evidence builds up.
- Correct but over time counts as half credit.
- On a multi-concept problem, credit is shared across the concepts. Blame goes mostly to
  the concept the grader says caused the error.
- An untested concept starts from the student's overall ability, capped by its weakest
  tested prerequisite.
- Mastery levels: **secure** (θ ≥ 6), **developing**, and **gap** (θ < 4 *with at least
  one miss*). Confidence comes from the amount of evidence.

**3. Adaptive selection** (`backend/engine/selector.py`). Each concept is scored, and the best
one becomes the next problem's focus, with the reason shown to the student:

| Mode | When | What it does |
|---|---|---|
| explore | no evidence yet | foundational concepts first; concepts that were gaps in earlier tests get priority |
| root_cause | an untested prerequisite of a shaky concept | finds where a gap actually starts |
| probe | just missed, or a gap with little evidence | re-tests the concept on its own, one level easier, with multiple-choice options built to reveal misconceptions; tells a slip from a real gap |
| confirm | developing | same concept from a new angle, often combined with another concept |
| stretch | secure | difficulty θ + 1.5, combined with a concept from another area |

Concepts that build on a shaky prerequisite (directly or further down the chain) are held
back. Difficulty is set near θ, where a problem tells the agent the most. The selector
also balances word vs direct problems, rotates through 8 angles for each concept
(computing, explaining why, real-world use, working backwards, spotting a mistake,
estimating, switching between forms, patterns), and keeps about a third of problems
multiple choice.

**4. Diagnosis.** Every wrong multiple-choice option is written to reflect a specific
misconception and tagged with the concept it reveals. Wrong short answers are diagnosed by
the LLM. Misconceptions are stored per concept, fed into later problems ("check whether
this misconception is still present"), and tracked across tests.

**5. Concept-level assessment.** The engine computes mastery, confidence, accuracy by
problem kind, timing, and **root causes**: a gap whose prerequisite is also a gap.
The LLM then explains each fundamental idea and the evidence for it. It's told to treat
the engine's numbers as facts, and it has to write a note for each concept the engine
listed.

**6. Performance memory.** Cross-test, per-concept ability and misconception history for
each student. A new session starts from remembered ability (with its evidence discounted, so
the agent checks it again instead of trusting it) and revisits remembered gaps first. The
**Progress report** shows this memory.

## Safety of the drawings

The diagrams are SVG written by the LLM, so they're treated as untrusted. The server parses
each one and keeps only an allow-list of drawing elements and attributes. It removes
scripts, `<style>`, `foreignObject`, event handlers, external links, and `url(...)`
references, and rejects DOCTYPE/entity declarations. The browser then shows the result as
an `<img>`, where SVG can't run scripts or load anything. A panel that fails validation is
dropped, not shown.

## Local data: one folder per student

Everything is stored on this computer under `data/` (set `DATA_DIR` to move it), in plain,
append-only JSONL:

```
data/
  concept_maps.jsonl            cached concept map per (topic, grade), shared by all students
  custom_topics.jsonl           topics added beyond the predefined list (backend/engine/topics.py)
  students/
    maya/                       folder name = student name, lowercased ("Maya Patel" → maya-patel)
      student.json              display name, created date
      events.jsonl              every step of every session
      tests.jsonl               one complete record per finished session
      progress_reports.jsonl    saved coach summaries
```

| File | One line per | Contents |
|---|---|---|
| `events.jsonl` | event | `session_started` (topic, grade, LLM, priors), `item_served` (selector spec with mode, reason, concepts, difficulty, plus the full problem with answer key and misconception tags), `item_paused` / `item_resumed` (timer), `attempt_recorded` (a wrong try with re-attempts left), `visual_added` (pictorial explanation), `response_recorded` (answer, correctness, time, misconception, implicated concept, credit weights), `session_completed` (assessment and narrative). This is the source of truth: it's replayed on startup, which is how unfinished sessions can be continued after a restart. |
| `tests.jsonl` | finished session | A self-contained record: every problem with its spec, answer, and the proficiency snapshot after it, plus the final proficiency, the assessment, and the narrative. Ready for analytics. |
| `progress_reports.jsonl` | coach summary | The summary text, when it was written, which model wrote it, and how much evidence it was based on. |

To back up or move a student, copy their folder. Don't edit files while the server is
running.

## API

| Method & path | Purpose |
|---|---|
| `GET /api/topics?grade=5` | predefined topics for a grade (all grades if omitted) plus custom topics |
| `GET /api/students` | saved students with completed / in-progress counts |
| `GET /api/students/{name}/tests` | a student's sessions (in progress and completed) |
| `GET /api/students/{name}/questions` | every answered question with answers, solutions and explanations, grouped by session |
| `GET /api/students/{name}/progress` | the full progress report (plus the latest saved coach summary) |
| `POST /api/students/{name}/progress/summary` | generate and save a new coach summary |
| `POST /api/tests` | start a session `{student_name, topic, grade, num_questions, time_limit_minutes, reattempts, timer_mode, seconds_per_question}` (`time_limit_minutes: null` = no limit; `reattempts` 0–3; `timer_mode` `auto` / `fixed` (needs `seconds_per_question` 30–600) / `off` (no session limit allowed)) |
| `POST /api/tests/{id}/next` | the open question (resumes its timer) or the next adaptive one |
| `POST /api/tests/{id}/answer` | submit `{idx, answer}`. Returns `{retry: true, attempts_left, note, hint, question}` while tries remain, else the full feedback plus what the agent learned |
| `POST /api/tests/{id}/pause` | pause the open question's timer |
| `POST /api/tests/{id}/items/{idx}/visual` | pictorial explanation of an answered question (generated once, then cached) |
| `POST /api/tests/{id}/finish` | concept-level report for the session |
| `GET /api/tests/{id}` | a session's details and report |
| `DELETE /api/tests/{id}` | permanently delete a session (unfinished or completed) |

## Layout

The code is split into a **backend** (Python package: API + assessment engine) and a
**frontend** (static files, no build step). The backend serves the frontend, so one process
runs everything.

```
backend/                      Python package: run with `uvicorn backend.main:app`
  main.py                     REST API (FastAPI); serves frontend/ at / and /static/
  engine/                     the assessment engine (no web code)
    agent.py                  orchestrator: start → next → submit/retry → finish; visuals; delete
    concept_map.py            topic decomposition and prerequisites
    selector.py               adaptive selection rules
    item_writer.py            LLM problem writer
    grader.py                 grading and misconception diagnosis
    proficiency.py            per-concept ability model
    assessment.py             concept-level session assessment and narrative
    progress.py               overall progress report and coach summary
    visualizer.py             pictorial explanations (LLM-drawn SVG, sanitized)
    topics.py                 predefined K–12 topic list by strand and grade range
  llm/                        provider interface, Anthropic adapter, OpenAI-compatible adapter
  storage/memory.py           per-student JSONL storage and performance memory
  tests/test_offline.py       offline test suite (stub LLM)
  requirements.txt
frontend/                     student-facing UI: index.html, app.js, styles.css
data/                         student data (JSONL; created on first run; git-ignored)
docs/                         SPECIFICATION.md, DESIGN.md
setup.sh                      create/update the virtual environment (.venv)
start.sh                      one-command local start (runs setup.sh; direct API or gateway)
```

The frontend talks to the backend only through the JSON API under `/api/` (table above). To
host it separately, serve `frontend/` from any static server and proxy `/api/*` to the
backend. `FRONTEND_DIR` and `DATA_DIR` override the default locations.
