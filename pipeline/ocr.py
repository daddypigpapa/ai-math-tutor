"""문제집 사진 → input/ocr-<사진이름>.json (명세 13절 세부과제: 스캔 전처리).

OCR 오독은 sympy 검증이 잡지 못한다(식이 바뀌어도 '맞는 문제'가 된다). 그래서 이 단계는 input/ 에 JSON 만 쓰고,
부모가 원본 사진과 대조한 뒤 `pipeline.run process` 로 자료를 만든다. sympy 변환은 process 의 convert 단계가 한다.
"""
import json
import mimetypes
from pathlib import Path

from app import config, llm, prompts, taxonomy

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["label", "instruction", "latex", "answer_form", "skip_reason"],
        "properties": {"label": {"type": "string"}, "instruction": {"type": "string"}, "latex": {"type": "string"},
                       "answer_form": {"type": "string", "enum": [*taxonomy.ANSWER_FORMS, "skip"]},
                       "skip_reason": {"type": "string"}}}}},
}


def image_paths(paths: list[str]) -> list[Path]:
    out = []
    for p in map(Path, paths):
        out += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT) if p.is_dir() else [p]
    return out


def _next_no(conn, unit: str) -> int:
    """input/ 의 번호와 DB 의 문제집 문제 번호 중 가장 큰 값 + 1 (input 파일을 지워도 id 가 겹치지 않게)."""
    nos = [0, conn.execute("SELECT MAX(seq) FROM problems WHERE unit=? AND source='workbook'", (unit,)).fetchone()[0] or 0]
    for f in config.input_dir().glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        nos += [int(r["no"]) for r in (data if isinstance(data, list) else [data]) if r.get("unit") == unit]
    return max(nos) + 1


def ocr_image(conn, path: Path, unit: str) -> tuple[list[dict], list[dict]] | None:
    """사진 한 장 → (쓸 항목, 건너뛴 항목). 호출 실패면 None."""
    system, user_tpl = prompts.load(config.task("ocr")["prompt"])
    mime = mimetypes.guess_type(path.name)[0] or "image/heic"
    r = llm.call(conn, "ocr", system, prompts.fill(user_tpl, unit_name=taxonomy.unit_name(unit)), schema=SCHEMA,
                 images=[(mime, path.read_bytes())], timeout=180)
    if not r.ok:
        return None
    keep, skipped = [], []
    for it in r.data.get("items", []):
        latex = (it.get("latex") or "").strip()
        if it.get("answer_form") in taxonomy.ANSWER_FORMS and latex and "[?]" not in latex:
            keep.append({"label": it.get("label", ""), "answer_form": it["answer_form"], "latex": latex,
                         "question": f'{it.get("instruction", "").strip()} ${latex}$'.strip()})
        else:
            skipped.append({"label": it.get("label", ""), "reason": it.get("skip_reason") or "형식 불일치"})
    return keep, skipped


def run(conn, paths: list[str], unit: str, log=print) -> list[Path]:
    written = []
    config.input_dir().mkdir(parents=True, exist_ok=True)
    for img in image_paths(paths):
        dest = config.input_dir() / f"ocr-{img.stem}.json"
        if dest.exists():
            log(f"  [{img.name}] {dest.name} 이미 있음 — 건너뜀 (다시 하려면 그 파일을 지운다)")
            continue
        res = ocr_image(conn, img, unit)
        if res is None:
            log(f"  [{img.name}] OCR 호출 실패 (ai_calls 의 fail_reason 확인)")
            continue
        keep, skipped = res
        for s in skipped:
            log(f"  [{img.name}] 건너뜀 {s['label']}: {s['reason']}")
        if not keep:
            log(f"  [{img.name}] 쓸 문제 없음")
            continue
        no = _next_no(conn, unit)
        items = [{"unit": unit, "no": no + i, "image": img.name, **k} for i, k in enumerate(keep)]
        dest.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"  [{img.name}] → {dest.name} ({len(items)}문제, no {no}~{no + len(items) - 1})")
        written.append(dest)
    return written
