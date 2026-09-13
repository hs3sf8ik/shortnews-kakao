"""공용 유틸: 경로, 설정 로딩, 날짜, 로깅."""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"
SECRETS_DIR = ROOT / "secrets"
DATA_DIR = ROOT / "data"

KST = ZoneInfo("Asia/Seoul")
WEEKDAYS_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
WEEKDAYS_KO_SHORT = ["월", "화", "수", "목", "금", "토", "일"]

log = logging.getLogger("shortnews")


def setup_logging(verbose: bool = False) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 깨짐 방지
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def load_env() -> None:
    """secrets/.env 를 환경변수로 로드 (이미 설정된 값은 유지)."""
    env_path = SECRETS_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def load_settings() -> dict:
    return json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))


def load_users() -> list[dict]:
    users = json.loads((CONFIG_DIR / "users.json").read_text(encoding="utf-8"))
    for u in users:
        u.setdefault("keywords", [])
        u.setdefault("kakao", True)
        u.setdefault("enabled", True)
    return [u for u in users if u["enabled"]]


def now_kst() -> dt.datetime:
    return dt.datetime.now(tz=KST)


def header_date(d: dt.date) -> str:
    """'26년 9월 10일 목요일' 형식."""
    return f"{d.year % 100}년 {d.month}월 {d.day}일 {WEEKDAYS_KO[d.weekday()]}"


def short_date(d: dt.date) -> str:
    """'9/10(목)' 형식."""
    return f"{d.month}/{d.day}({WEEKDAYS_KO_SHORT[d.weekday()]})"


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
