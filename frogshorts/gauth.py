"""구글 클라우드 연결 — 영상·더빙·글씨 찾기를 모두 한 Vertex AI 프로젝트(한 크레딧)로 부른다.

연결 방식 두 가지(설정 [vertex] account 로 고른다):
  ADC(기본, 수강생) — `gcloud auth application-default login` 한 번으로 만든 "내 구글 계정 로그인 파일".
       account 를 비워 두면 이 방식. 토큰은 그 파일로 직접 새로 받는다(gcloud 를 매번 부르지 않는다).
  계정 지정(gcloud 에 여러 계정을 로그인해 둔 PC) — account 에 메일을 적으면 `gcloud auth print-access-token --account=<메일>`.
       gcloud 기본 계정은 절대 바꾸지 않는다.
프로젝트 = 설정 [vertex] project → 로그인 파일의 quota_project_id 순서.
토큰·키 값은 어디에도 출력하지 않는다.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config
from .util import FrogError, which

TOKEN_URL = "https://oauth2.googleapis.com/token"
SETUP_HINT = "처음이면 `frogshorts setup` 을 실행하면 차례대로 안내한다."
_cache: dict = {}


def account() -> str:
    return str(config.get("vertex", "account") or "")


def mode() -> str:
    return "account" if account() else "adc"


def adc_path() -> Path:
    """ADC 로그인 파일 위치 — GOOGLE_APPLICATION_CREDENTIALS → 윈도우 %APPDATA%\\gcloud → ~/.config/gcloud."""
    env = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env:
        return Path(env).expanduser()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "gcloud" / "application_default_credentials.json"
    base = os.environ.get("CLOUDSDK_CONFIG") or Path.home() / ".config" / "gcloud"
    return Path(base) / "application_default_credentials.json"


def adc_info() -> dict:
    p = adc_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def gcloud() -> str | None:
    return which("gcloud")                      # 윈도우는 gcloud.cmd — which 로 찾아야 한다


def _gcloud_token(args: list[str], who: str) -> str:
    exe = gcloud()
    if not exe:
        raise FrogError(f"gcloud 가 없다 — {SETUP_HINT}")
    r = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    tok = (r.stdout or "").strip()
    if r.returncode != 0 or not tok:
        last = ((r.stderr or "").strip().splitlines() or [""])[-1][:200]
        raise FrogError(f"구글 로그인 토큰을 못 받았다({who}) — {last}\n  {SETUP_HINT}")
    return tok


def _refresh(info: dict) -> tuple[str, float]:
    body = urllib.parse.urlencode({"client_id": info["client_id"], "client_secret": info["client_secret"],
                                   "refresh_token": info["refresh_token"], "grant_type": "refresh_token"}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body), timeout=30) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        why = ""
        try:
            why = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except ValueError:
            pass
        if why == "invalid_grant":
            raise FrogError("구글 로그인이 만료됐다 — 다시 연결하자: `frogshorts setup` "
                            "(또는 `gcloud auth application-default login`)")
        raise FrogError(f"구글 로그인 토큰 갱신 실패 HTTP {e.code} {why}")
    except (urllib.error.URLError, OSError) as e:
        raise FrogError(f"인터넷 연결을 확인하자 — 구글 로그인 서버에 못 닿았다: {str(e)[:100]}")
    return d["access_token"], time.time() + float(d.get("expires_in", 3600))


def token() -> str:
    """Vertex 호출용 접근 토큰(1시간짜리, 만료 5분 전까지 재사용)."""
    key = mode() + account()
    hit = _cache.get(key)
    if hit and hit[1] - 300 > time.time():
        return hit[0]
    if mode() == "account":
        tok, exp = _gcloud_token(["auth", "print-access-token", f"--account={account()}"], account()), time.time() + 3000
    else:
        info = adc_info()
        if not info:
            raise FrogError(f"구글 계정이 아직 연결되지 않았다(로그인 파일 없음: {adc_path()}).\n  {SETUP_HINT}")
        if info.get("type") == "authorized_user" and info.get("refresh_token"):
            tok, exp = _refresh(info)
        else:                                          # 서비스 계정 등 다른 종류는 gcloud 에 맡긴다
            tok, exp = _gcloud_token(["auth", "application-default", "print-access-token"], "ADC"), time.time() + 3000
    _cache[key] = (tok, exp)
    return tok


def project(required: bool = True) -> str:
    p = str(config.get("vertex", "project") or "") or str(adc_info().get("quota_project_id") or "")
    if not p and required:
        raise FrogError(f"구글 클라우드 프로젝트가 정해지지 않았다 — frogshorts.toml [vertex] project.\n  {SETUP_HINT}")
    return p


def email() -> str:
    """지금 연결된 구글 계정 메일(표시용). 모르면 빈 문자열."""
    if mode() == "account":
        return account()
    if "email" in _cache:
        return _cache["email"]
    em = ""
    try:
        url = "https://www.googleapis.com/oauth2/v3/tokeninfo?" + urllib.parse.urlencode({"access_token": token()})
        with urllib.request.urlopen(url, timeout=15) as r:
            em = json.load(r).get("email", "")
    except (FrogError, urllib.error.URLError, OSError, ValueError):
        pass
    _cache["email"] = em
    return em


def who() -> str:
    """과금 한 줄에 넣을 '계정 · 프로젝트' — 설정이 비어도 멈추지 않는다(계획 출력용)."""
    if mode() == "account":
        acc = account()
    else:
        acc = (email() or "내 구글 계정") + "(ADC)" if adc_info() else "(구글 계정 연결 안 됨 — frogshorts setup)"
    return f"계정 {acc} · 프로젝트 {project(required=False) or '(정해지지 않음 — frogshorts setup)'}"


def call(url: str, body: dict | None = None, timeout: int = 180, user_project: bool = True) -> dict:
    """구글 API 호출(JSON). 실패는 urllib.error.HTTPError 그대로 올린다 — 부르는 쪽이 알맞게 안내한다."""
    headers = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    if user_project and project(required=False):
        headers["x-goog-user-project"] = project()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def error_text(e: urllib.error.HTTPError) -> tuple[str, str]:
    """구글 오류 응답 → (상태 이름, 메시지)."""
    try:
        err = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
    except ValueError:
        return "", ""
    reason = ""
    for d in err.get("details", []) or []:
        reason = reason or d.get("reason", "")
    return reason or err.get("status", ""), str(err.get("message", ""))[:300]
