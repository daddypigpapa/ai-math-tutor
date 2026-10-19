"use strict";
/* 공통 도우미(h, api, setMath)와 아이 학습 화면. 부모 화면(parent.js)도 이 파일의 도우미를 쓴다. */

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(path, body, opts = {}) {
  const init = { method: body === undefined ? "GET" : "POST", headers: { "Content-Type": "application/json", ...(opts.headers || {}) } };
  if (body !== undefined) init.body = JSON.stringify(body);
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch (_) { /* 본문 없음 */ }
  if (!r.ok) { const e = new Error((data && data.detail) || r.statusText); e.status = r.status; throw e; }
  return data;
}

/* ---- 수식 표시 (KaTeX, 로컬 복사본) ---------------------------------- */
function toTex(s) {
  return s.replace(/·/g, "\\cdot ").replace(/−/g, "-").replace(/²/g, "^{2}").replace(/³/g, "^{3}")
    .replace(/\^(\d{2,})/g, "^{$1}").replace(/\^\(([^)]+)\)/g, "^{$1}").replace(/sqrt\(([^)]+)\)/g, "\\sqrt{$1}");
}
function mathNode(tex) {
  const span = document.createElement("span");
  try {
    if (!window.katex) throw new Error("katex 없음");
    span.innerHTML = window.katex.renderToString(toTex(tex), { throwOnError: false, strict: false });
  } catch (_) { span.textContent = tex; }
  return span;
}
/* $...$ 가 있으면 그 부분만 수식, 한글이 있으면 문장, 아니면 전체를 수식으로 그린다 */
function setMath(el, text) {
  el.textContent = "";
  text = String(text);
  if (text.includes("$")) {
    text.split(/(\$[^$]+\$)/).forEach(seg => {
      if (!seg) return;
      if (seg.startsWith("$") && seg.endsWith("$") && seg.length > 2) el.append(mathNode(seg.slice(1, -1)));
      else el.append(document.createTextNode(seg));
    });
  } else if (/[가-힣]/.test(text)) el.textContent = text;
  else el.append(mathNode(text));
  return el;
}
const math = (text, tag = "span", cls = "") => setMath(h(tag, { class: cls }), text);

/* ---- 아이 학습 화면 ----------------------------------------------------- */
const App = typeof document !== "undefined" && document.getElementById("app");
const S = { learner: null, learnerName: "", unit: null, mode: "normal", aid: null, goal: null };

function show(...nodes) { App.textContent = ""; App.append(...nodes.filter(Boolean)); window.scrollTo(0, 0); }
function fail(e) { show(h("div", { class: "card bad" }, "문제가 생겼어: " + e.message, h("button", { class: "btn primary", onclick: pickScreen }, "처음으로"))); }

async function pickScreen() {
  try {
    const list = await api("/api/learners");
    if (!list.length) return show(h("h1", {}, "AI 가정교사"), h("div", { class: "card" }, "아직 등록된 사람이 없어. 부모님께 부탁해서 등록해줘."));
    show(h("h1", {}, "누가 공부할까?"),
      ...list.map(l => h("button", { class: "btn primary", onclick: () => { S.learner = l.id; S.learnerName = l.name; homeScreen(); } }, l.name)));
  } catch (e) { fail(e); }
}

function goalBar(g) {
  const pct = Math.min(100, Math.round((g.done / g.target) * 100));
  return h("div", {}, h("div", { class: "top" }, h("span", { class: "sub" }, `오늘 목표 ${g.done} / ${g.target}문제`),
    g.done >= g.target ? h("span", { class: "tag ok" }, "목표 달성!") : null),
    h("div", { class: "bar" + (g.done >= g.target ? " done" : "") }, h("i", { style: `width:${pct}%` })));
}

async function homeScreen() {
  try {
    const d = await api("/api/home?learner=" + S.learner);
    S.goal = d.goal;
    show(h("div", { class: "top" }, h("h1", {}, `안녕, ${d.learner.name}!`), h("button", { class: "link", onclick: pickScreen }, "다른 사람")),
      h("div", { class: "card" }, goalBar(d.goal)),
      d.review ? h("button", { class: "btn primary", onclick: () => startProblem("review", null) }, "약한 부분 복습하기") : null,
      ...d.units.map(u => h("button", { class: "btn", disabled: !u.total, onclick: () => startProblem("normal", u.code) },
        h("div", {}, u.name), h("div", { class: "sub" }, u.total ? `${u.solved} / ${u.total}문제 풀었어` + (u.next_number ? ` · 이어서 ${u.next_number}번` : " · 다 풀었어!") : "준비 중"))));
  } catch (e) { fail(e); }
}

async function startProblem(mode, unit) {
  S.mode = mode; S.unit = unit;
  try {
    const r = await api("/api/attempts", { learner: S.learner, mode, unit });
    if (r.done) return show(h("div", { class: "card good" }, "이 단원의 문제를 모두 풀었어. 대단해!"), h("button", { class: "btn primary", onclick: homeScreen }, "처음으로"));
    S.aid = r.attempt_id; S.goal = r.goal;
    problemScreen(r);
  } catch (e) { fail(e); }
}

function problemScreen(r) {
  const aid = S.aid;
  const goalBox = h("div", { class: "card" });
  const setGoal = g => { S.goal = g; goalBox.textContent = ""; goalBox.append(goalBar(g)); };
  setGoal(S.goal);
  const helpBox = h("div"), choicesBox = h("div"), coachBox = h("div"), resultBox = h("div");
  const cards = {};
  const putCard = (key, title, content) => {
    if (!cards[key]) { cards[key] = h("div", { class: "card help" }); helpBox.append(cards[key]); }
    cards[key].textContent = ""; cards[key].append(h("div", { class: "sub" }, title), content);
    return cards[key];
  };
  const run = async fn => { try { await fn(); } catch (e) { fail(e); } };

  const btnA = h("button", { class: "btn", onclick: () => run(async () => { const x = await api(`/api/attempts/${aid}/hint`, {}); putCard("hint", "힌트", math(x.hint, "div")); }) }, "a. 너무 어려워");
  const btnB = h("button", { class: "btn", onclick: () => run(async () => { const x = await api(`/api/attempts/${aid}/intent`, {}); putCard("intent", "출제의도", math(x.intent, "div")); }) }, "b. 출제의도가 뭐야?");
  const btnC = h("button", { class: "btn", onclick: () => run(async () => { const x = await api(`/api/attempts/${aid}/steps`, {}); putCard("steps", "계산단계", stepsView(aid, x.steps)); }) }, "c. 계산단계 보기");
  const btnD = h("button", { class: "btn primary", onclick: () => run(showChoices) }, "d. 답을 알 것 같아");
  const actions = h("div", { class: "row" }, btnA, btnB, btnC, btnD);

  async function showChoices() {
    coachBox.textContent = "";
    const c = await api(`/api/attempts/${aid}/choices`, {});
    btnD.disabled = true;
    choicesBox.textContent = "";
    if (c.retry) choicesBox.append(h("div", { class: "sub" }, "틀린 보기는 빼줬어. 다시 골라봐!"));
    c.choices.forEach(v => {
      const b = h("button", { class: "btn choice", onclick: () => run(() => submit(b, v)) });
      setMath(b, v);
      choicesBox.append(b);
    });
  }

  async function submit(btn, value) {
    choicesBox.querySelectorAll("button").forEach(b => (b.disabled = true));
    const out = await api(`/api/attempts/${aid}/answer`, { value });
    if (out.correct) {
      btn.classList.add("right");
      setGoal(out.goal);
      return finish(h("div", { class: "card good" }, h("div", {}, out.praise), h("div", { class: "sub" }, `${out.score}점`)));
    }
    btn.classList.add("wrong", "shake");
    if (out.finished) {
      setGoal(out.goal);
      const box = h("div", { class: "card bad" }, h("div", {}, `이번엔 0점이야. 정답은 `, math(out.answer), ` 였어.`), h("div", { class: "sub" }, "계산단계를 천천히 따라가 보자."),
        stepsView(aid, out.steps, false));
      return finish(box);
    }
    choicesBox.textContent = "";
    coachBox.textContent = "";
    const rating = out.ai_call_id ? ratingRow(out.ai_call_id) : null;
    coachBox.append(h("div", { class: "card bad" }, h("div", {}, out.coaching.text), rating),
      h("button", { class: "btn primary", onclick: () => run(showChoices) }, "다시 풀어볼게"));
  }

  function finish(box) {
    actions.style.display = "none"; choicesBox.textContent = ""; coachBox.textContent = "";
    resultBox.textContent = "";
    resultBox.append(box, h("button", { class: "btn primary", onclick: () => startProblem(S.mode, S.unit) }, "다음 문제"));
  }

  show(h("div", { class: "top" }, h("button", { class: "link", onclick: homeScreen }, "← 처음으로"),
      h("span", { class: "sub" }, (S.mode === "review" ? "복습 " : "") + `${r.problem.number} / ${r.problem.total}번`)),
    goalBox, h("div", { class: "card" }, math(r.problem.question, "div", "q")), helpBox, actions, choicesBox, coachBox, resultBox);
}

function stepsView(aid, steps, helpable = true) {
  const wrap = h("div");
  steps.forEach(s => {
    const helpArea = h("div");
    const row = h("div", { class: "step" }, h("div", {}, `${s.no}. `, math(s.text)), helpArea);
    const ask = (level) => async () => {
      try {
        const x = await api(`/api/attempts/${aid}/step-help`, { step: s.no, level });
        helpArea.textContent = "";
        const more = x.can_more ? h("button", { class: "btn small", onclick: ask(x.level + 1) }, "한 번 더 쉽게") : null;
        helpArea.append(h("div", { class: "card help" }, h("div", {}, x.text),
          h("button", { class: "btn small", onclick: () => (helpArea.textContent = "") }, "이해했어"), more,
          x.ai_call_id ? ratingRow(x.ai_call_id) : null));
      } catch (e) { helpArea.textContent = e.message; }
    };
    if (helpable) row.append(h("button", { class: "btn small", onclick: ask(1) }, "이 단계가 이해 안 돼"));
    wrap.append(row);
  });
  return wrap;
}

function ratingRow(callId) {
  const row = h("div", { class: "sub" });
  const send = v => async () => { try { await api(`/api/ai-calls/${callId}/rating`, { helpful: v }); } catch (_) {} row.textContent = "고마워!"; };
  row.append("이 설명이 ", h("button", { class: "btn small", onclick: send(1) }, "도움됐어"), h("button", { class: "btn small", onclick: send(0) }, "별로야"));
  return row;
}

if (App) pickScreen();
