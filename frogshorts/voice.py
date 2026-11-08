"""더빙 — 문장마다 TTS → 앞뒤 무음 다듬기 → 위스퍼 받아쓰기 → 꼭읽기 단어 검수 → 대본 단어 시각.

캐시 키 = (읽는 글, 목소리, 말투, 모델). 대사 한 줄을 고치면 그 줄만 다시 만든다.
검수에서 꼭읽기 단어가 안 들리면 그 문장만 최대 3번까지 다시 뽑고, 그래도 안 되면 경고로 남긴다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import align, words
from .providers import tts
from .spec import Line, Spec
from .util import FrogError, duration, read_json, run, say, sha, warn, write_json

TRIES = 3
TRIM = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
        "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse")


def key(line: Line, spec: Spec) -> str:
    v = spec.voice
    return sha("tts", line.speech, v.get("보이스", ""), v.get("말투", ""), tts.MODEL)


def _check(line: Line, heard: list[dict], spec: Spec) -> list[str]:
    """꼭읽기 단어 중 이 문장에 있는데 안 들린 것."""
    got = words.plain(words.heard_text(heard))
    pron = spec.raw.get("발음") or {}
    miss = []
    for t in spec.must_say:
        if t not in line.display:
            continue
        forms = {words.plain(t), words.plain(pron.get(t, t))}
        if not any(f and f in got for f in forms):
            miss.append(t)
    return miss


def _make_one(line: Line, spec: Spec, wav: Path, meta: Path) -> None:
    v = spec.voice
    tmp = wav.with_suffix(".raw.wav")
    tries = []
    for i in range(1, TRIES + 1):
        tts.synth(line.speech, tmp, v.get("보이스") or tts.default_voice(), v.get("말투", ""))
        cand = wav.with_suffix(f".try{i}.wav")
        run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-af", TRIM, "-ar", "24000", "-ac", "1", cand])
        heard = words.transcribe(cand)
        miss = _check(line, heard, spec)
        say(f"    대사{line.n} {i}차: 들림 「{words.heard_text(heard)}」" + (f" ✗ 안 들림 {miss}" if miss else " ✓"))
        tries.append((len(miss), i, cand, heard, miss))
        if not miss:
            break
    _, i, cand, heard, miss = min(tries, key=lambda x: (x[0], x[1]))   # 가장 덜 틀린 것, 같으면 먼저 것
    shutil.move(cand, wav)
    for p in wav.parent.glob(f"{wav.stem}.try*.wav"):
        p.unlink()
    tmp.unlink(missing_ok=True)
    if miss:
        warn(f"대사{line.n}: {TRIES}번 뽑아도 {miss} 가 안 들린다 — 전체 더빙 맥락으로 다시 확인한다")
    write_json({"읽기": line.speech, "들림": heard, "안들림": miss, "시도": i,
                "보이스": v.get("보이스", ""), "말투": v.get("말투", "")}, meta)


def prepare(spec: Spec, cache: Path, go: bool, quiet: bool = False) -> dict:
    """대사마다 {wav, dur, words(문장 기준 시각), miss}. go=False 면 없는 것은 계획만."""
    out, todo = {}, []
    d = cache / "음성"
    d.mkdir(parents=True, exist_ok=True)
    for ln in spec.lines:
        k = key(ln, spec)
        wav, meta = d / f"{k}.wav", d / f"{k}.json"
        if not wav.exists():
            todo.append((ln, wav, meta))
        out[ln.n] = {"wav": wav, "meta": meta, "key": k}
    if todo and not quiet:
        say(f"  더빙 새로 {len(todo)}줄 / 캐시 {len(spec.lines) - len(todo)}줄 — "
            + tts.billing_line(spec.voice.get("보이스", "")))
    if todo and not go:
        return {"계획": [ln.n for ln, _, _ in todo], **out}
    for ln, wav, meta in todo:
        _make_one(ln, spec, wav, meta)
    for ln in spec.lines:
        o = out[ln.n]
        m = read_json(o["meta"])
        o["dur"] = duration(o["wav"])
        o["words"] = align.align(ln.display, m["들림"], spec.raw.get("발음") or {})
        o["miss"] = m["안들림"]
        if not o["words"]:
            raise FrogError(f"대사{ln.n} 단어 정렬 실패")
    return out


def recheck(spec: Spec, tl: dict, vo: dict, narration: Path) -> dict:
    """문장 하나만 받아쓰면 위스퍼가 짧은 고유명사를 흔들리게 적는다(2026-11-03 '차콜'→'체콜' 실측).
    문장 검수에서 걸린 줄만, 이어 붙인 전체 더빙을 받아써서 그 줄 구간에 단어가 들리는지 다시 본다."""
    bad = {ln.n: vo[ln.n]["miss"] for ln in spec.lines if vo[ln.n].get("miss")}
    if not bad:
        return {}
    heard = words.transcribe(narration)
    pron = spec.raw.get("발음") or {}
    left = {}
    for ln in tl["lines"]:
        if ln["n"] not in bad:
            continue
        s, e = ln["start"] - 0.3, ln["start"] + ln["dur"] + 0.3
        got = words.plain(" ".join(h["w"] for h in heard if h["e"] > s and h["s"] < e))
        miss = [t for t in bad[ln["n"]] if not ({words.plain(t), words.plain(pron.get(t, t))} & _subs(got))]
        say(f"    대사{ln['n']} 전체 맥락 재확인: 「{got}」 → " + ("✓ 들림" if not miss else f"✗ 여전히 {miss}"))
        if miss:
            left[ln["n"]] = miss
    return left


def _subs(text: str) -> set:
    return {text[i:j] for i in range(len(text)) for j in range(i + 1, min(len(text), i + 12) + 1)}
