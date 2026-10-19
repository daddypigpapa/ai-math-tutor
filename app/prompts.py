"""prompts/<이름>.md 로더. 파일은 "[시스템]" 블록과 "[사용자]" 블록으로 나뉜다."""
import re

from . import config


def load(name: str) -> tuple[str, str]:
    text = (config.prompts_dir() / f"{name}.md").read_text(encoding="utf-8")
    parts = re.split(r"^\[사용자\]\s*$", text, maxsplit=1, flags=re.M)
    system = parts[0].replace("[시스템]", "", 1).strip()
    user = parts[1].strip() if len(parts) > 1 else ""
    return system, user


def fill(template: str, **vars) -> str:
    for k, v in vars.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template
