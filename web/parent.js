"use strict";
/* 부모 화면 (명세 9절): 표와 막대 정도의 단순한 화면. h()/api() 는 app.js 에서 온다. */

const root = document.getElementById("root");
const P = { pin: sessionStorage.getItem("pin") || "", learner: null, learners: [], tab: "summary" };

const papi = (path, body) => api("/api/parent" + path, body, { headers: { "X-Parent-Pin": P.pin } });

function table(headers, rows) {
  return h("table", {}, h("tr", {}, headers.map(x => h("th", {}, x))), rows.map(r => h("tr", {}, r.map(c => h("td", {}, c == null ? "" : c)))));
}
const pctBar = v => v == null ? "-" : h("div", { style: "min-width:120px" }, `${v}%`, h("div", { class: "bar" }, h("i", { style: `width:${v}%` })));
const tag = (t, cls = "") => h("span", { class: "tag " + cls }, t);

function loginScreen(msg) {
  const inp = h("input", { type: "password", placeholder: "PIN", autofocus: "autofocus" });
  const go = async () => {
    P.pin = inp.value;
    try { await papi("/ping"); sessionStorage.setItem("pin", P.pin); await boot(); }
    catch (e) { loginScreen(e.message); }
  };
  inp.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  root.textContent = "";
  root.append(h("h1", {}, "부모 화면"), h("div", { class: "card" }, inp, " ", h("button", { class: "btn small primary", onclick: go }, "들어가기"),
    msg ? h("div", { class: "err" }, msg) : null));
}

async function boot() {
  P.learners = await papi("/learners");
  if (!P.learner && P.learners.length) P.learner = P.learners[0].id;
  render();
}

const TABS = [["summary", "요약"], ["units", "단원별 정답률"], ["errors", "실수유형별 정답률"], ["attempts", "풀이 기록"], ["reports", "주간 보고서"],
  ["review", "검토 필요"], ["manage", "문제 관리"], ["calls", "AI 호출 기록"], ["costs", "AI 비용"], ["settings", "설정"]];

function render() {
  const sel = h("select", { onchange: e => { P.learner = Number(e.target.value); render(); } },
    P.learners.map(l => h("option", { value: l.id, selected: l.id === P.learner }, l.name)));
  const nav = h("nav", { class: "tabs" }, TABS.map(([k, t]) => h("button", { class: P.tab === k ? "on" : "", onclick: () => { P.tab = k; render(); } }, t)));
  const body = h("div", {}, "불러오는 중…");
  root.textContent = "";
  root.append(h("div", { class: "top" }, h("h1", {}, "부모 화면"), h("span", {}, "학습자 ", sel)), nav, body);
  VIEWS[P.tab](body).catch(e => { body.textContent = ""; body.append(h("div", { class: "err" }, e.message)); });
}

const VIEWS = {
  async summary(el) {
    if (!P.learner) { el.textContent = "먼저 설정에서 아이를 추가해 주세요."; return; }
    const s = await papi("/summary?learner=" + P.learner);
    const card = (t, d) => h("div", { class: "card" }, h("b", {}, t), h("div", {}, `푼 문제 ${d.solved}개 · 점수 ${d.score}점 · 학습 ${d.minutes}분`));
    el.textContent = "";
    el.append(card("오늘", s.today), card("이번 주(월~)", s.week), h("h2", {}, "하루 목표 달성 (최근 14일)"),
      table(["날짜", "푼 문제", "목표", "달성"], s.days.slice().reverse().map(d => [d.date, d.solved, d.target, d.achieved ? tag("달성", "ok") : tag("미달")])));
  },
  async units(el) {
    const u = await papi("/units?learner=" + P.learner);
    el.textContent = "";
    el.append(h("div", { class: "sub" }, "정답률 = 도움 없이 첫 시도에 맞힌 문제 ÷ 푼 문제 (최근 20문제). 계산단계를 본 뒤 맞힌 것은 오답으로 셉니다."),
      table(["단원", "푼 문제(최근 20)", "정답률", "도움 받아 맞힘", "약점"], u.map(x => [x.name, x.n, pctBar(x.rate_pct), x.helped, x.weak ? tag("약점", "weak") : ""])));
  },
  async errors(el) {
    const u = await papi("/error-types?learner=" + P.learner);
    el.textContent = "";
    el.append(h("div", { class: "sub" }, "정답률 = 그 오답이 선택지에 있었던 문제 중 그 오답을 고르지 않은 비율 (최근 20회 노출)."),
      table(["실수유형", "노출", "고름", "정답률", "최근10 → 이전10", "약점"],
        u.map(x => [x.name, x.n, x.picked, pctBar(x.rate_pct), x.recent_pct == null ? "-" : `${x.recent_pct}% ← ${x.previous_pct == null ? "-" : x.previous_pct + "%"}`, x.weak ? tag("약점", "weak") : ""])));
  },
  async attempts(el) {
    const a = await papi("/attempts?learner=" + P.learner + "&limit=100");
    el.textContent = "";
    el.append(h("div", { class: "sub" }, "고른 순서: a 힌트, b 출제의도, c 계산단계, d 답(번호 뒤 ○ 정답 / × 오답). c+ 는 단계 추가 설명."),
      table(["시각", "문제", "고른 순서", "결과", "점수", "소요(초)"],
        a.map(x => [x.ended_at, h("span", {}, x.mode === "review" ? "[복습] " : "", math(x.question)), x.sequence,
          ({ first: "첫 시도 정답", retry: "재시도 정답", wrong: "오답" })[x.result] + (x.used_steps ? " (c 봄)" : ""), x.score, x.seconds])));
  },
  async reports(el) {
    const r = await papi("/reports?learner=" + P.learner);
    const msg = h("div", { class: "sub" });
    const make = send => async () => {
      try { const x = await papi("/reports/generate", { learner: P.learner, send }); msg.textContent = send ? (x.discord_sent ? "디스코드로 보냈어요." : "디스코드 전송 실패: " + x.discord_error) : "만들었어요."; setTimeout(render, 800); }
      catch (e) { msg.textContent = e.message; }
    };
    el.textContent = "";
    el.append(h("div", {}, h("button", { class: "btn small", onclick: make(false) }, "이번 주 보고서 지금 만들기"),
      h("button", { class: "btn small", onclick: make(true) }, "만들고 디스코드로 보내기")), msg,
      r.length ? r.map(x => { const st = JSON.parse(x.stats_json || "{}");
        return h("div", { class: "card" }, h("b", {}, `${x.week_start} 주`), " ", x.discord_sent ? tag("디스코드 전송됨", "ok") : tag("디스코드 미전송" + (st.discord_error ? `: ${st.discord_error}` : ""), "weak"), h("pre", {}, x.body)); })
        : h("div", { class: "sub" }, "아직 보고서가 없어요. 매주 일요일 저녁에 자동으로 만들어져요."));
  },
  async review(el) {
    const q = await papi("/review-queue");
    el.textContent = "";
    if (!q.length) { el.append(h("div", { class: "card" }, "검토할 문제가 없어요.")); return; }
    q.forEach(item => {
      const ta = h("textarea", {});
      const msg = h("div", { class: "err" });
      papi("/problems/" + item.id).then(p => {
        const d = p.draft || {};
        ta.value = JSON.stringify({ hint: d.hint || p.hint || "", intent: d.intent || p.intent || "", steps: d.steps || p.steps || [], distractors: d.distractors || [] }, null, 2);
      });
      const act = (action, withEdits) => async () => {
        try {
          const x = await papi("/approve", { problem_id: item.id, action, edits: withEdits ? JSON.parse(ta.value) : null });
          if (x.ok === false) msg.textContent = "검증 실패: " + x.issues.map(i => `${i.code}(${i.field})`).join(", ");
          else render();
        } catch (e) { msg.textContent = e.message; }
      };
      el.append(h("div", { class: "card" }, h("b", {}, item.id), " ", tag(item.status, "weak"), " ", item.reason,
        h("div", {}, math(item.question)),
        item.issues.length ? h("pre", {}, item.issues.map(i => `${i.code}  ${i.field}  기대=${i.expected}  실제=${i.actual}`).join("\n")) : null,
        h("details", {}, h("summary", {}, "수정하기 (JSON)"), ta,
          h("button", { class: "btn small primary", onclick: act("approve", true) }, "수정 내용으로 승인")),
        h("button", { class: "btn small", onclick: act("discard", false) }, "폐기"), msg));
    });
  },
  async manage(el) {
    const [jobs, list] = await Promise.all([papi("/jobs"), papi("/problems")]);
    const f = {};
    const field = (k, label, attrs = {}) => { f[k] = h("input", attrs); return h("label", {}, label, " ", f[k]); };
    const msg = h("div", { class: "sub" });
    const form = isInput => h("div", { class: "card" }, h("b", {}, isInput ? "문제 입력 (문제집)" : "AI 문제 생성"), h("div", {},
      ...(isInput ? [field("no", "문제집 번호", { type: "number", value: 1 }), field("form", "답의 꼴(expanded/factored/number)", { value: "expanded" }),
        h("div", {}, field("question", "문제 (수식은 $...$)", { size: 50 })), h("div", {}, field("latex", "수식 LaTeX", { size: 40 }), field("sympy", "sympy(선택)", { size: 30 }))]
        : [field("count", "개수", { type: "number", value: 5 }), field("err", "노릴 실수유형(선택)", { placeholder: "SIGN" })]),
      h("button", { class: "btn small primary", onclick: async () => {
        try {
          if (isInput) await papi("/problems", { unit: "m3-poly", no: Number(f.no.value), question: f.question.value, latex: f.latex.value || null, sympy: f.sympy.value || null, answer_form: f.form.value });
          else await papi("/generate", { unit: "m3-poly", count: Number(f.count.value), error_type: f.err.value || null });
          msg.textContent = "요청했어요. 끝나면 아래 목록과 '검토 필요'에 나타나요.";
        } catch (e) { msg.textContent = e.message; }
      } }, "요청")));
    el.textContent = "";
    el.append(form(true), form(false), msg, h("h2", {}, "작업 상태"),
      table(["작업", "내용", "상태"], Object.entries(jobs).map(([k, v]) => [k, v.what, v.status + (v.error ? " " + v.error : "")])),
      h("h2", {}, "문제 목록 (ai_review = 약점 복습용 자동 준비)"),
      table(["id", "단원", "출처", "번호", "꼴", "상태", "사유"], list.map(x => [x.id, x.unit, x.source, x.seq, x.answer_form, x.status, x.status_reason])));
  },
  async calls(el) {
    const c = await papi("/ai-calls?limit=100");
    const detail = h("div");
    el.textContent = "";
    el.append(table(["id", "시각", "용도", "모델", "프롬프트", "토큰(입/출)", "ms", "결과", "평가", ""],
      c.map(x => [x.id, x.at, x.purpose, x.model, x.prompt_version, `${x.input_tokens}/${x.output_tokens}`, x.ms, x.ok ? "성공" : "실패: " + (x.fail_reason || ""),
        x.helpful == null ? "" : x.helpful ? "도움됨" : "별로",
        h("button", { class: "btn small", onclick: async () => { const d = await papi("/ai-calls/" + x.id); detail.textContent = ""; detail.append(h("h2", {}, `호출 #${d.id}`), h("pre", {}, d.prompt), h("pre", {}, d.response)); detail.scrollIntoView(); } }, "전문")])), detail);
  },
  async costs(el) {
    const c = await papi("/costs");
    el.textContent = "";
    el.append(h("div", { class: "sub" }, "토큰 수 × config.toml 의 단가(USD). 단가는 예시값이므로 실제 요금표로 고쳐 주세요."),
      table(["기간", "합계(USD)", "모델별", "작업별"], Object.entries(c).map(([k, v]) => [k, v.total_usd,
        Object.entries(v.by_model).map(([m, x]) => `${m}: ${x.calls}회, ${x.input}/${x.output}토큰, $${x.usd.toFixed(4)}`).join("\n"),
        Object.entries(v.by_purpose).map(([p, x]) => `${p}: $${x}`).join("\n")])));
  },
  async settings(el) {
    const L = await papi("/learners");
    const name = h("input", { placeholder: "이름" }), goal = h("input", { type: "number", value: 10, style: "width:80px" });
    const msg = h("div", { class: "sub" });
    el.textContent = "";
    el.append(h("h2", {}, "아이 목록·하루 목표"), table(["id", "이름", "학년", "하루 목표(문제)", ""], L.map(l => {
        const g = h("input", { type: "number", value: l.daily_goal, style: "width:80px" });
        return [l.id, l.name, l.grade, g, h("button", { class: "btn small", onclick: async () => { await papi(`/learners/${l.id}/goal`, { daily_goal: Number(g.value) }); msg.textContent = "저장했어요."; } }, "저장")];
      })),
      h("h2", {}, "아이 추가"), h("div", { class: "card" }, name, " 하루 목표 ", goal, " ",
        h("button", { class: "btn small primary", onclick: async () => { try { await papi("/learners", { name: name.value, daily_goal: Number(goal.value) }); P.learners = await papi("/learners"); render(); } catch (e) { msg.textContent = e.message; } } }, "추가")), msg);
  },
};

(P.pin ? boot().catch(() => loginScreen()) : Promise.resolve(loginScreen()));
