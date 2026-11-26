// Adaptive Math Assessment - student-facing client
const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const LETTERS = ["A", "B", "C", "D", "E", "F"];
const PERSPECTIVE_LABELS = {
  procedural: "Procedural", conceptual: "Conceptual", application: "Application",
  reverse: "Work backwards", error_analysis: "Error analysis", estimation: "Estimation",
  representation: "Representation", pattern: "Patterns",
};
const DIMENSION_LABELS = { direct_concept: "Single concept", multi_concept: "Multi-concept" };
const TYPE_LABELS = { word: "Word problems", direct: "Direct problems" };
const MODE_LABELS = {
  explore: "Explore", probe: "Probe gap", root_cause: "Root cause", confirm: "Confirm", stretch: "Stretch",
};
const MASTERY = {
  secure: ["good", "Secure"], developing: ["warn", "Developing"], gap: ["bad", "Gap"],
  not_assessed: ["neutral", "Not assessed"],
};
const MISC_STATUS = { "still present": "bad", improving: "warn", "likely resolved": "good" };
const RING_LENGTH = 100.53; // 2πr for r=16
// Difficulty is relative to the student's grade (same bands the item writer uses).
const LEVEL_BANDS = [[2, "below grade level (warm-up)"], [4, "on grade level (routine)"],
  [6, "on grade level (challenging)"], [8, "above grade level"], [10, "challenge level"]];
const levelText = (d) => `level ${d}/10 · ${LEVEL_BANDS.find(([max]) => d <= max)[1]}`;
const GRADE_LEVEL = 5;      // difficulty scale: 5 = on-grade, non-routine

const NEW_TOPIC = "__new__";
const state = {
  student: null, testId: null, question: null, timerHandle: null, deadline: null,
  sessionDeadline: null, sessionLimit: null, submitting: false,
};

// ---------------------------------------------------------------- helpers
function show(screen) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.add("hidden"));
  $(`screen-${screen}`).classList.remove("hidden");
  state.screen = screen;
  window.scrollTo(0, 0);
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

function fillList(id, items, empty = "") {
  const list = (items || []).map((t) => el("li", {}, t));
  $(id).replaceChildren(...(list.length || !empty ? list : [el("li", { class: "muted" }, empty)]));
}

function showError(msg) {
  const box = $("error");
  box.textContent = msg;
  box.classList.remove("hidden");
  clearTimeout(showError.t);
  showError.t = setTimeout(() => box.classList.add("hidden"), 8000);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

const enc = encodeURIComponent;
const fmtTime = (sec) => {
  sec = Math.max(0, Math.round(sec));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
};
const fmtDate = (ts) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const fmtDay = (ts) => new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function loading(text) {
  $("loading-text").textContent = text;
  show("loading");
}

function choiceLabel(choices, letter) {
  const i = LETTERS.indexOf(letter);
  return i >= 0 && choices[i] != null ? `${letter}. ${choices[i]}` : letter;
}

function masteryBadge(m) {
  const [cls, label] = MASTERY[m] || ["neutral", m];
  return el("span", { class: `badge ${cls}` }, label);
}

// Horizontal ability bar on the 1-10 scale, with a tick at grade level.
function abilityBar(theta, mastery) {
  const pct = Math.max(2, Math.min(100, (theta / 10) * 100));
  return el("div", { class: `bar ${mastery}` },
    el("span", { style: `width:${pct}%` }),
    el("i", { class: "grade-mark", style: `left:${GRADE_LEVEL * 10}%`, title: "grade level" }));
}

function barRow(name, theta, mastery, { external = false, extra = null, wide = false } = {}) {
  return el("div", { class: `bar-row${wide ? " wide" : ""}` },
    el("span", { class: `name${external ? " external" : ""}`, title: name }, name),
    abilityBar(theta, mastery),
    el("span", { class: "small" }, masteryBadge(mastery), extra ? " " : "", extra));
}

function rateRows(title, obj, labels = {}) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return [];
  return [
    el("h4", {}, title),
    el("div", { class: "kv" }, ...entries.flatMap(([k, v]) => {
      const pct = v.total ? v.correct / v.total : 0;
      return [el("span", {}, labels[k] || k),
        el("div", { class: `bar ${pct >= 0.75 ? "secure" : pct >= 0.4 ? "developing" : "gap"}` },
          el("span", { style: `width:${Math.max(2, pct * 100)}%` })),
        el("span", { class: "small" }, `${v.correct}/${v.total}`)];
    })),
  ];
}

const stat = (label, value, sub = "") => el("div", { class: "stat" },
  el("div", { class: "label" }, label), el("div", { class: "value" }, value),
  sub ? el("div", { class: "small muted" }, sub) : null);

// ---------------------------------------------------------------- home: students
async function showHome() {
  await pauseIfOnQuestion();
  state.student = null;
  $("nav-dashboard").classList.add("hidden");
  show("home");
  try {
    const { students } = await api("/api/students");
    $("students-hint").textContent = students.length ? "Pick a student to continue." : "No students yet. Add one on the left.";
    $("students").replaceChildren(...students.map((s) => el("li", { onclick: () => openStudent(s.name) },
      el("div", {},
        el("div", { class: "h-topic" }, s.name),
        el("div", { class: "small muted" },
          `${s.completed} completed` + (s.last_activity ? ` · last active ${fmtDate(s.last_activity)}` : ""))),
      s.in_progress ? el("span", { class: "badge neutral" }, `${s.in_progress} to continue`) : null)));
  } catch (e) {
    showError(e.message);
  }
}

// ---------------------------------------------------------------- student dashboard
async function openStudent(name) {
  await pauseIfOnQuestion();
  state.student = name;
  localStorage.setItem("studentName", name);
  $("nav-dashboard").textContent = `${name}'s dashboard`;
  $("nav-dashboard").classList.remove("hidden");
  $("dash-name").textContent = name;
  show("student");
  loadTopics();
  try {
    const { tests } = await api(`/api/students/${enc(name)}/tests`);
    const open = tests.filter((t) => t.status === "in_progress");
    const done = tests.filter((t) => t.status === "completed");
    $("dash-sub").textContent = tests.length
      ? `${done.length} completed · ${open.length} in progress`
      : "New student. Start a first session below.";
    $("progress-btn").disabled = !tests.some((t) => t.answered > 0);

    $("continue-card").classList.toggle("hidden", !open.length);
    $("in-progress").replaceChildren(...open.map((t) => el("li", { onclick: () => resumeTest(t.id) },
      el("div", {},
        el("div", { class: "h-topic" }, t.topic),
        el("div", { class: "small muted" },
          `Grade ${t.grade} · started ${fmtDate(t.started_at)} · last active ${fmtDate(t.last_activity)}`
          + (t.time_limit_minutes ? ` · ${fmtTime(t.session_time_left)} of ${t.time_limit_minutes} min left` : "")),
        el("div", { class: "mini-progress" }, el("div", { style: `width:${(100 * t.answered) / t.num_questions}%` }))),
      el("span", { class: "actions" },
        el("span", { class: "small muted" }, `${t.answered}/${t.num_questions} answered`),
        el("button", { class: "btn small-btn primary", type: "button" }, "Continue"),
        deleteButton(t)))));

    $("completed-hint").textContent = done.length ? "" : "No completed sessions yet.";
    $("completed").replaceChildren(...done.map((t) => {
      const pct = Math.round((100 * t.score) / Math.max(1, t.answered));
      return el("li", { onclick: () => openReport(t.id) },
        el("div", {},
          el("div", { class: "h-topic" }, t.topic),
          el("div", { class: "small muted" }, `Grade ${t.grade} · ${fmtDate(t.finished_at)} · ability ${t.overall_theta}/10`
            + (t.time_limit_minutes ? ` · ${t.time_limit_minutes}-min session` : ""))),
        el("span", { class: "actions" },
          el("span", { class: `badge ${pct >= 80 ? "good" : pct >= 50 ? "warn" : "bad"}` }, `${t.score}/${t.answered}`),
          deleteButton(t)));
    }));
  } catch (e) {
    showError(e.message);
  }
}

// ---------------------------------------------------------------- delete a session
function deleteButton(t) {
  return el("button", {
    class: "btn small-btn danger", type: "button", title: "Delete this session",
    "aria-label": `Delete ${t.topic} session`,
    onclick: (e) => { e.stopPropagation(); deleteSession(t); },  // don't open/continue the row
  }, "Delete");
}

async function deleteSession(t) {
  const what = `${t.topic} (grade ${t.grade}) session from ${fmtDate(t.started_at)}`;
  if (!confirm(`Delete the ${what}?\n\nIts questions, answers, pictures and report are removed `
      + "permanently, and the progress report is recalculated without it. This can't be undone.")) return;
  try {
    await api(`/api/tests/${t.id}`, { method: "DELETE" });
    if (state.testId === t.id) state.testId = null;
    openStudent(state.student);
  } catch (e) {
    showError(e.message);
  }
}

// ---------------------------------------------------------------- session flow
// ---------------------------------------------------------------- topic picker & session options
async function loadTopics() {
  const select = $("topic-select");
  const previous = select.value;
  const grade = $("all-grades").checked ? "" : `?grade=${enc($("grade").value)}`;
  try {
    const { predefined, custom } = await api(`/api/topics${grade}`);
    const options = [el("option", { value: "" }, "Choose a topic…")];
    predefined.forEach((g) => options.push(el("optgroup", { label: g.strand },
      g.topics.map((t) => el("option", { value: t }, t)))));
    if (custom.length) {
      options.push(el("optgroup", { label: "Your topics" }, custom.map((t) => el("option", { value: t }, t))));
    }
    options.push(el("option", { value: NEW_TOPIC }, "➕ Add a new topic…"));
    select.replaceChildren(...options);
    if ([...select.options].some((o) => o.value === previous)) select.value = previous;
  } catch (e) {
    showError(e.message);
  }
  onTopicChange();
}

function onTopicChange() {
  const isNew = $("topic-select").value === NEW_TOPIC;
  $("new-topic-wrap").classList.toggle("hidden", !isNew);
  $("new-topic").required = isNew;
  if (isNew) $("new-topic").focus();
}

function sessionMinutes() {
  const v = $("session-time").value;
  if (!v) return null;
  const m = v === "custom" ? parseInt($("custom-time").value, 10) : parseInt(v, 10);
  return Number.isFinite(m) && m > 0 ? Math.min(180, m) : null;
}

function updateSessionPlan() {
  $("custom-time-wrap").classList.toggle("hidden", $("session-time").value !== "custom");
  const n = parseInt($("num-questions").value, 10);
  const m = sessionMinutes();
  const r = parseInt($("reattempts").value, 10) || 0;
  $("session-plan").textContent = (m
    ? `${n} questions in ${m} minutes: about ${fmtTime((m * 60) / n)} per question. If time runs out, the session ends and the report covers the questions answered.`
    : `${n} questions, no overall time limit (each problem still has its own timer).`)
    + (r ? ` After a wrong answer the student can try again ${r === 1 ? "once" : `${r} times`} (with a hint) before the answer is shown.` : "");
}

async function startSession(evt) {
  evt.preventDefault();
  const picked = $("topic-select").value;
  const topic = (picked === NEW_TOPIC ? $("new-topic").value : picked).trim();
  if (!topic) {
    showError("Choose a topic or type a new one.");
    return;
  }
  const body = {
    student_name: state.student,
    grade: $("grade").value,
    topic,
    num_questions: parseInt($("num-questions").value, 10) || 10,
    time_limit_minutes: sessionMinutes(),
    reattempts: parseInt($("reattempts").value, 10) || 0,
  };
  loading("Mapping the fundamental concepts of this topic…");
  try {
    const { test_id } = await api("/api/tests", { method: "POST", body });
    await resumeTest(test_id);
  } catch (e) {
    showError(e.message);
    openStudent(state.student);
  }
}

async function resumeTest(testId) {
  state.testId = testId;
  await nextQuestion();
}

async function nextQuestion() {
  loading("Choosing what to check next and writing your problem…");
  try {
    const data = await api(`/api/tests/${state.testId}/next`, { method: "POST" });
    if (data.done) return finishTest();
    renderQuestion(data.question);
  } catch (e) {
    showError(e.message);
    openStudent(state.student);
  }
}

// Stop the server-side clock when the student leaves an open question.
async function pauseIfOnQuestion() {
  stopTimer();
  if (state.screen === "question" && state.testId && !state.submitting) {
    try { await api(`/api/tests/${state.testId}/pause`, { method: "POST" }); } catch (_) { /* best effort */ }
  }
}

window.addEventListener("pagehide", () => {
  if (state.screen === "question" && state.testId && !state.submitting) {
    navigator.sendBeacon(`/api/tests/${state.testId}/pause`);
  }
});

function renderQuestion(q) {
  state.question = q;
  state.submitting = false;
  const answered = q.number - 1;
  $("q-progress").textContent = `Problem ${q.number} of ${q.total} · ${answered} answered`;
  // One dot per question: filled = answered, ringed = current, empty = still to come.
  $("q-dots").replaceChildren(...Array.from({ length: q.total }, (_, i) =>
    el("span", { class: i < answered ? "done" : i === answered ? "current" : "" })));

  $("q-tags").replaceChildren(
    el("span", { class: "badge neutral" }, `Focus: ${q.focus}${q.also.length ? " + " + q.also.join(", ") : ""}`),
    el("span", { class: "badge neutral" }, q.question_type === "word" ? "Word problem" : "Direct problem"),
    el("span", { class: "badge neutral" }, DIMENSION_LABELS[q.dimension]),
    el("span", { class: "badge neutral" }, PERSPECTIVE_LABELS[q.perspective] || q.perspective),
  );
  $("q-level").textContent = levelText(q.difficulty);
  $("q-text").textContent = q.question_text;
  $("q-hint").textContent = q.answer_hint;

  $("retry-box").classList.add("hidden");
  const multi = q.attempts_allowed > 1;
  $("q-attempts").classList.toggle("hidden", !multi);
  $("q-attempts").textContent = multi
    ? `Attempt ${q.attempts_used + 1} of ${q.attempts_allowed}` + (q.attempts_used ? ` · already tried: ${q.previous_answers.join(", ")}` : "")
    : "";

  const choices = $("choices");
  const input = $("short-answer");
  if (q.answer_format === "multiple_choice") {
    input.classList.add("hidden");
    choices.replaceChildren(...q.choices.map((c, i) => {
      const tried = q.previous_answers.includes(LETTERS[i]);  // wrong earlier: can't pick again
      const radio = el("input", { type: "radio", name: "choice", value: LETTERS[i] });
      radio.disabled = tried;
      const label = el("label", { class: `choice${tried ? " tried" : ""}` }, radio, el("span", { class: "letter" }, `${LETTERS[i]}.`), el("span", {}, c));
      radio.addEventListener("change", () => {
        choices.querySelectorAll(".choice").forEach((n) => n.classList.remove("selected"));
        label.classList.add("selected");
      });
      return label;
    }));
  } else {
    choices.replaceChildren();
    input.value = q.previous_answers.length ? q.previous_answers[q.previous_answers.length - 1] : "";
    input.classList.remove("hidden");
  }
  $("submit-btn").disabled = false;
  show("question");
  if (q.answer_format !== "multiple_choice") input.focus();
  state.sessionLimit = q.session_time_limit_seconds;
  state.sessionDeadline = q.session_remaining_seconds != null ? Date.now() + q.session_remaining_seconds * 1000 : null;
  $("session-clock").classList.toggle("hidden", state.sessionDeadline == null);
  startTimer(q.remaining_seconds, q.time_limit_seconds);
}

function startTimer(remaining, limit) {
  stopTimer();
  state.deadline = Date.now() + remaining * 1000;
  const tick = () => {
    const left = (state.deadline - Date.now()) / 1000;
    const frac = Math.max(0, left / limit);
    $("timer-text").textContent = fmtTime(left);
    $("timer-ring").style.strokeDashoffset = String(RING_LENGTH * (1 - frac));
    $("timer").classList.toggle("warn", frac <= 0.3 && frac > 0.1);
    $("timer").classList.toggle("danger", frac <= 0.1);
    if (state.sessionDeadline != null) {
      const sLeft = (state.sessionDeadline - Date.now()) / 1000;
      $("session-clock-text").textContent = fmtTime(sLeft);
      $("session-clock").classList.toggle("warn", sLeft <= 120 && sLeft > 30);
      $("session-clock").classList.toggle("danger", sLeft <= 30);
    }
    if (left <= 0) {
      stopTimer();
      submitAnswer(null, true);
    }
  };
  tick();
  state.timerHandle = setInterval(tick, 250);
}

function stopTimer() {
  if (state.timerHandle) clearInterval(state.timerHandle);
  state.timerHandle = null;
}

function currentAnswer() {
  if (state.question.answer_format === "multiple_choice") {
    const checked = document.querySelector('input[name="choice"]:checked');
    return checked ? checked.value : "";
  }
  return $("short-answer").value.trim();
}

async function submitAnswer(evt, fromTimer = false) {
  if (evt) evt.preventDefault();
  if (state.submitting) return;
  const answer = currentAnswer();
  if (!answer && !fromTimer) {
    showError("Please enter or choose an answer first.");
    return;
  }
  state.submitting = true;
  stopTimer();
  $("submit-btn").disabled = true;
  const q = state.question;
  loading(fromTimer ? "Time's up! Checking your answer…" : "Checking your answer…");
  try {
    const r = await api(`/api/tests/${state.testId}/answer`, { method: "POST", body: { idx: q.idx, answer } });
    if (r.retry) {
      renderQuestion(r.question);  // same problem, same running clock, wrong choice disabled
      const box = $("retry-box");
      box.replaceChildren(
        el("strong", {}, `${r.note} Try again: ${r.attempts_left} ${r.attempts_left === 1 ? "try" : "tries"} left.`),
        r.hint ? el("div", { class: "small" }, `Something to check: ${r.hint}`) : null);
      box.classList.remove("hidden");
      return;
    }
    renderFeedback(q, answer, r);
  } catch (e) {
    showError(e.message);
    state.submitting = false;
    show("question");
    $("submit-btn").disabled = false;
  }
}

function renderFeedback(q, answer, r) {
  const banner = $("fb-banner");
  if (r.timed_out) {
    banner.className = "banner warn";
    banner.textContent = r.is_correct ? "⏱ Correct, but time ran out" : "⏱ Time ran out";
  } else if (r.is_correct) {
    banner.className = "banner good";
    banner.textContent = "✓ Correct!";
  } else {
    banner.className = "banner bad";
    banner.textContent = "✗ Not quite";
  }
  const mc = q.answer_format === "multiple_choice";
  $("fb-yours").textContent = answer ? (mc ? choiceLabel(r.choices, answer) : answer) : "(no answer)";
  $("fb-correct").textContent = mc ? choiceLabel(r.choices, r.correct_answer) : r.correct_answer;
  $("fb-time").textContent = `${fmtTime(r.time_taken)} / ${fmtTime(q.time_limit_seconds)}`;
  $("fb-note").textContent = r.note;
  const misc = $("fb-misconception");
  misc.classList.toggle("hidden", !r.misconception);
  misc.textContent = r.misconception ? `Likely misconception: ${r.misconception}` : "";
  const tries = $("fb-attempts");
  const earlier = (r.attempts || []).slice(0, -1);
  tries.classList.toggle("hidden", !earlier.length);
  tries.replaceChildren(...(earlier.length ? [
    el("h3", {}, `Answered in ${r.attempt_count} attempts`),
    el("ul", { class: "concepts" }, earlier.map((a, i) => el("li", {},
      el("strong", {}, `Try ${i + 1}: ${mc ? choiceLabel(r.choices, a.answer) : a.answer}`),
      a.misconception ? `: ${a.misconception}` : ""))),
  ] : []));
  const sess = $("fb-session");
  sess.className = r.session_over ? "time-banner" : `small muted${r.session_remaining_seconds == null ? " hidden" : ""}`;
  sess.textContent = r.session_over
    ? "⏱ Session time is up. Your report covers the questions you answered."
    : r.session_remaining_seconds != null ? `Session time left: ${fmtTime(r.session_remaining_seconds)}` : "";
  fillList("fb-steps", r.solution_steps);
  $("fb-visual").replaceChildren();
  const vbtn = $("fb-visual-btn");
  vbtn.classList.remove("hidden");
  vbtn.textContent = "🖼 Explain with a picture";
  vbtn.onclick = () => loadVisual(state.testId, q.idx, $("fb-visual"), vbtn);
  $("fb-concepts").replaceChildren(...r.concepts.map((c) => el("li", {}, el("strong", {}, c.name), ` (${c.area}): ${c.role}`)));

  $("fb-why").replaceChildren(el("span", { class: "badge neutral" }, MODE_LABELS[r.mode] || r.mode), " ", r.why_this_question);
  $("fb-learned").replaceChildren(...r.learned.map((l) => {
    const delta = l.after - l.before;
    return el("li", {}, el("strong", {}, l.concept), ": ",
      el("span", { class: delta >= 0 ? "up" : "down" }, `${delta >= 0 ? "▲" : "▼"} ${l.before.toFixed(1)} → ${l.after.toFixed(1)}`),
      " ", masteryBadge(l.mastery));
  }));
  $("fb-profile").replaceChildren(...r.profile.map((c) => barRow(c.name, c.theta, c.mastery, { external: c.external })));

  $("next-btn").textContent = r.is_last ? "See my report" : "Next problem";
  $("next-btn").onclick = r.is_last ? finishTest : nextQuestion;
  $("fb-exit-btn").classList.toggle("hidden", r.is_last);
  show("feedback");
}

async function finishTest() {
  loading("Assessing each fundamental concept and writing your report…");
  try {
    renderReport(await api(`/api/tests/${state.testId}/finish`, { method: "POST" }));
  } catch (e) {
    showError(e.message);
    openStudent(state.student);
  }
}

async function openReport(testId) {
  loading("Loading report…");
  try {
    renderReport(await api(`/api/tests/${testId}`));
  } catch (e) {
    showError(e.message);
    openStudent(state.student);
  }
}

// ---------------------------------------------------------------- pictorial explanations
// Diagrams are sanitized SVG from the server, shown via <img> so they can never run scripts.
function renderVisual(container, v) {
  container.replaceChildren(
    el("p", { class: "visual-intro" }, v.intro),
    el("div", { class: "visual-panels" }, v.panels.map((p, i) => el("figure", { class: "visual-panel" },
      el("div", { class: "visual-title" }, `${i + 1}. ${p.title}`),
      el("img", { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(p.svg)}`, alt: p.alt }),
      el("figcaption", {}, p.caption)))),
    el("div", { class: "key-idea" }, el("strong", {}, "Key idea: "), v.key_idea));
}

async function loadVisual(testId, idx, container, btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "Drawing the explanation…";
  try {
    renderVisual(container, await api(`/api/tests/${testId}/items/${idx}/visual`, { method: "POST" }));
    btn.classList.add("hidden");
  } catch (e) {
    showError(e.message);
    btn.textContent = label;
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- session report
function reportVisual(testId, q) {
  const box = el("div", { class: "visual-box" });
  const holder = el("div");
  if (q.visual) {
    renderVisual(holder, q.visual);
    box.append(holder);
  } else {
    const btn = el("button", { type: "button", class: "btn small-btn" }, "🖼 Explain with a picture");
    btn.addEventListener("click", () => loadVisual(testId, q.idx, holder, btn));
    box.append(btn, holder);
  }
  return box;
}

function renderReport(d) {
  const a = d.assessment;
  const n = d.narrative;
  if (!a || !n) { showError("This session has no report yet."); openStudent(state.student); return; }
  $("r-title").textContent = `${d.topic}: Grade ${d.grade}`;
  $("r-delete").onclick = () => deleteSession({ id: d.test_id, topic: d.topic, grade: d.grade, started_at: d.started_at });
  $("r-sub").textContent = `${d.student_name} · ${fmtDate(d.started_at)} · ${d.llm.provider} / ${d.llm.model}`;

  const correct = d.questions.filter((q) => q.is_correct).length;
  $("r-stats").replaceChildren(
    stat("Score (in time)", `${a.score}/${a.answered}`),
    stat("Accuracy", `${Math.round((100 * correct) / Math.max(1, a.answered))}%`),
    stat("Ability estimate", `${a.overall_theta.toFixed(1)}/10`),
    stat("Secure concepts", String(a.secure.length)),
    stat("Gaps found", String(a.gaps.length)),
    stat("Avg time", a.timing.avg_seconds != null ? fmtTime(a.timing.avg_seconds) : "-"),
    ...(a.attempts && a.attempts.reattempts_allowed ? [stat("First try / after retry",
      `${a.attempts.first_try_correct} / ${a.attempts.correct_after_retry}`,
      `${a.attempts.reattempts_allowed} re-attempt${a.attempts.reattempts_allowed > 1 ? "s" : ""} allowed`)] : []),
    ...(a.timing.session_limit_seconds ? [stat("Session time",
      `${fmtTime(a.timing.session_used_seconds)} / ${fmtTime(a.timing.session_limit_seconds)}`,
      a.timing.ended_by_time ? `time ran out: ${a.answered} of ${a.timing.planned_questions} answered` : "")] : []),
  );

  const traj = [];
  d.questions.forEach((q, i) => {
    if (i) traj.push(el("span", { class: "arrow" }, "→"));
    const cls = q.timed_out ? "warn" : q.is_correct ? "good" : "bad";
    const mark = q.timed_out ? "⏱" : q.is_correct ? "✓" : "✗";
    traj.push(el("span", { class: `step ${cls}`, title: `Q${q.number}: ${MODE_LABELS[q.mode]} · ${q.focus}` }, `${q.difficulty}${mark}`));
  });
  $("r-trajectory").replaceChildren(...traj);
  $("r-summary").textContent = n.overall_summary;
  $("r-level").textContent = a.estimated_level;
  $("r-rootcauses").replaceChildren(...a.root_causes.map((rc) =>
    el("div", { class: "root-cause" }, el("strong", {}, "Root cause: "),
      `difficulty with ${rc.concept} likely stems from ${rc.because_of.join(", ")}.`)));

  const notes = Object.fromEntries(n.concept_notes.map((c) => [c.concept_id, c]));
  $("r-concepts").replaceChildren(...a.concepts.map((c) => {
    const note = notes[c.id] || {};
    return el("div", { class: `concept-card ${c.mastery === "gap" ? "needs_work" : c.mastery === "secure" ? "strong" : "developing"}` },
      el("div", { class: "cc-head" },
        el("span", { class: "cc-title" }, c.name, c.external ? el("span", { class: "small muted" }, "  (cross-area)") : null),
        el("span", {}, masteryBadge(c.mastery), " ",
          el("span", { class: "small muted" }, `${c.area} · ${c.correct}/${c.attempts} correct · confidence ${c.confidence}`
            + (c.questions.length ? ` · Q${c.questions.join(", Q")}` : "")))),
      c.attempts ? el("div", { class: "kv" }, el("span", { class: "small muted" }, "Ability"), abilityBar(c.theta, c.mastery),
        el("span", { class: "small" }, `${c.theta.toFixed(1)}/10`)) : null,
      note.fundamental_explanation ? el("p", {}, el("strong", {}, "The fundamental idea: "), note.fundamental_explanation) : null,
      note.observation ? el("p", {}, el("strong", {}, "What we saw: "), note.observation) : null,
      c.misconceptions.length ? el("p", {}, el("strong", {}, "Misconceptions: "), c.misconceptions.join("; ")) : null,
      c.likely_root_causes && c.likely_root_causes.length
        ? el("p", {}, el("strong", {}, "Likely root cause: "), c.likely_root_causes.join(", ")) : null,
      c.unchecked_prerequisites && c.unchecked_prerequisites.length
        ? el("p", { class: "small muted" }, `Prerequisites not yet checked: ${c.unchecked_prerequisites.join(", ")}`) : null);
  }));

  $("r-breakdown").replaceChildren(
    ...rateRows("Single vs multi-concept", a.accuracy_by_dimension, DIMENSION_LABELS),
    ...rateRows("Word vs direct", a.accuracy_by_type, TYPE_LABELS),
    ...rateRows("Perspective", a.accuracy_by_perspective, PERSPECTIVE_LABELS),
  );
  $("r-misc").textContent = n.misconception_insights || "No specific misconceptions detected.";
  $("r-timing").textContent = n.timing_insights;

  const analysis = Object.fromEntries(n.question_analysis.map((x) => [x.question_number, x]));
  $("r-questions").replaceChildren(...d.questions.map((q) => {
    const x = analysis[q.number] || {};
    const cls = q.timed_out ? "warn" : q.is_correct ? "good" : "bad";
    const label = q.timed_out ? "Timed out" : q.is_correct ? "Correct" : "Incorrect";
    const mc = q.choices && q.choices.length;
    return el("details", { class: "qa" },
      el("summary", {},
        el("strong", {}, `Q${q.number}`),
        el("span", { class: `badge ${cls}` }, label),
        el("span", { class: "badge neutral" }, MODE_LABELS[q.mode] || q.mode),
        el("span", { class: "badge neutral" }, `Level ${q.difficulty}`),
        (q.attempt_count || 1) > 1 ? el("span", { class: "badge warn" }, `${q.attempt_count} tries`) : null,
        el("span", { class: "badge neutral" }, q.question_type === "word" ? "Word" : "Direct"),
        el("span", { class: "badge neutral" }, DIMENSION_LABELS[q.dimension]),
        el("span", { class: "small muted" }, `${q.focus} · ${fmtTime(q.time_taken)} / ${fmtTime(q.time_limit_seconds)}`)),
      el("div", { class: "qa-body" },
        el("p", { class: "small muted" }, `Why asked: ${q.rationale}`),
        el("p", {}, q.question_text),
        mc ? el("p", { class: "small muted" }, q.choices.map((c, i) => `${LETTERS[i]}. ${c}`).join("   ")) : null,
        el("p", {}, el("strong", {}, "Your answer: "),
          q.answer ? (mc ? choiceLabel(q.choices, q.answer) : q.answer) : "(none)",
          "   ", el("strong", {}, "Correct: "), mc ? choiceLabel(q.choices, q.correct_answer) : q.correct_answer),
        (q.attempts || []).length > 1 ? el("p", {}, el("strong", {}, "Earlier tries: "),
          q.attempts.slice(0, -1).map((t) => `${mc ? choiceLabel(q.choices, t.answer) : t.answer}`
            + (t.misconception ? ` (${t.misconception})` : "")).join("; ")) : null,
        q.misconception ? el("p", {}, el("strong", {}, "Misconception: "), q.misconception) : null,
        el("p", {}, el("strong", {}, "Fundamental concepts: "), (x.fundamental_concepts || q.concepts.map((c) => c.name)).join(", ")),
        x.how_concepts_connect ? el("p", {}, el("strong", {}, "How they connect: "), x.how_concepts_connect) : null,
        x.diagnosis ? el("p", {}, el("strong", {}, "Diagnosis: "), x.diagnosis) : null,
        el("p", {}, el("strong", {}, "Solution:")),
        el("ol", { class: "steps" }, q.solution_steps.map((st) => el("li", {}, st))),
        reportVisual(d.test_id, q)));
  }));

  fillList("r-strengths", n.strengths);
  fillList("r-improve", n.areas_to_improve);
  fillList("r-next", n.recommended_next_steps);
  show("report");
}

// ---------------------------------------------------------------- holistic progress report
async function openProgress() {
  loading("Building the progress report…");
  try {
    renderProgress(await api(`/api/students/${enc(state.student)}/progress`));
  } catch (e) {
    showError(e.message);
    openStudent(state.student);
  }
}

function renderProgress(p) {
  const s = p.summary;
  $("pg-title").textContent = `${p.student_name}: progress report`;
  $("pg-sub").textContent = s.first_activity
    ? `Practicing since ${fmtDate(s.first_activity)} · last active ${fmtDate(s.last_activity)}` : "";
  $("pg-stats").replaceChildren(
    stat("Sessions completed", String(s.sessions_completed), s.sessions_in_progress ? `${s.sessions_in_progress} in progress` : ""),
    stat("Questions answered", String(s.questions_answered)),
    stat("Accuracy (in time)", s.accuracy != null ? `${Math.round(s.accuracy * 100)}%` : "-"),
    stat("Time practiced", `${Math.round(s.minutes_practiced)} min`),
    stat("Latest ability", s.latest_ability != null ? `${s.latest_ability.toFixed(1)}/10` : "-"),
    stat("Concepts", `${s.concepts_assessed}`, `${s.secure} secure · ${s.developing} developing · ${s.gaps} gaps`),
  );

  renderAbilityChart($("pg-chart"), p.ability_trend);
  $("pg-trend-table").replaceChildren(
    el("tr", {}, el("th", {}, "Date"), el("th", {}, "Topic"), el("th", {}, "Grade"), el("th", {}, "Ability"), el("th", {}, "Score")),
    ...p.ability_trend.map((t) => el("tr", {}, el("td", {}, fmtDate(t.date)), el("td", {}, t.topic), el("td", {}, t.grade),
      el("td", {}, `${t.ability.toFixed(1)}/10`), el("td", {}, `${t.score}/${t.answered}`))));

  renderCoachSummary(p);

  const byArea = {};
  p.concepts.forEach((c) => (byArea[c.area] = byArea[c.area] || []).push(c));
  $("pg-areas").replaceChildren(...p.areas.map((a) => el("div", { class: "area-block" },
    el("div", { class: "area-head" },
      el("h4", {}, a.area),
      el("span", { class: "small muted" },
        `average ability ${a.avg_theta.toFixed(1)}/10 · ${a.secure} secure · ${a.developing} developing · ${a.gap} gaps`)),
    el("div", { class: "bars" }, ...byArea[a.area].map((c) => barRow(c.name, c.theta, c.mastery, {
      wide: true,
      extra: el("span", {},
        c.growth != null && Math.abs(c.growth) >= 0.05
          ? el("span", { class: `growth ${c.growth > 0 ? "up" : "down"}` }, `${c.growth > 0 ? "▲" : "▼"}${Math.abs(c.growth).toFixed(1)} `) : null,
        el("span", { class: "muted" }, `${c.correct}/${c.attempts} · ${c.topics.join(", ")}`)),
    }))))));

  fillList("pg-strengths", p.strengths, "None secure yet. Keep practicing!");
  fillList("pg-focus", p.focus_areas, "No confirmed gaps.");
  $("pg-misc").replaceChildren(...(p.misconceptions.length ? p.misconceptions.map((m) => el("li", {},
    el("span", { class: `badge ${MISC_STATUS[m.status]}` }, m.status), " ", m.misconception,
    el("div", { class: "small muted" },
      `${m.concept} · seen ${m.count}× in ${m.sessions} session${m.sessions > 1 ? "s" : ""} · last ${fmtDay(m.last_seen)}`)))
    : [el("li", { class: "muted" }, "No misconceptions recorded.")]));

  $("pg-topics").replaceChildren(
    el("tr", {}, el("th", {}, "Topic"), el("th", {}, "Grade"), el("th", {}, "Sessions"), el("th", {}, "Ability (first → latest)"),
      el("th", {}, "Latest gaps"), el("th", {}, "Last active")),
    ...p.topics.map((t) => el("tr", {},
      el("td", {}, t.topic), el("td", {}, t.grade), el("td", {}, `${t.completed}/${t.sessions} completed`),
      el("td", {}, t.latest_ability == null ? "-" : t.completed > 1
        ? `${t.first_ability.toFixed(1)} → ${t.latest_ability.toFixed(1)}` : t.latest_ability.toFixed(1)),
      el("td", {}, t.latest_gaps.join(", ") || "-"), el("td", {}, fmtDay(t.last_activity)))));

  $("pg-breakdown").replaceChildren(
    ...rateRows("Single vs multi-concept", p.accuracy_by_dimension, DIMENSION_LABELS),
    ...rateRows("Word vs direct", p.accuracy_by_type, TYPE_LABELS),
    ...rateRows("Perspective", p.accuracy_by_perspective, PERSPECTIVE_LABELS),
  );
  const tm = p.timing;
  $("pg-timing").replaceChildren(
    el("p", {}, el("strong", {}, "Average time per problem: "), tm.avg_seconds != null ? fmtTime(tm.avg_seconds) : "-"),
    el("p", {}, el("strong", {}, "Share of time limit used: "), tm.avg_fraction_of_limit != null ? `${Math.round(tm.avg_fraction_of_limit * 100)}%` : "-"),
    el("p", {}, el("strong", {}, "Ran out of time: "), `${tm.timed_out} problem${tm.timed_out === 1 ? "" : "s"}`));
  show("progress");
}

function renderCoachSummary(p) {
  const saved = p.coach_summary;
  const btn = $("pg-summary-btn");
  btn.textContent = saved ? "Refresh" : "Generate";
  btn.disabled = !p.evidence_count;
  $("pg-summary-status").textContent = !saved
    ? "A coach-style overview of growth, persistent gaps and a practice plan, written by the AI from all saved sessions."
    : `Written ${fmtDate(saved.generated_at)} by ${saved.llm.provider} / ${saved.llm.model}`
      + (p.coach_summary_stale ? ". New results since then: refresh to include them." : ".");
  if (!saved) { $("pg-summary").replaceChildren(); return; }
  const c = saved.summary;
  const section = (title, body) => body && (!Array.isArray(body) || body.length) ? [el("h4", {}, title),
    Array.isArray(body) ? el("ul", {}, body.map((b) => el("li", {}, b))) : el("p", {}, body)] : [];
  $("pg-summary").replaceChildren(
    el("p", {}, c.overall_progress),
    ...section("Growth highlights", c.growth_highlights),
    ...section("Persistent gaps", c.persistent_gaps),
    ...section("Misconception patterns", c.misconception_patterns),
    ...section("Learning habits", c.learning_habits),
    el("h4", {}, "Practice plan"),
    el("ol", { class: "plan" }, c.practice_plan.map((x) => el("li", {},
      el("strong", {}, x.focus), `: ${x.why}`, el("div", { class: "small muted" }, `Try: ${x.activity}`)))),
    el("p", {}, el("em", {}, c.encouragement)));
}

async function generateCoachSummary() {
  const btn = $("pg-summary-btn");
  btn.disabled = true;
  $("pg-summary-status").textContent = "Writing the coach summary from all saved sessions…";
  try {
    const p = await api(`/api/students/${enc(state.student)}/progress/summary`, { method: "POST" });
    renderCoachSummary(p);
  } catch (e) {
    showError(e.message);
    btn.disabled = false;
  }
}

// Single-series line chart: ability (1-10) per completed session, with a grade-level reference,
// crosshair + tooltip on hover/keyboard, and a table view beside it for accessibility.
function renderAbilityChart(container, points) {
  container.replaceChildren();
  if (!points.length) {
    container.append(el("div", { class: "chart-empty" }, "Complete a session to start the trend."));
    return;
  }
  const W = 640, H = 240, m = { top: 16, right: 44, bottom: 30, left: 32 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const x = (i) => m.left + (points.length === 1 ? iw / 2 : (i * iw) / (points.length - 1));
  const y = (v) => m.top + ih - (Math.max(0, Math.min(10, v)) / 10) * ih;

  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", tabindex: "0",
    "aria-label": `Ability over ${points.length} sessions, latest ${points[points.length - 1].ability.toFixed(1)} out of 10` });
  for (let v = 0; v <= 10; v += 2) {
    root.append(svg("line", { class: "chart-grid", x1: m.left, x2: m.left + iw, y1: y(v), y2: y(v) }));
    root.append(svg("text", { class: "chart-axis-label", x: m.left - 8, y: y(v) + 4, "text-anchor": "end" }, v));
  }
  root.append(svg("line", { class: "chart-ref", x1: m.left, x2: m.left + iw, y1: y(GRADE_LEVEL), y2: y(GRADE_LEVEL) }));
  root.append(svg("text", { class: "chart-ref-label", x: m.left + iw + 6, y: y(GRADE_LEVEL) + 4 }, "grade"));

  // Label by date, or by session number when several sessions share a day.
  const step = Math.max(1, Math.ceil(points.length / 8));
  const days = points.map((p) => fmtDay(p.date));
  const label = new Set(days).size === days.length ? (i) => days[i] : (i) => `#${i + 1}`;
  points.forEach((_, i) => {
    if (i % step === 0 || i === points.length - 1) {
      root.append(svg("text", { class: "chart-axis-label", x: x(i), y: H - 8, "text-anchor": "middle" }, label(i)));
    }
  });

  if (points.length > 1) {
    const d = points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.ability)}`).join(" ");
    root.append(svg("path", { class: "chart-area", d: `${d} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z` }));
    root.append(svg("path", { class: "chart-line", d }));
  }
  const crosshair = svg("line", { class: "chart-crosshair", y1: m.top, y2: m.top + ih, visibility: "hidden" });
  root.append(crosshair);
  const dots = points.map((p, i) => {
    const dot = svg("circle", { class: "chart-dot", cx: x(i), cy: y(p.ability), r: 4 });
    root.append(dot);
    return dot;
  });
  const last = points[points.length - 1];
  root.append(svg("text", { class: "chart-end-label", x: x(points.length - 1) + 9, y: y(last.ability) - 8 }, last.ability.toFixed(1)));

  const hit = svg("rect", { x: m.left - 12, y: m.top, width: iw + 24, height: ih, fill: "transparent" });
  root.append(hit);
  container.append(root);
  const tip = el("div", { class: "chart-tooltip hidden" });
  container.append(tip);

  let active = -1;
  const focusPoint = (i) => {
    if (active >= 0) dots[active].setAttribute("r", 4);
    active = i;
    const p = points[i];
    dots[i].setAttribute("r", 6);
    crosshair.setAttribute("x1", x(i));
    crosshair.setAttribute("x2", x(i));
    crosshair.setAttribute("visibility", "visible");
    tip.replaceChildren(el("strong", {}, `${p.ability.toFixed(1)}/10`), ` · ${p.score}/${p.answered} correct`,
      el("br"), `${p.topic} (grade ${p.grade})`, el("br"), fmtDate(p.date));
    const scale = root.getBoundingClientRect().width / W;
    tip.style.left = `${x(i) * scale}px`;
    tip.style.top = `${y(p.ability) * scale}px`;
    tip.classList.remove("hidden");
  };
  const clear = () => {
    if (active >= 0) dots[active].setAttribute("r", 4);
    active = -1;
    crosshair.setAttribute("visibility", "hidden");
    tip.classList.add("hidden");
  };
  hit.addEventListener("pointermove", (e) => {
    const box = root.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    points.forEach((_, i) => { if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i; });
    if (best !== active) focusPoint(best);
  });
  hit.addEventListener("pointerleave", clear);
  root.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") focusPoint(Math.min(points.length - 1, active + 1));
    else if (e.key === "ArrowLeft") focusPoint(Math.max(0, active < 0 ? points.length - 1 : active - 1));
    else return;
    e.preventDefault();
  });
  root.addEventListener("blur", clear);
}

// ---------------------------------------------------------------- init
$("student-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = $("student-name").value.trim();
  if (name) openStudent(name);
});
$("new-session-form").addEventListener("submit", startSession);
$("grade").addEventListener("change", loadTopics);
$("all-grades").addEventListener("change", loadTopics);
$("topic-select").addEventListener("change", onTopicChange);
["num-questions", "session-time", "reattempts"].forEach((id) => $(id).addEventListener("change", updateSessionPlan));
$("custom-time").addEventListener("input", updateSessionPlan);
updateSessionPlan();
$("answer-form").addEventListener("submit", submitAnswer);
$("save-exit-btn").addEventListener("click", () => openStudent(state.student));
$("fb-exit-btn").addEventListener("click", () => openStudent(state.student));
$("home-link").addEventListener("click", showHome);
$("nav-dashboard").addEventListener("click", () => openStudent(state.student));
$("r-back").addEventListener("click", () => openStudent(state.student));
$("pg-back").addEventListener("click", () => openStudent(state.student));
$("progress-btn").addEventListener("click", openProgress);
$("pg-summary-btn").addEventListener("click", generateCoachSummary);

api("/api/config").then((c) => {
  $("llm-info").textContent = `Model: ${c.provider} / ${c.model}` + (c.connection ? ` · via ${c.connection}` : "");
}).catch(() => {});
const savedName = localStorage.getItem("studentName");
if (savedName) $("student-name").value = savedName;
showHome();
