"""Omni 최신 버전 고르기 — 설정 [vertex] model 이 "latest"(기본)면 Vertex 모델 목록에서 가장 높은 버전을 쓴다.

같은 버전이면 정식(GA)을 미리보기(preview)보다 앞에 둔다. 목록은 하루 한 번만 묻고(~/.frogshorts/omni-latest.json),
못 물으면(로그인 전·인터넷 끊김) 마지막으로 본 값 → 알려진 최신(FALLBACK) 순서로 쓴다 — 계획 출력은 멈추지 않는다.
버전마다 한 번에 만들 수 있는 길이·추정 단가가 달라서 SPEC 표로 둔다(모르는 새 버전은 표의 가장 높은 버전 값).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import gauth

LIST_URL = "https://aiplatform.googleapis.com/v1beta1/publishers/google/models?pageSize=200"
FALLBACK = "gemini-omni-1.2-flash"
CACHE_SEC = 24 * 3600
# 버전 → (한 번에 최대 초, 720p 1초당 추정 달러). 출처: 2026-11 구글 발표(1.2 = 3~15초 · 1.1 대비 약 30% 인하).
SPEC: dict[tuple[int, int], tuple[int, float]] = {(1, 1): (10, 0.10), (1, 2): (15, 0.07)}
_mem: dict = {}


def version(model: str) -> tuple[int, int] | None:
    m = re.match(r"gemini-omni-(\d+)\.(\d+)", model)
    return (int(m[1]), int(m[2])) if m else None


def spec(model: str) -> tuple[int, float]:
    v = version(model)
    known = sorted(SPEC)
    if v is None:
        return SPEC[known[-1]]
    lower = [k for k in known if k <= v]
    return SPEC[lower[-1] if lower else known[0]]


def pick(names: list[str]) -> str | None:
    """모델 이름 목록 → 가장 높은 Omni(같은 버전이면 preview 아닌 것)."""
    cands = [n for n in names if version(n)]
    if not cands:
        return None
    return max(cands, key=lambda n: (version(n), "preview" not in n))


def _cache_file() -> Path:
    return Path.home() / ".frogshorts" / "omni-latest.json"


def _read_cache() -> dict:
    try:
        return json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _list_names() -> list[str]:
    names, page = [], ""
    for _ in range(10):
        d = gauth.call(LIST_URL + (f"&pageToken={page}" if page else ""), timeout=30)
        names += [m.get("name", "").split("/")[-1] for m in d.get("publisherModels", [])]
        page = d.get("nextPageToken", "")
        if not page:
            break
    return names


def latest() -> str:
    if "model" in _mem:
        return _mem["model"]
    c = _read_cache()
    if c.get("model") and time.time() - float(c.get("at", 0)) < CACHE_SEC:
        _mem["model"] = c["model"]
        return c["model"]
    try:
        found = pick(_list_names())
    except Exception:                       # 로그인 전·인터넷 끊김 — 계획 출력은 멈추지 않는다
        found = None
    model = found or c.get("model") or FALLBACK
    if found:
        try:
            _cache_file().parent.mkdir(parents=True, exist_ok=True)
            _cache_file().write_text(json.dumps({"model": found, "at": time.time()}), encoding="utf-8")
        except OSError:
            pass
    _mem["model"] = model
    return model
