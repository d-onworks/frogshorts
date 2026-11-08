"""타임라인 — 실제 더빙 길이·단어 시각으로 대사 위치를 정하고, 앵커를 초로 바꾼다.

'몇 초'는 어디에도 적지 않는다. 대사가 바뀌면 이 계산이 다시 돌아 컷·부품이 저절로 따라온다.
"""
from __future__ import annotations

import math

from .providers import video
from .spec import Spec, resolve, split_offset
from .util import FrogError, warn

LEAD_IN = 0.3          # 첫 대사 전 숨


def layout(spec: Spec, vo: dict) -> tuple[list, dict, float]:
    """대사를 차례로 놓는다 → (lines, 절대 단어 시각, 전체 길이)."""
    f = spec.finish
    t = LEAD_IN
    lines, wbl = [], {}
    for ln in spec.lines:
        o = vo[ln.n]
        wbl[ln.n] = [{**w, "s": round(t + w["s"], 3), "e": round(t + w["e"], 3)} for w in o["words"]]   # 근거도 함께
        lines.append({"n": ln.n, "start": round(t, 3), "dur": o["dur"], "wav": str(o["wav"])})
        t += o["dur"] + f["대사사이"]
    return lines, wbl, round(t - f["대사사이"] + f["꼬리"], 3)


def snap(t: float, fps: int) -> float:
    """프레임 칸에 맞춘다(시작 쪽은 내림) — 화면은 프레임 단위로만 바뀐다."""
    return round(math.floor(t * fps + 1e-6) / fps, 4)


def spans(spec: Spec, wbl: dict, total: float, notes: list | None = None) -> list[tuple[float, float]]:
    """컷마다 (시작, 끝) — 앵커에서 앞당김만큼 먼저 시작(앵커에 오프셋을 적으면 그 값만), 다음 컷 시작에서 끝."""
    fps = int(spec.finish["fps"])
    starts = []
    for i, c in enumerate(spec.cuts):
        _, own = split_offset(c.start, fps)
        lead = 0.0 if own else spec.finish["앞당김"]
        s = 0.0 if i == 0 else max(0.0, resolve(c.start, wbl, fps=fps, notes=notes) - lead)
        starts.append(snap(s, fps))
    if starts != sorted(starts):
        raise FrogError(f"컷 시작 순서가 꼬였다: {list(zip([c.id for c in spec.cuts], starts))}")
    return [(s, round(starts[i + 1] if i + 1 < len(starts) else total, 3)) for i, s in enumerate(starts)]


def _skip(spec: Spec, i: int) -> float:
    return float((spec.raw.get("컷")[i] or {}).get("클립시작", 0))


def gen_lengths(spec: Spec, vo: dict) -> dict:
    """길이: 자동 인 컷의 생성 길이(초) — 더빙이 다 있어야 계산된다. 10초를 넘으면 경고."""
    auto = [c for c in spec.cuts if c.gen_len == "자동"]
    if not auto or any(ln.n not in vo or not vo[ln.n].get("words") for ln in spec.lines):
        return {}
    _, wbl, total = layout(spec, vo)
    out = {}
    for i, (c, (s, e)) in enumerate(zip(spec.cuts, spans(spec, wbl, total))):
        if c.gen_len != "자동":
            continue
        need = e - s + _skip(spec, i)
        if need > video.MAX_SEC + 0.5:      # 0.5초까지는 살짝 늦춰 맞춘다
            warn(f"컷 {c.id} 는 {need:.1f}초가 필요한데 한 번에 {video.MAX_SEC}초까지만 생성된다 — 컷을 나누는 게 낫다")
        out[c.id] = video.secs_for(need)
    return out


def build(spec: Spec, vo: dict, ks: dict) -> dict:
    lines, wbl, total = layout(spec, vo)
    notes: list = []
    cuts = []
    for i, (c, (s, e)) in enumerate(zip(spec.cuts, spans(spec, wbl, total, notes))):
        k = ks[c.id]
        cuts.append({"id": c.id, "start": s, "end": e, "clip": str(k["path"]),
                     "speed_floor": c.speed_floor, "crop": (spec.raw.get("컷")[i] or {}).get("크롭위"),
                     "skip": _skip(spec, i)})
    parts = place_parts(spec, wbl, None, total, notes)
    from .captions import build_cues
    return {"total": total, "lines": lines, "words": wbl, "cuts": cuts, "parts": parts,
            "cues": build_cues(spec, wbl, total), "notes": list(dict.fromkeys(notes))}


def place_parts(spec: Spec, wbl: dict, src_words: list | None, total: float, notes: list) -> list[dict]:
    """부품마다 시작(at)·끝(until) 초 — 랭킹 항목은 항목별 시작도."""
    fps = int(spec.finish["fps"])

    def at(anchor):
        return snap(resolve(anchor, wbl, src_words, fps=fps, notes=notes), fps)
    out = []
    for p in spec.parts:
        q = dict(p)
        if p.get("시작") is not None:
            q["at"] = at(p["시작"])
        if p.get("끝") is not None:
            q["until"] = min(total, at(p["끝"]))
        if isinstance(p.get("항목"), list):
            q["항목"] = [{**it, **({"at": at(it["시작"])} if it.get("시작") is not None else {})}
                       for it in p["항목"]]
        out.append(q)
    return out
