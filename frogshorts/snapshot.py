"""스냅샷 — 완성본에서 '그 말이 나올 때' 화면을 바로 뽑아 본다(앵커로 시각을 고른다, 렌더 없음).

  frogshorts snapshot work/옷가게 --at "대사2:단추,대사4:차콜(끝),12.5"        # 시점마다 한 칸
  frogshorts snapshot work/옷가게 --around "대사2:단추" --span 1 --step 0.125   # 앞뒤 1초를 촘촘히(넘김 확인)
결과 = <작업>/검수/스냅샷_<이름>.jpg (칸마다 시각과 그때 나온 말).
"""
from __future__ import annotations

import re
from pathlib import Path

from .analyze import grid
from .spec import load, resolve
from .util import FrogError, read_json, say


def _timeline(folder: Path) -> dict:
    p = folder / "build" / "timeline.json"
    if not p.exists():
        raise FrogError(f"{p} 가 없다 — 먼저 frogshorts make 로 완성본을 만든다")
    tl = read_json(p)
    tl["words"] = {int(k): v for k, v in tl.get("words", {}).items()}
    return tl


def _output(folder: Path) -> Path:
    outs = sorted((folder / "output").glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if not outs:
        raise FrogError(f"{folder / 'output'} 에 완성본이 없다")
    return outs[-1]


def times_for(folder: Path, at: str | None, around: str | None, span: float, step: float) -> list[float]:
    spec, tl = load(folder), _timeline(folder)
    fps = int(spec.finish["fps"])
    wbl = tl["words"]
    src = wbl.get(1) if spec.mode == "원본재현" else None
    if around:
        c = resolve(around, wbl, src, fps=fps)
        n = int(round(span / step))
        ts = [c + step * k for k in range(-n, n + 1)]
    else:
        ts = [resolve(a.strip(), wbl, src, fps=fps) for a in (at or "").split(",") if a.strip()]
    if not ts:
        raise FrogError("--at 또는 --around 를 준다")
    return [round(min(max(0.0, t), tl["total"] - 0.05), 3) for t in ts]


def snapshot(folder: Path, at: str | None = None, around: str | None = None,
             span: float = 1.0, step: float = 0.125) -> Path:
    folder = Path(folder)
    tl = _timeline(folder)
    ts = times_for(folder, at, around, span, step)
    flat = [w for n in sorted(tl["words"]) for w in tl["words"][n]]
    name = re.sub(r"[^\w가-힣]+", "_", around or at or "")[:30].strip("_")
    out = folder / "검수" / f"스냅샷_{name}.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    grid(_output(folder), ts, flat, out, step if around else 0.6)
    say(f"✓ 스냅샷 {len(ts)}칸 → {out}")
    return out
