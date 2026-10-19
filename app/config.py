"""설정·경로·비밀값 로딩. 경로는 호출 시점에 환경변수를 읽어 테스트에서 바꿀 수 있다."""
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_env_files() -> dict:
    # 낮음→높음: ~/claude/key.env < ROOT/.env  (실제 환경변수가 최우선)
    vals: dict = {}
    for p in (Path.home() / "claude" / "key.env", ROOT / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v:
                vals[k.strip()] = v
    return vals


_ENV = _read_env_files()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name) or _ENV.get(name) or default


def db_path() -> Path:
    return Path(os.environ.get("TUTOR_DB") or ROOT / "data" / "tutor.db")


def problems_dir() -> Path:
    return Path(os.environ.get("TUTOR_PROBLEMS") or ROOT / "problems")


def input_dir() -> Path:
    return Path(os.environ.get("TUTOR_INPUT") or ROOT / "input")


def prompts_dir() -> Path:
    return ROOT / "prompts"


def web_dir() -> Path:
    return ROOT / "web"


_cfg: dict | None = None


def cfg() -> dict:
    global _cfg
    if _cfg is None:
        path = Path(os.environ.get("TUTOR_CONFIG") or ROOT / "config.toml")
        _cfg = tomllib.loads(path.read_text(encoding="utf-8"))
    return _cfg


def reload() -> None:
    global _cfg
    _cfg = None


def task(name: str) -> dict:
    return cfg()["tasks"][name]
