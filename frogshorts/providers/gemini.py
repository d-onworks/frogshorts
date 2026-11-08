"""제미나이 모델 호출 한 겹(더빙 TTS · 글씨 찾기 비전) — 어느 길로 부를지 여기서만 정한다.

설정 [gemini] via:
  "vertex"(기본) — 영상과 같은 Vertex AI 프로젝트·같은 구글 로그인으로 부른다(체험 크레딧 하나로 전부).
  "apikey"       — AI Studio API 키로 부른다(키는 환경변수나 .env, 설정 [gemini] api_key_env).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config, gauth
from ..util import FrogError

AISTUDIO = "https://generativelanguage.googleapis.com/v1beta"


def via() -> str:
    v = str(config.get("gemini", "via") or "vertex").lower()
    if v not in ("vertex", "apikey"):
        raise FrogError(f"설정 [gemini] via = {v!r} — 'vertex' 또는 'apikey' 만 된다")
    return v


def billing(what: str) -> str:
    """과금 한 줄 — 어느 계정·프로젝트(또는 어느 키 이름)인지. 값은 싣지 않는다."""
    if via() == "vertex":
        return f"과금: Vertex AI {what} · {gauth.who()}"
    return f"과금: 제미나이 API 키({config.get('gemini', 'api_key_env') or 'GEMINI_API_KEY'}) 프로젝트 · {what}"


def _vertex_url(model: str) -> str:
    loc = config.get("vertex", "location") or "global"
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{gauth.project()}/locations/{loc}/publishers/google/models/{model}:generateContent"


def generate(model: str, body: dict, timeout: int = 180) -> dict:
    """generateContent 한 번. HTTP 오류는 urllib.error.HTTPError 그대로 올린다(재시도는 부르는 쪽)."""
    if via() == "vertex":
        return gauth.call(_vertex_url(model), body, timeout=timeout)
    req = urllib.request.Request(f"{AISTUDIO}/models/{model}:generateContent", data=json.dumps(body).encode(),
                                 method="POST", headers={"x-goog-api-key": config.gemini_key(),
                                                         "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def interactions(body: dict, timeout: int = 180) -> dict:
    """AI Studio interactions(말투 주석 TTS) — apikey 길에서만 쓴다."""
    req = urllib.request.Request(f"{AISTUDIO}/interactions", data=json.dumps(body).encode(), method="POST",
                                 headers={"x-goog-api-key": config.gemini_key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def explain(e: urllib.error.HTTPError, model: str) -> str:
    """HTTP 오류 → 초보도 고칠 수 있는 한 줄."""
    reason, msg = gauth.error_text(e)
    if e.code == 404:
        return f"모델 '{model}' 을 이 프로젝트에서 못 찾았다 — 설정 [gemini] 의 모델 이름을 확인하자 ({msg[:120]})"
    if reason in ("BILLING_DISABLED",) or "billing" in msg.lower():
        return "이 프로젝트에 결제(또는 무료 체험 크레딧)가 연결되지 않았다 — `frogshorts setup` 이 연결 화면을 알려 준다"
    if reason in ("SERVICE_DISABLED", "API_DISABLED") or "has not been used" in msg:
        return "이 프로젝트에서 Vertex AI API 가 꺼져 있다 — `frogshorts setup` 을 다시 실행하면 켜 준다"
    if e.code in (401, 403):
        return f"권한이 없다(HTTP {e.code}) — 연결한 구글 계정이 이 프로젝트의 소유자·편집자인지 확인하자 ({msg[:120]})"
    if e.code == 429:
        return "요청이 너무 잦다(HTTP 429) — 1분쯤 뒤에 다시"
    return f"HTTP {e.code} {reason} {msg[:160]}"
