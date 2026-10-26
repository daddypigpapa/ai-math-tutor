"""Claude / Gemini 공통 호출과 ai_calls 기록 (명세 3·5-1·8절).

- API 키가 없으면 호출하지 않고 즉시 실패(no_api_key)를 돌려준다. 이 경우는 ai_calls 에 남기지 않는다.
- Claude: 구조화 출력(output_config.format=json_schema). 프리필·temperature 는 보내지 않는다.
- 프롬프트 캐시: 시스템 프롬프트에 cache_control 을 둔다.
"""
import base64
import json
import time
from dataclasses import dataclass

import ssl

import httpx

from . import config, db

try:   # Windows 인증서 저장소를 쓴다 (사내 프록시·보안 프로그램이 낀 PC에서 self-signed 체인 오류 방지)
    import truststore
    SSL_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except Exception:
    SSL_CONTEXT = True

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass
class Result:
    ok: bool
    text: str = ""
    data: dict | None = None
    fail_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    ms: int = 0
    call_id: int | None = None
    stop_reason: str = ""


def _key(t: dict) -> str:
    """작업의 key_env(config.toml) 가 있으면 그 이름, 없으면 제공자 기본 이름으로 key.env·.env·환경변수에서 읽는다."""
    return config.env(t.get("key_env") or ("ANTHROPIC_API_KEY" if t["provider"] == "claude" else "GEMINI_API_KEY"))


def _claude(t: dict, system: str, user: str, schema: dict | None, timeout: float, key: str, max_tokens: int,
            images: list):
    content = [{"type": "image", "source": {"type": "base64", "media_type": m, "data": base64.b64encode(b).decode()}}
               for m, b in images] + [{"type": "text", "text": user}] if images else user
    body = {
        "model": t["model"],
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": content}],
    }
    oc: dict = {}
    if schema:
        oc["format"] = {"type": "json_schema", "schema": schema}
    if t.get("effort"):
        oc["effort"] = t["effort"]
    if oc:
        body["output_config"] = oc
    r = httpx.post(
        ANTHROPIC_URL, json=body, timeout=timeout, verify=SSL_CONTEXT,
        headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
    u = j.get("usage", {})
    tin = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
    return text, tin, u.get("output_tokens", 0), j.get("stop_reason", "")


def _gemini(t: dict, system: str, user: str, schema: dict | None, timeout: float, key: str, max_tokens: int,
            images: list):
    gen: dict = {"maxOutputTokens": max_tokens}
    if schema:
        gen["responseMimeType"] = "application/json"
    if "thinking_budget" in t:
        gen["thinkingConfig"] = {"thinkingBudget": t["thinking_budget"]}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": m, "data": base64.b64encode(b).decode()}} for m, b in images] + [{"text": user}]}],
        "generationConfig": gen,
    }
    r = httpx.post(
        GEMINI_URL.format(model=t["model"]), json=body, timeout=timeout, verify=SSL_CONTEXT,
        headers={"x-goog-api-key": key, "content-type": "application/json"},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    cands = j.get("candidates") or []
    parts = (cands[0].get("content", {}).get("parts", []) if cands else [])
    text = "".join(p.get("text", "") for p in parts)
    u = j.get("usageMetadata", {})
    stop = (cands[0].get("finishReason", "") if cands else "NO_CANDIDATE")
    return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0), stop


def call(conn, task: str, system: str, user: str, *, schema: dict | None = None,
         learner_id: int | None = None, attempt_id: int | None = None, problem_id: str | None = None,
         timeout: float = 60.0, prompt_version: str = "", max_tokens: int | None = None,
         images: list[tuple[str, bytes]] | None = None) -> Result:
    """task 이름으로 config.toml 의 제공자·모델을 골라 호출하고 ai_calls 에 기록한다. images: [(mime, 바이트)]."""
    t = dict(config.task(task))
    provider = t["provider"]
    if t.get("model_env") and config.env(t["model_env"]):   # key.env 의 모델 값이 config.toml 의 model 보다 우선
        t["model"] = config.env(t["model_env"])
    key = _key(t)
    if not key:
        return Result(False, fail_reason="no_api_key")
    if not t.get("model"):
        return Result(False, fail_reason="no_model")
    prompt_version = prompt_version or t.get("prompt", "")
    mt = max_tokens or t.get("max_tokens", 1000)
    start = time.time()
    res = Result(False)
    try:
        fn = _claude if provider == "claude" else _gemini
        try:
            text, tin, tout, stop = fn(t, system, user, schema, timeout, key, mt, images or [])
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            if timeout < 30:          # 실시간 코칭(5초 제한)은 재시도하지 않고 바로 저장 문장으로 대체
                raise
            time.sleep(1.5)           # 생성·보고서 같은 느린 호출은 연결이 끊기면 한 번 더 시도
            text, tin, tout, stop = fn(t, system, user, schema, timeout, key, mt, images or [])
        res = Result(True, text=text, input_tokens=tin, output_tokens=tout, stop_reason=stop)
        if provider == "claude" and stop == "refusal":
            res.ok, res.fail_reason = False, "refusal"
        elif not text.strip():
            res.ok, res.fail_reason = False, f"empty({stop})"
        elif schema and res.ok:
            try:
                res.data = json.loads(text)
            except json.JSONDecodeError:
                res.ok, res.fail_reason = False, f"bad_json({stop})"
    except httpx.TimeoutException:
        res.fail_reason = "timeout"
    except Exception as e:  # 네트워크·HTTP 오류는 호출자가 대체 문장을 쓰도록 실패로만 알린다
        res.fail_reason = f"error: {type(e).__name__}: {str(e)[:200]}"
    res.ms = int((time.time() - start) * 1000)

    cur = conn.execute(
        "INSERT INTO ai_calls(learner_id,attempt_id,problem_id,purpose,provider,model,prompt_version,prompt,response,"
        "input_tokens,output_tokens,ms,ok,fail_reason,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (learner_id, attempt_id, problem_id, task, provider, t["model"], prompt_version,
         f"[시스템]\n{system}\n\n[사용자]\n{user}" + (f"\n[이미지 {len(images)}장]" if images else ""), res.text, res.input_tokens, res.output_tokens, res.ms,
         1 if res.ok else 0, res.fail_reason or None, db.now()),
    )
    conn.commit()
    res.call_id = cur.lastrowid
    return res


def mark_failed(conn, call_id: int | None, reason: str) -> None:
    """응답은 왔지만 후속 검사(누설·길이 등)에서 탈락한 호출을 실패로 고쳐 적는다."""
    if call_id is None:
        return
    conn.execute("UPDATE ai_calls SET ok=0, fail_reason=? WHERE id=?", (reason, call_id))
    conn.commit()


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = config.cfg().get("pricing", {}).get(model)
    if not p:
        return None
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000
