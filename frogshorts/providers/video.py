"""영상 공급자 한 겹 — Vertex AI Omni(기본 "latest" = 그때그때 최신 버전, omni.py). 모델을 바꿀 땐 이 파일만 고친다.

과금 계정·프로젝트·장부 경로는 코드가 아니라 설정 파일(frogshorts.toml 의 [vertex]·[ledger])에서 읽는다.
인증은 gauth 한 곳 — 기본은 내 구글 계정 ADC 로그인 파일, 설정 [vertex] account 가 있으면
`gcloud auth print-access-token --account=<그 계정>`(gcloud 기본 계정은 절대 바꾸지 않는다).
Veo 자동 전환은 기본 꺼짐(실패하면 보고).
설정값은 가져올(import) 때가 아니라 쓰는 순간에 읽는다 — 설정이 비어도 계획 출력(dry-run)·테스트는 돈다.

요청 형식 두 가지:
  기본형 — 텍스트(+이미지 1장), 길이 지정 없음(~10초). 옷가게·떡집편에서 실측.
  참조형 — 길이(3초~모델 상한: 1.1=10초 · 1.2=15초) 지정 · 이미지/영상 참조 여러 개. 실측(2026-11-03, 7초 생성 성공)과
           같은 모양: background+store 로 시작 → 10초마다 조회 → 수거, response_format 목록에 duration,
           generation_config.video_config.task=reference_to_video.
"""
from __future__ import annotations

import base64
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import config, gauth
from . import omni
from ..util import FrogError, duration, now, say, write_json

EST_CLIP_SEC = 10             # 길이를 안 주면 약 10초로 나온다
MIN_SEC = 3                   # 길이 지정 최소(duration "Ns") — 최대는 모델마다(_max_sec)
POLL_SEC, POLL_LIMIT = 10, 1500

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
         ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}

def _model() -> str:
    m = str(config.get("vertex", "model") or "latest").strip()
    return omni.latest() if m.lower() == "latest" else m


def _max_sec() -> int:
    return omni.spec(_model())[0]


def _usd_per_sec() -> float:
    """추정 단가 — 설정에 적었으면 그 값, 아니면 모델 버전 표. 실제 차감은 결제 콘솔."""
    return float(config.get("vertex", "usd_per_sec") or 0) or omni.spec(_model())[1]


def _account() -> str:
    """과금 계정(표시용) — 계정 지정 방식이면 그 메일, ADC 면 연결된 구글 계정 메일."""
    return gauth.email() or "ADC"


def _project() -> str:
    return gauth.project()


def _ledger() -> Path:
    return config.path("ledger", "path") or Path.home() / ".frogshorts" / "vertex-ledger.jsonl"


def _base() -> str:
    loc = config.get("vertex", "location") or "global"
    return f"https://aiplatform.googleapis.com/v1beta1/projects/{_project()}/locations/{loc}/interactions"


_LAZY = {"MODEL": _model, "MAX_SEC": _max_sec, "USD_PER_SEC": _usd_per_sec, "ACCOUNT": _account, "PROJECT": _project,
         "LEDGER": _ledger, "BASE": _base}


def __getattr__(name: str):
    """video.MODEL · video.ACCOUNT 같은 옛 상수 이름을 그대로 쓰게 — 읽을 때마다 설정에서 가져온다."""
    if name in _LAZY:
        return _LAZY[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


SLOW_OK = 0.95                # 생성 길이를 정할 때 이만큼까지는 살짝 늦춰 맞춘다(1초 더 사지 않으려고)


def secs_for(need: float) -> int:
    """필요한 화면 길이 → 요청할 생성 길이(초, 3~모델 상한)."""
    return min(_max_sec(), max(MIN_SEC, math.ceil(round(need * SLOW_OK, 3))))


def est_usd(secs: int | None) -> float:
    return (secs or EST_CLIP_SEC) * _usd_per_sec()


def billing_line(secs: int | None = None) -> str:
    """호출 전 한 줄 — 어느 계정·프로젝트에서 과금되는지. 설정이 비었으면 비었다고 적는다(계획 출력은 계속)."""
    price = f" · 클립당 약 ${est_usd(secs):.1f}({secs}초)" if secs else ""
    return f"과금: Vertex AI {_model()} · {gauth.who()}{price}"


def _token() -> str:
    return gauth.token()


def _log(rec: dict) -> None:
    led = _ledger()
    led.parent.mkdir(parents=True, exist_ok=True)
    with led.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), **rec}, ensure_ascii=False) + "\n")


def is_video(p: Path) -> bool:
    return _MIME.get(p.suffix.lower(), "").startswith("video/")


def build_body(prompt: str, refs: list[Path], res: str, aspect: str, secs: int | None) -> dict:
    """요청 본문. 길이도 영상 참조도 없고 참조 이미지가 1장 이하면 기본형(지금까지 쓰던 모양 그대로)."""
    if secs is not None and not MIN_SEC <= secs <= _max_sec():
        raise FrogError(f"생성 길이 {secs}초 — {_model()} 은 {MIN_SEC}~{_max_sec()}초만 된다(길면 컷을 나눠라)")
    for r in refs:
        if r.suffix.lower() not in _MIME:
            raise FrogError(f"참조 파일 형식을 모른다: {r}")
    parts = [{"type": "video" if is_video(r) else "image", "mime_type": _MIME[r.suffix.lower()],
              "data": base64.b64encode(r.read_bytes()).decode()} for r in refs]
    if secs is None and len(refs) <= 1 and not any(is_video(r) for r in refs):
        inp = [{"type": "text", "text": prompt}, *parts] if parts else prompt
        return {"model": _model(), "input": inp,
                "response_format": {"type": "video", "aspect_ratio": aspect, "resolution": res}}
    fmt = {"type": "video", "delivery": "inline", "resolution": res, "aspect_ratio": aspect}
    if secs is not None:
        fmt["duration"] = f"{secs}s"
    body = {"model": _model(), "background": True, "store": True,
            "input": [{"type": "text", "text": prompt}, *parts], "response_format": [fmt]}
    if parts:
        body["generation_config"] = {"video_config": {"task": "reference_to_video"}}
    return body


def _api(path: str = "", body: dict | None = None, timeout: int = 900) -> dict:
    req = urllib.request.Request(_base() + path, data=json.dumps(body).encode() if body else None,
                                 method="POST" if body else "GET", headers={
                                     "Authorization": f"Bearer {_token()}", "x-goog-user-project": _project(),
                                     "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _errors(resp: dict) -> list[dict]:
    """응답의 실패 분류 — errors[] (예: code=safety). 없으면 빈 목록."""
    errs = resp.get("errors") or ([resp["error"]] if isinstance(resp.get("error"), dict) else [])
    return [{"code": e.get("code"), "message": str(e.get("message", ""))[:300]} for e in errs if isinstance(e, dict)]


def _fail(errs: list[dict], res: str, secs, tag: str, why: str) -> None:
    _log({"model": _model(), "res": res, "secs_req": secs, "ok": False, "err": why, "errors": errs, "tag": tag})
    codes = ", ".join(str(e["code"]) for e in errs) or "없음"
    hint = (" — 안전 필터에 걸렸다. 무엇이 걸렸는지는 알려 주지 않는다: 강제·위험해 보이는 동작 묘사를 "
            "부드럽게 고쳐 그 컷만 다시 요청하라(앞 컷은 캐시로 재사용)") if any(e["code"] == "safety" for e in errs) else ""
    raise FrogError(f"Omni 실패({why}) · 분류 {codes}: {json.dumps(errs, ensure_ascii=False)[:300]}{hint}")


def _save_video(resp: dict, out: Path) -> bool:
    for step in resp.get("steps", []):
        for c in step.get("content", []) if step.get("type") == "model_output" else []:
            if c.get("type") == "video" and c.get("data"):
                out.write_bytes(base64.b64decode(c["data"]))
            elif c.get("type") == "video" and c.get("uri"):
                q = urllib.request.Request(c["uri"], headers={"Authorization": f"Bearer {_token()}",
                                                              "x-goog-user-project": _project()})
                with urllib.request.urlopen(q, timeout=600) as f:
                    out.write_bytes(f.read())
            if out.exists():
                return True
    return False


def generate(prompt: str, out: Path, refs: list[Path] | None = None, res: str = "1080p",
             aspect: str = "9:16", secs: int | None = None, tag: str = "") -> float:
    """클립 하나 생성 → 실제 초. 이미 있는 파일은 덮지 않는다."""
    if out.exists():
        raise FrogError(f"이미 있는 클립 — 덮지 않는다: {out}")
    refs = list(refs or [])
    body = build_body(prompt, refs, res, aspect, secs)
    _project(), _token()                                # 돈 쓰기 전에 연결부터 확인(없으면 안내하며 멈춤)
    say("  " + billing_line(secs))
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        resp = _api(body=body)
        if body.get("background"):
            iid = resp.get("id")
            if not iid:
                _fail(_errors(resp), res, secs, tag, "요청 번호 없음")
            write_json({"interaction": iid, "at": now(), "secs": secs, "refs": [str(r) for r in refs]},
                       out.with_suffix(".요청.json"))           # 중간에 끊겨도 번호로 다시 받을 수 있게
            for _ in range(POLL_LIMIT):
                time.sleep(POLL_SEC)
                resp = _api("/" + iid)
                st = resp.get("status")
                if st == "completed":
                    break
                if st in ("failed", "cancelled"):
                    _fail(_errors(resp), res, secs, tag, st)
            else:
                _fail([], res, secs, tag, f"시간 초과({POLL_SEC * POLL_LIMIT}초) · 요청 {iid}")
    except urllib.error.HTTPError as e:
        _fail([{"code": f"HTTP {e.code}", "message": e.read().decode(errors="replace")[:300]}], res, secs, tag, "HTTP")
    if not _save_video(resp, out):
        _fail(_errors(resp), res, secs, tag, "생성 거절" if _errors(resp) else "응답에 영상 없음")
    d, usd = duration(out), _usd_per_sec()
    _log({"model": _model(), "res": res, "aspect": aspect, "secs": round(d, 2), "secs_req": secs,
          "refs": [r.name for r in refs], "est_usd": round(d * usd, 3),
          "elapsed": round(time.time() - t0), "out": str(out), "tag": tag, "ok": True})
    say(f"  ✓ {_model()} {res} {d:.1f}초 · 추정 ${d * usd:.2f} · {time.time() - t0:.0f}초 걸림")
    return d


def ledger_summary() -> None:
    led = _ledger()
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines() if x.strip()] if led.exists() else []
    ok = [r for r in rows if r.get("ok")]
    mine = [r for r in ok if str(r.get("tag", "")).startswith("frogshorts")]
    fails = [r for r in rows if r.get("ok") is False and str(r.get("tag", "")).startswith("frogshorts")]
    say(f"장부 {led}\n  전체 성공 {len(ok)}건 · 추정 ${sum(r.get('est_usd', 0) for r in ok):.2f}")
    say(f"  frogshorts 분 {len(mine)}건 · 추정 ${sum(r.get('est_usd', 0) for r in mine):.2f} · 실패 {len(fails)}건")
    for r in fails[-5:]:
        say(f"    실패 {r.get('at', '')[:16]} {r.get('tag', '')} · {r.get('err', '')} · "
            f"{[e.get('code') for e in r.get('errors') or []]}")
