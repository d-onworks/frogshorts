"""원본재현 모드 — 원본의 구간·길이·소리를 따른다(새 대사·더빙 없음).

새대사 모드는 새 더빙의 단어 시각이 기준이고, 이 모드는 원본 시간이 기준이다.
  원본범위: 0-14          재현할 원본 구간(기본 전체)
  컷[].원본구간: a-b      컷마다 재현할 원본 구간 — 이어져야 한다(빈틈·겹침 금지)
  컷[].길이              생성 길이 — 안 적으면 구간 길이(3~10초)
  소리: 원본 | 없음        원본 소리를 그대로 쓰거나 비운다
부품 시작 앵커: 완성본 기준 초(12.5) 또는 원본 받아쓰기 단어(원본:딸기#2).
★ 원본 소리·구성은 참고 영상 제작자의 것이다 — 강의·시연용. 발행하려면 권리를 먼저 확인한다.
"""
from __future__ import annotations

from pathlib import Path

from .providers import video
from .captions import build_cues
from .spec import Spec
from .timeline import place_parts
from .util import FrogError, duration, job_root, read_json, run, warn

GAP_OK = 0.05


def src_range(spec: Spec) -> tuple[float, float]:
    if not spec.source:
        raise FrogError("원본재현 모드인데 원본 영상(참고.mp4)이 없다 — 먼저 frogshorts analyze")
    return spec.source_range or (0.0, round(duration(spec.source), 3))


def check(spec: Spec) -> tuple[float, float]:
    """컷 구간이 재현 범위를 빈틈·겹침 없이 덮는지."""
    a, b = src_range(spec)
    t = a
    for c in spec.cuts:
        s, e = c.src_range
        if abs(s - t) > GAP_OK:
            raise FrogError(f"컷 {c.id} 원본구간 {s:g}-{e:g}: 앞 컷이 {t:g}초에서 끝났다(빈틈·겹침 금지)")
        t = e
    if abs(t - b) > GAP_OK:
        raise FrogError(f"컷이 {t:g}초에서 끝나는데 재현 범위는 {b:g}초까지다 — 원본범위 또는 마지막 컷을 맞춰라")
    return a, b


def gen_lengths(spec: Spec) -> dict:
    """컷마다 생성 길이 — 적은 값, 없으면 구간 길이를 올림(3~10초)."""
    out = {}
    for c in spec.cuts:
        need = c.src_range[1] - c.src_range[0]
        if isinstance(c.gen_len, int):
            out[c.id] = c.gen_len
            continue
        if need > video.MAX_SEC + 0.5:      # 0.5초까지는 살짝 늦춰 맞춘다
            warn(f"컷 {c.id} 원본구간 {need:.1f}초 — 한 번에 {video.MAX_SEC}초까지만 생성된다. 컷을 나눠라")
        out[c.id] = video.secs_for(need)
    return out


def src_words(spec: Spec, a: float, b: float) -> list[dict]:
    """원본 받아쓰기에서 재현 범위 안 단어 → 완성본 기준 시각."""
    f = job_root(spec.folder) / "analysis.json"
    heard = read_json(f).get("받아쓰기", []) if f.exists() else []
    return [{"w": w["w"], "s": round(w["s"] - a, 3), "e": round(min(w["e"], b) - a, 3)}
            for w in heard if a <= w["s"] < b]


def build(spec: Spec, ks: dict) -> dict:
    a, b = check(spec)
    total = round(b - a, 3)
    cuts = []
    for i, c in enumerate(spec.cuts):
        raw = spec.raw.get("컷")[i] or {}
        cuts.append({"id": c.id, "start": round(c.src_range[0] - a, 3), "end": round(c.src_range[1] - a, 3),
                     "clip": str(ks[c.id]["path"]), "speed_floor": c.speed_floor, "crop": raw.get("크롭위"),
                     "skip": float(raw.get("클립시작", 0))})
    words = src_words(spec, a, b)
    wbl = {1: words} if words else {}
    notes: list = []
    parts = place_parts(spec, {}, words, total, notes)
    return {"total": total, "lines": [], "words": wbl, "cuts": cuts, "parts": parts,
            "cues": build_cues(spec, wbl, total) if words else [], "원본범위": [a, b], "notes": notes}


def audio(spec: Spec, tl: dict, out: Path) -> None:
    """원본 소리 그대로(또는 무음) → 48kHz 스테레오 WAV."""
    a, _ = tl["원본범위"]
    if spec.sound == "원본":
        warn("원본 소리를 그대로 쓴다 — 참고 영상 제작자의 것. 강의·시연용, 발행 전 권리 확인")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{tl['total']:.3f}", "-i", spec.source,
             "-vn", "-ac", "2", "-ar", "48000", "-af", f"apad,atrim=duration={tl['total']:.3f}", "-c:a", "pcm_s16le", out])
    else:
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{tl['total']:.3f}", "-c:a", "pcm_s16le", out])
