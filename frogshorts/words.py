"""단어 시각 — 로컬 위스퍼(whisper.cpp, 기본 whisper-cli)로 받아쓰고, 우리 대본 글자에 시각을 붙인다.

위스퍼는 고유명사를 틀리게 적을 수 있다(오토프로그→오토프로고). 그래서 시각은 위스퍼 단어에서 받되,
글자는 대본 것을 쓴다: 대본 글자열과 받아쓰기 글자열을 difflib 로 맞추고, 못 맞춘 글자는 앞뒤에서 보간.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
import tempfile
from pathlib import Path

from . import config
from .util import FrogError, read_json, run

_KEEP = re.compile(r"[0-9A-Za-z가-힣]")


def model_dir() -> Path:
    """모델 기본 폴더 — 맥·윈도우 모두 사용자 폴더 아래 .local/share/whisper-models."""
    return Path.home() / ".local" / "share" / "whisper-models"


def model_candidates() -> list[Path]:
    """찾는 순서: 설정 [whisper] model → 환경변수 FROGSHORTS_WHISPER_MODEL → 기본 폴더의 ggml-large-v3*.bin."""
    out = [p for p in (config.path("whisper", "model"),) if p]
    if os.environ.get("FROGSHORTS_WHISPER_MODEL"):
        out.append(Path(os.environ["FROGSHORTS_WHISPER_MODEL"]).expanduser())
    d = model_dir()
    out += [d / "ggml-large-v3.bin", d / "ggml-large-v3-turbo.bin"]
    if d.is_dir():
        out += sorted(d.glob("ggml-large-v3*.bin"))
    return out


def model_path() -> str:
    for m in model_candidates():
        if m.is_file():
            return str(m)
    raise FrogError("위스퍼 모델이 없다 — 설정 [whisper] model 이나 환경변수 FROGSHORTS_WHISPER_MODEL 에 "
                    f"ggml 모델 경로를 적거나, {model_dir()} 에 ggml-large-v3*.bin 을 둬라(`frogshorts doctor` 가 받는 법을 알려 준다)")


TOOLS_DIR = Path.home() / ".frogshorts" / "tools"      # setup 이 받은 프로그램(윈도우 위스퍼 등)


def whisper_bin() -> str:
    """위스퍼 실행 파일 — 설정 [whisper] bin(기본 whisper-cli) → PATH → setup 이 받아 둔 폴더."""
    name = str(config.get("whisper", "bin") or "whisper-cli")
    found = shutil.which(os.path.expanduser(name))
    if not found and (TOOLS_DIR / "whisper").is_dir():
        exe = "whisper-cli.exe" if os.name == "nt" else "whisper-cli"
        found = next((str(p) for p in sorted((TOOLS_DIR / "whisper").rglob(exe))), None)
    if not found:
        raise FrogError(f"위스퍼 실행 파일 '{name}' 을 못 찾는다 — 설치하거나 설정 [whisper] bin 에 경로를 적어라 "
                        "(`frogshorts doctor` 참고)")
    return found


def transcribe(media: Path, lang: str = "ko") -> list[dict]:
    """미디어 → [{w, s, e}] (초). 단어 단위(-ml 1 -sow)."""
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", media, "-ar", "16000", "-ac", "1", wav])
        run([whisper_bin(), "-m", model_path(), "-l", lang, "-ml", "1", "-sow", "-oj", "-np",
             "-of", Path(td) / "t", "-f", wav])
        data = read_json(Path(td) / "t.json")
    out = []
    for seg in data.get("transcription", []):
        w = seg["text"].strip()
        if not w:
            continue
        out.append({"w": w, "s": seg["offsets"]["from"] / 1000, "e": seg["offsets"]["to"] / 1000})
    return out


def plain(text: str) -> str:
    return "".join(_KEEP.findall(text))


def align(script: str, heard: list[dict]) -> list[dict]:
    """대본(표기 그대로 띄어쓰기 단위) → [{w, s, e}]. 시각은 받아쓰기에서."""
    words = script.split()
    if not words:
        return []
    # 받아쓰기 글자마다 시각(단어 구간 안에서 고르게 나눔)
    hc, ht = [], []
    for h in heard:
        cs = plain(h["w"])
        for i, c in enumerate(cs):
            hc.append(c)
            ht.append((h["s"] + (h["e"] - h["s"]) * i / len(cs), h["s"] + (h["e"] - h["s"]) * (i + 1) / len(cs)))
    sc = []                       # (글자, 단어번호)
    for wi, w in enumerate(words):
        for c in plain(w):
            sc.append((c, wi))
    if not hc or not sc:
        raise FrogError(f"받아쓰기가 비었다 — 음성 확인 필요: {script[:30]}")
    times: list = [None] * len(sc)
    sm = difflib.SequenceMatcher(a=[c for c, _ in sc], b=hc, autojunk=False)
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = ht[b + k]
    _fill(times, ht[0][0], ht[-1][1])
    out = []
    for wi, w in enumerate(words):
        idx = [i for i, (_, x) in enumerate(sc) if x == wi]
        if not idx:               # 문장부호만 있는 토큰 → 앞 단어에 붙인다
            if out:
                out[-1]["w"] += " " + w
            continue
        out.append({"w": w, "s": round(times[idx[0]][0], 3), "e": round(times[idx[-1]][1], 3)})
    return out


def _fill(times: list, t0: float, t1: float) -> None:
    """못 맞춘 글자 시각을 앞뒤 맞춘 글자 사이에서 고르게 채운다."""
    n = len(times)
    i = 0
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 else t0
        right = times[j][0] if j < n else t1
        step = max(right - left, 0) / (j - i)
        for k in range(i, j):
            times[k] = (left + step * (k - i), left + step * (k - i + 1))
        i = j


def heard_text(heard: list[dict]) -> str:
    return " ".join(h["w"] for h in heard)
