"""설정 — 계정·프로젝트·목소리·경로는 코드가 아니라 설정 파일에 둔다(수강생은 자기 값만 적는다).

찾는 순서(앞에 있는 파일이 이긴다, 없는 항목만 뒤에서 채운다):
  1) 환경변수 FROGSHORTS_CONFIG 가 가리키는 파일
  2) 지금 폴더부터 위로 올라가며 처음 만나는 frogshorts.local.toml  (개인·회사 전용, git 제외)
  3) 지금 폴더부터 위로 올라가며 처음 만나는 frogshorts.toml
  4) 이 저장소 루트의 frogshorts.local.toml · frogshorts.toml  (어느 폴더에서 실행해도 찾게)
  5) 사용자 폴더 ~/.frogshorts/config.toml  (윈도우: C:\\Users\\<이름>\\.frogshorts\\config.toml)
예시 = 저장소의 frogshorts.example.toml. 키(API 값)는 설정 파일이 아니라 환경변수나 .env 에 둔다.
"""
from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path

from .util import FrogError

REPO = Path(__file__).resolve().parent.parent
DEFAULTS: dict = {
    "vertex": {"project": "", "account": "", "location": "global",
               "model": "latest", "usd_per_sec": 0},
    "ledger": {"path": "~/.frogshorts/vertex-ledger.jsonl"},
    "gemini": {"via": "vertex", "api_key_env": "GEMINI_API_KEY", "env_file": "", "tts_model": "gemini-3.8-flash-tts",
               "vision_model": "gemini-3.5-flash", "default_voice": "Kore"},
    "whisper": {"bin": "whisper-cli", "model": ""},
    "voice_clone": {"names": [], "command": [], "style_args": []},
}


def _candidates() -> list[Path]:
    out: list[Path] = []
    if os.environ.get("FROGSHORTS_CONFIG"):
        out.append(Path(os.environ["FROGSHORTS_CONFIG"]).expanduser())
    here = Path.cwd().resolve()
    for name in ("frogshorts.local.toml", "frogshorts.toml"):
        for d in [here, *here.parents]:
            if (d / name).exists():
                out.append(d / name)
                break
        out.append(REPO / name)
    out.append(Path.home() / ".frogshorts" / "config.toml")
    seen, uniq = set(), []
    for p in out:
        r = p.resolve() if p.exists() else p
        if r not in seen and p.exists():
            seen.add(r)
            uniq.append(p)
    return uniq


@lru_cache(maxsize=1)
def load() -> dict:
    """합친 설정(dict). 파일이 하나도 없어도 기본값으로 돈다(계정이 필요한 순간에 안내하며 멈춘다)."""
    merged = {k: dict(v) for k, v in DEFAULTS.items()}
    for p in reversed(_candidates()):                 # 뒤(약한 것)부터 덮어 앞이 이기게
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
            raise FrogError(f"설정 파일을 못 읽는다: {p} — {e}")
        for sec, vals in data.items():
            if isinstance(vals, dict):
                merged.setdefault(sec, {}).update(vals)
    merged["_files"] = [str(p) for p in _candidates()]
    return merged


def get(section: str, key: str, default=None):
    return load().get(section, {}).get(key, default)


def need(section: str, key: str, hint: str) -> str:
    """꼭 있어야 하는 값 — 없으면 어디에 무엇을 적으라고 안내하며 멈춘다."""
    v = get(section, key)
    if not v:
        raise FrogError(f"설정 [{section}] {key} 가 비었다 — frogshorts.toml 에 적어라. {hint}\n"
                        f"  (예시: {REPO / 'frogshorts.example.toml'} · 지금 읽은 파일: {load()['_files'] or '없음'})")
    return str(v)


def path(section: str, key: str) -> Path | None:
    v = get(section, key)
    return Path(os.path.expandvars(str(v))).expanduser() if v else None


def _env_file_value(name: str) -> str | None:
    f = path("gemini", "env_file")
    cands = [f] if f else []
    cands += [Path.cwd() / ".env", REPO / ".env"]
    for p in cands:
        if p and p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return None


def gemini_key() -> str:
    """제미나이 API 키 — 환경변수(api_key_env 이름) → .env 파일 순서. 값은 출력하지 않는다."""
    name = str(get("gemini", "api_key_env") or "GEMINI_API_KEY")
    k = os.environ.get(name) or _env_file_value(name)
    if not k:
        raise FrogError(f"제미나이 API 키가 없다 — 환경변수 {name} 에 넣거나 .env 에 {name}=… 로 적어라 "
                        f"(키 발급: https://aistudio.google.com/apikey)")
    return k
