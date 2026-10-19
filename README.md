# AI 가정교사 (1차)

중3 학습자가 문제마다 정해진 선택지(a 너무 어려워 / b 출제의도 / c 계산단계 / d 답을 알 것 같아)로 AI와 대화하며 푸는 학습 앱.
서버 PC가 자료를 만들고 웹으로 제공하며, 아이 PC는 브라우저로 접속만 한다.
구현 근거: 「AI가정교사 구현명세 (1차)」 (claude.ai 문서 `6LYu4JzQHVqXGoYk4MnYee`, 2026-10-19).

> **상태: 1차 검증 완료(일부 미달·미검증 있음).** 결과는 [docs/검증_결과.md](docs/검증_결과.md), 절차는 [docs/검증_체크리스트.md](docs/검증_체크리스트.md).
> 미달: 생성 오답 타당성 69~72%(기준 90%). 미검증: 디스코드 전송, 작업 스케줄러·방화벽·Tailscale.

## 설치·실행 (서버 PC, Windows PowerShell 5.1)

```powershell
cd C:\Users\USER\Claude\ai-math-tutor
.\scripts\setup.ps1 -Learner "아이이름"     # venv, 패키지, KaTeX, DB, 시작 문제 12개
notepad .env                                 # ANTHROPIC_API_KEY, GEMINI_API_KEY, DISCORD_WEBHOOK_URL, PARENT_PIN
.\scripts\run_server.ps1                     # http://localhost:8000  (부모 화면: /parent)
```

- 방화벽(관리자): `.\scripts\open_firewall.ps1` — 개인 네트워크 8000번만 연다. 아이 PC는 `http://<서버 PC IP>:8000`.
- 부팅 자동 실행 + 일요일 21시 주간 보고서(관리자): `.\scripts\register_tasks.ps1`
- 집 밖 접속은 Tailscale만 쓴다(인터넷에 직접 공개하지 않는다): 서버 PC와 아이 기기에 설치 후 `http://<Tailscale 이름>:8000`.
- API 키가 없어도 시작 문제 12개로 학습·채점·부모 화면이 동작한다(AI 코칭은 저장된 피드백으로 대체).

## 구성 (명세 11절 폴더 구조와 같음)

```
app/       main.py(라우트) db.py learn.py(흐름·채점) stats.py(정답률·약점·복습) coach.py(실시간 코칭)
           report.py(주간 보고서·디스코드) llm.py(Claude/Gemini 호출·ai_calls) parent.py(부모 API)
           problems.py(문제 JSON·색인) taxonomy.py(단원·실수유형 사전) prompts.py config.py
pipeline/  generate.py(자료 생성·AI 문제·복습 준비) verify.py(sympy 검증) run.py(CLI) seed.py(시작 문제)
prompts/   generate_v1 repair_v1 newproblem_v1 convert_v1 coach_v1 step_help_v1 report_v1 judge_*_v1 + examples/
web/       learn.html parent.html app.js parent.js style.css katex/(로컬 복사본)
seed/      손으로 쓴 시작 문제 12개 (전개 7 + 인수분해 5)  →  problems/ 로 검증 후 반영
problems/  검증 통과한 문제 JSON (시작 12개 + AI가 만든 인수분해 5개 f01~f05)
tests/     단위·API 테스트(pytest) + eval/(평가 세트 36개, 채점 스크립트)
config.toml  점수·임계값·하루 목표·작업별 AI 제공자/모델·단가·일일 한도·보고서 설정
```

## 자주 쓰는 명령

```powershell
.\.venv\Scripts\python.exe -m pipeline.run process            # input/*.json (문제집 문제) → problems/
.\.venv\Scripts\python.exe -m pipeline.run generate --count 5 # AI가 새 문제 생성·검증
.\.venv\Scripts\python.exe -m pipeline.run reindex            # problems/ → DB 색인
.\.venv\Scripts\python.exe -m app.report --learner all --no-send
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m tests.eval.run_eval --judge     # 생성 평가 (API 키 필요)
.\.venv\Scripts\python.exe -m tests.eval.score_coaching --judge
```

`input/*.json` 형식 (문제집 문제):
```json
[{"unit": "m3-poly", "no": 1, "answer_form": "expanded",
  "question": "다음 식을 전개하시오. $(x+3)(x-5)$", "latex": "(x+3)(x-5)", "sympy": "(x+3)*(x-5)"}]
```
`sympy` 를 생략하면 Claude(가벼운 모델)가 `latex` 를 sympy 식으로 변환한다.

## 명세와 다르게 한 곳 / 명세에 없어 정한 곳 (가정)

1. **스키마 추가 2건**: `review_sessions` 표와 `attempts.review_session_id` 열 — 복습 1회(10문제)의 진행 상태를 보관하려고 넣었다(10절 표는 그대로 유지).
2. **문제 id**: 문제집 문제 `<단원>-<번호4자리>`, AI 문제 `<단원>-a<번호>`, 시작 문제 `m3-poly-sNN`(source=`ai`, prompt_version=`seed-v1`).
3. **재시도 화면**: 코칭의 "다시 풀어볼게" → 같은 문제 화면에서 틀린 보기를 뺀 3지선다를 바로 보여주고, a·b·c 버튼은 그대로 쓸 수 있다.
4. **"도움 받아 맞힘"** = 계산단계(c)를 본 뒤 맞힌(첫 시도/재시도) 문제. 재시도로 맞힌 문제(c 안 봄)는 정답률에서 오답이지만 이 수에는 넣지 않는다.
5. **하루 목표 집계**: 그날 끝낸 풀이 전부(정답/오답, 일반/복습). **학습 시간**: 풀이당 상한 600초(`parent.time_cap_s`)로 자리 비움 보정.
6. **약점 복습 준비**: 생성이 일부/전부 실패해도 기존 문제로 복습 가능하면(`review_ready=1`) 버튼을 띄운다. 단원 약점은 그 단원 전반, 실수유형 약점은 그 유형(`focus_error_type`)을 노려 생성.
7. **일일 호출 한도**: 실제 호출 시도(실패·누설 탈락 포함)를 센다. 캐시 적중·키 없음·한도 초과는 세지 않는다.
8. **반말 검사**: 문장 끝이 `요/습니다/입니다/세요/까요` 이면 존댓말로 보고 코칭을 버린다(휴리스틱).
9. **부모 PIN**: `.env` 의 `PARENT_PIN`, 비어 있으면 부모 화면 잠김. 틀리면 1초 지연.
10. **모델 이름·단가**: `config.toml` 의 값은 예시다(`claude-sonnet-5-5`, `claude-haiku-5-5`, `gemini-2.5-flash-lite`). 실제 사용 가능한 이름·요금으로 고칠 것.
11. 시작 문제는 `~/claude/key.env` → 프로젝트 `.env` → 환경변수 순으로 키를 읽는다(전역 규약).

## 1차에서 하지 않은 것 (명세 1·13절)

도형·그래프 문제, 문제집 스캔/PDF 전처리(13절 세부과제), 12절 6단계(문제 확대·단원 추가).
집 밖 접속(Tailscale)·학습자 추가는 구조만 있다: 학습자는 부모 화면 설정 또는 `learners` 한 줄, 첫 화면은 이름 버튼.

## 남은 일 (명세 13절 "남은 일")

- 보고서를 받을 디스코드 채널 정하고 웹후크 주소를 `.env` 에 넣기
- 정답률 구간 문장(`config.toml [report.tiers]`) 확정
- 문제집 목차와 첫 단원 문제 30개 준비 → `input/` (평가 세트는 `tests/eval/eval_problems.json` 에 임시로 36개 있음)
