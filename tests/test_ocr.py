"""OCR 단계: 사진 → input/ocr-*.json. 맞는 형식만 쓰고, 번호는 기존 input 뒤에 잇고, 같은 사진은 다시 읽지 않는다."""
import json

from app import config
from pipeline import generate, ocr
from tests.conftest import ok

ITEMS = [
    {"label": "1 (1)", "instruction": "다음 식을 전개하여라.", "latex": "(x+3)(x-5)", "answer_form": "expanded",
     "skip_reason": ""},
    {"label": "1-2", "instruction": "다음 중 인수가 아닌 것은?", "latex": "", "answer_form": "skip", "skip_reason": "객관식"},
    {"label": "2", "instruction": "인수분해하여라.", "latex": "x^2-[?]", "answer_form": "factored", "skip_reason": ""},
]


def test_ocr_writes_input_json_after_existing_numbers(conn, fake, tmp_path):
    config.input_dir().mkdir(parents=True)
    (config.input_dir() / "a.json").write_text(json.dumps([{"unit": "m3-poly", "no": 7}]), encoding="utf-8")
    img = tmp_path / "page1.png"
    img.write_bytes(b"\x89PNG")
    fake.handlers["ocr"] = lambda s, u, kw: ok(data={"items": ITEMS})

    files = ocr.run(conn, [str(tmp_path)], "m3-poly", log=lambda *_: None)

    items = json.loads(files[0].read_text(encoding="utf-8"))
    assert files[0].name == "ocr-page1.json"
    assert [(i["no"], i["label"]) for i in items] == [(8, "1 (1)")]      # 객관식·판독 불가는 빠짐
    assert items[0]["question"] == "다음 식을 전개하여라. $(x+3)(x-5)$"
    assert fake.calls[0][3]["images"] == [("image/png", b"\x89PNG")]
    assert generate.item_from_input(items[0])["id"] == "m3-poly-0008"     # process 가 그대로 읽는다

    assert ocr.run(conn, [str(img)], "m3-poly", log=lambda *_: None) == []   # 이미 있으면 다시 읽지 않음
    assert len(fake.calls) == 1


def test_tasks_read_their_own_key_names(monkeypatch):
    from app import llm
    monkeypatch.setattr(config, "_ENV", {"VISION_OCR_GEMINI_API_KEY": "ocr", "COACHING_GEMINI_API_KEY": "coach",
                                         "GEMINI_API_KEY": "base", "ANTHROPIC_API_KEY": "claude"})
    assert [llm._key(config.task(t)) for t in ("ocr", "coach", "step_help", "report", "judge_generate", "generate")] \
        == ["ocr", "coach", "coach", "coach", "base", "claude"]


def test_key_env_model_overrides_config_model(conn, monkeypatch):
    from app import llm
    seen = {}
    monkeypatch.setattr(config, "_ENV", {"VISION_OCR_GEMINI_API_KEY": "k", "VISION_OCR_GEMINI_API_MODEL": "m-from-env"})
    monkeypatch.setattr(llm, "_gemini", lambda t, *a: seen.setdefault("model", t["model"]) and ("ok", 1, 1, "STOP"))
    llm.call(conn, "ocr", "s", "u")
    assert seen["model"] == "m-from-env"
    monkeypatch.setattr(config, "_ENV", {"VISION_OCR_GEMINI_API_KEY": "k"})
    seen.clear()
    llm.call(conn, "ocr", "s", "u")
    assert seen["model"] == config.task("ocr")["model"]      # key.env 에 없으면 config.toml 값
