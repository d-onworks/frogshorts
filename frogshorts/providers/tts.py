"""더빙 공급자 한 겹 — 제미나이 플래시 TTS (24kHz 모노 WAV 그대로, 16k 변환 금지).

과금: 기본은 영상과 같은 Vertex AI 프로젝트(설정 [gemini] via = "vertex") · 10초 ≈ $0.002.
  via = "apikey" 면 AI Studio API 키(환경변수나 .env, 설정 [gemini] api_key_env).
  말투: Vertex 는 연출 메모(DIRECTOR'S NOTES) + 대본(TRANSCRIPT) 구조로 넣는다 — 2026-11-07 받아쓰기로
        6/6 대본만 읽음 확인("[말투] 대사" 꼴은 말투까지 소리 내어 읽은 적이 있어 버렸다),
        API 키 길은 interactions 의 speech_metadata 주석으로 넣는다.
기본 목소리 = 설정 [gemini] default_voice(기본 Kore). 모델 = [gemini] tts_model.
복제 목소리(선택 기능): 설정 [voice_clone] 의 names 에 든 목소리는 command 로 바깥 프로그램을 부른다.
  command·style_args 의 {voice} {text} {out} {style} 자리에 값이 들어가고, ~ 는 사용자 폴더로 펼친다.
  [voice_clone] 이 없으면 이 기능은 없다(배포본 기본).
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import time
import urllib.error
from pathlib import Path

from .. import config
from ..util import FrogError
from . import gemini


def _model() -> str:
    return str(config.get("gemini", "tts_model") or "gemini-3.8-flash-tts")


def __getattr__(name: str):
    """tts.MODEL — 읽을 때마다 설정에서 가져온다(더빙 캐시 키에 쓰인다)."""
    if name == "MODEL":
        return _model()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def default_voice() -> str:
    return str(config.get("gemini", "default_voice") or "Kore")


def _clone_names() -> list[str]:
    return [str(n) for n in (config.get("voice_clone", "names") or [])]


def is_clone(voice: str) -> bool:
    return bool(voice) and voice in _clone_names() and bool(config.get("voice_clone", "command"))


def billing_line(voice: str) -> str:
    """과금 한 줄 — 키 '이름'만 보여 주고 값은 절대 싣지 않는다."""
    if is_clone(voice):
        prog = " ".join(Path(a).name for a in _clone_cmd(voice, "", Path(), "")[:2])
        return f"과금: 복제 목소리 '{voice}' · 바깥 프로그램 경유({prog})"
    return gemini.billing(f"{_model()} · 목소리 {voice or default_voice()} · 문장당 1원 안팎")


def _fill(arg: str, vals: dict) -> str:
    """자리표시자를 한 번에 바꾼다(대사 안에 '{out}' 같은 글자가 있어도 다시 바뀌지 않게)."""
    arg = str(Path(os.path.expanduser(arg))) if arg.startswith("~") else arg      # 윈도우에서도 \ 하나로
    return re.sub(r"\{(voice|text|out|style)\}", lambda m: vals[m.group(1)], arg)


def _clone_cmd(voice: str, text: str, out: Path, style: str) -> list[str]:
    vals = {"voice": voice, "text": text, "out": str(out), "style": style}
    cmd = [_fill(str(a), vals) for a in config.get("voice_clone", "command") or []]
    if style:
        cmd += [_fill(str(a), vals) for a in config.get("voice_clone", "style_args") or []]
    return cmd


def _synth_clone(text: str, out: Path, voice: str, style: str) -> None:
    cmd = _clone_cmd(voice, text, out, style)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not out.exists():
        tail = (r.stderr or r.stdout or "").strip()[-300:]
        raise FrogError(f"복제 목소리 '{voice}' TTS 실패: {text[:30]} — {tail}")


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    if pcm[:4] == b"RIFF":
        return pcm
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _speech_config(voice: str) -> dict:
    return {"responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}


def _audio(text: str, voice: str, style: str) -> bytes:
    if style and gemini.via() == "apikey":
        r = gemini.interactions({
            "model": _model(),
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text, "annotations": [
                {"type": "speech_metadata", "style": style}]}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice}]}})
        aud = [c for s in r.get("steps", []) if s.get("type") == "model_output"
               for c in s.get("content", []) if c.get("type") == "audio"]
        return base64.b64decode(aud[-1]["data"])
    said = f"### DIRECTOR'S NOTES\nStyle: {style}\n\n### TRANSCRIPT\n{text}" if style else text
    r = gemini.generate(_model(), {"contents": [{"role": "user", "parts": [{"text": said}]}],
                                   "generationConfig": _speech_config(voice)})
    return base64.b64decode(r["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])


def synth(text: str, out: Path, voice: str, style: str = "") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    voice = voice or default_voice()
    if is_clone(voice):
        _synth_clone(text, out, voice, style)
        return
    err = ""
    for attempt in (1, 2, 3):
        try:
            out.write_bytes(_pcm_to_wav(_audio(text, voice, style)))
            return
        except urllib.error.HTTPError as e:
            err = gemini.explain(e, _model())
            if e.code in (400, 401, 403, 404):
                break
        except FrogError:                # 키 없음 같은 설정 문제는 재시도하지 않는다
            raise
        except Exception as e:  # 네트워크 흔들림 → 재시도
            err = str(e)[:200]
        time.sleep(2 * attempt)
    raise FrogError(f"TTS 실패({err}): {text[:30]}")
