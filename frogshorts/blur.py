"""글씨 흐림 — AI 생성 클립의 옷 라벨·택·간판에 생기는 읽을 수 없는 가짜 글씨를 찾아 흐린다.

영역 하나 = {"box": [x, y, w, h] (화면 비율 0~1, 클립 원본 프레임 기준), "t0": 초, "t1": 초,
             "label": 무슨 글씨인지, "src": "손" | "자동"}  — 시간은 클립 자체 시간(자르기 전).
  · 손으로 적기: shorts.yaml 컷의 `흐림: [{영역: [x, y, w, h], 구간: "a-b"}]` (구간 없으면 클립 전체)
  · 자동 찾기: scan() — 프레임을 뽑아 제미나이 비전에 글씨 상자를 묻고, 시간 순으로 이어 붙여 트랙으로 만든다.
    움직이는 글씨는 트랙 하나를 짧은 토막 여러 개(토막마다 덮는 상자 하나)로 나눈다.
판정("글씨가 맞나")은 모델의 해석이다 — preview() 시트로 사람이 눈으로 확인한다.
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
import time
import urllib.error

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config
from .providers import gemini
from .spec import parse_range
from .util import PKG, FrogError, duration, now, read_json, run, say, sha, warn, write_json


FONT = PKG / "parts/fonts/Pretendard-Bold.otf"
VERSION = 2                 # 묻는 글·응답 형식을 바꾸면 올린다(옛 캐시는 다시 묻는다)
FRAME_W = 768               # 비전에 보낼 프레임 가로(픽셀)
PER_CALL = 4                # 한 요청에 넣는 프레임 수
MAX_AREA = 0.25             # 화면 면적의 이 비율을 넘는 상자는 오탐으로 버린다
PAD = 0.15                  # 덮는 상자 여유(한쪽마다 상자 크기의 15%)
MIN_PAD = 0.006             # 아주 작은 글씨도 최소 이만큼은 여유(화면 비율)
GROW = 1.8                  # 토막 하나의 덮는 상자가 가장 큰 단일 상자의 이 배를 넘으면 토막을 끊는다

ASK = """이 사진들은 AI로 만든 옷가게 영상의 프레임이다(각 사진 앞에 '프레임 번호'가 적혀 있다).
각 프레임에서 읽히거나 글자처럼 보이는 모든 글씨의 상자를 찾아라:
옷 목 라벨·브랜드 라벨, 가격표·행택(hang tag)의 글씨, 간판·안내판, 화면, 포장·쇼핑백 글자, 뜻 없는 가짜 글자 포함.
아주 작아도 글자 모양이면 넣는다. 글씨가 없는 빈 택·빈 종이는 넣지 않는다.
넣지 않는 것: 사람 얼굴·손·팔, 손목시계(숫자판 포함), 단추, 뜨개 무늬, 옷걸이, 옷 주름.
상자는 글씨 부분만 꼭 맞게. 좌표는 그 프레임 기준 [ymin, xmin, ymax, xmax] (0~1000 정수).
JSON 배열로만 답하라: [{"frame": 프레임번호, "box_2d": [ymin, xmin, ymax, xmax], "label": "무슨 글씨(라벨/택/간판 등)"}]
글씨가 하나도 없으면 []."""

SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "frame": {"type": "INTEGER"}, "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
    "label": {"type": "STRING"}}, "required": ["frame", "box_2d"]}}


# ── 상자 계산(비율 좌표 [x, y, w, h]) ───────────────────────────────────────────

def iou(a: list, b: list) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _union(boxes: list[list]) -> list:
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


def _pad(b: list) -> list:
    px, py = max(b[2] * PAD, MIN_PAD), max(b[3] * PAD, MIN_PAD)
    x0, y0 = max(0.0, b[0] - px), max(0.0, b[1] - py)
    x1, y1 = min(1.0, b[0] + b[2] + px), min(1.0, b[1] + b[3] + py)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def _same(a: list, b: list) -> bool:
    """같은 글씨인가 — 겹침 비율이 있거나, 중심이 상자 크기만큼 안쪽에 있으면 같다고 본다."""
    if iou(a, b) >= 0.15:
        return True
    ca, cb = (a[0] + a[2] / 2, a[1] + a[3] / 2), (b[0] + b[2] / 2, b[1] + b[3] / 2)
    reach = max(a[2], a[3], b[2], b[3], 0.03)
    return abs(ca[0] - cb[0]) <= reach and abs(ca[1] - cb[1]) <= reach


def tracks(dets: list[dict], every: float, dur: float) -> list[dict]:
    """프레임별 상자 [{t, box, label}] → 흐림 영역 목록. 큰 상자는 버리고, 같은 글씨는 시간 순으로 잇는다."""
    keep = []
    for d in dets:
        if d["box"][2] * d["box"][3] > MAX_AREA:
            warn(f"흐림: {d['t']:.1f}초 상자가 화면의 {d['box'][2] * d['box'][3]:.0%} — 너무 커서 오탐으로 버림 ({d.get('label', '')})")
            continue
        keep.append(d)
    gap = every * 2.5                        # 이만큼 안 보이다 다시 나오면 같은 트랙으로 잇는다
    live: list[list[dict]] = []
    for t in sorted({d["t"] for d in keep}):
        used: set[int] = set()
        for d in sorted((d for d in keep if d["t"] == t), key=lambda d: -d["box"][2] * d["box"][3]):
            best, score = None, -1.0
            for k, tr in enumerate(live):
                last = tr[-1]
                if k in used or t - last["t"] > gap or not _same(last["box"], d["box"]):
                    continue
                s = iou(last["box"], d["box"])
                if s > score:
                    best, score = k, s
            if best is None:
                live.append([d])
                best = len(live) - 1
            else:
                live[best].append(d)
            used.add(best)
    out = []
    for tr in live:
        out += _segments(tr, every, dur)
    return out


def _segments(tr: list[dict], every: float, dur: float) -> list[dict]:
    """트랙 하나 → 토막들. 글씨가 많이 움직이면 덮는 상자가 커지지 않게 끊는다(토막 구간은 서로 겹친다)."""
    segs, cur = [], [tr[0]]
    for d in tr[1:]:
        boxes = [c["box"] for c in cur] + [d["box"]]
        big = max(b[2] * b[3] for b in boxes)
        u = _union(boxes)
        if u[2] * u[3] > big * GROW:
            segs.append(cur)
            cur = [d]
        else:
            cur.append(d)
    segs.append(cur)
    out = []
    for s in segs:
        box = _pad(_union([d["box"] for d in s]))
        if box[2] * box[3] > MAX_AREA:
            warn(f"흐림: {s[0]['t']:.1f}~{s[-1]['t']:.1f}초 덮는 상자가 너무 커서 버림")
            continue
        labels = [d.get("label", "") for d in s if d.get("label")]
        out.append({"box": box, "t0": round(max(0.0, s[0]["t"] - every), 3),
                    "t1": round(min(dur, s[-1]["t"] + every), 3),
                    "label": max(set(labels), key=labels.count) if labels else "", "src": "자동"})
    return out


# ── 자동 찾기(제미나이 비전) ─────────────────────────────────────────────────────

def _frames(clip: Path, every: float, td: Path) -> list[tuple[float, bytes]]:
    run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vf", f"fps=1/{every:g},scale={FRAME_W}:-2",
         "-q:v", "3", td / "f_%04d.jpg"])
    files = sorted(td.glob("f_*.jpg"))
    return [(round(i * every, 3), f.read_bytes()) for i, f in enumerate(files)]


def _post(model: str, body: dict) -> dict:
    for attempt in range(3):
        try:
            return gemini.generate(model, body)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 2:
                time.sleep(4 * (attempt + 1))
                continue
            raise FrogError(f"글씨 찾기(비전) 실패 — {gemini.explain(e, model)}")
    raise FrogError("글씨 찾기(비전) 호출 실패")


def _ask(model: str, batch: list[tuple[int, bytes]]) -> list[dict]:
    parts: list[dict] = []
    for n, jpg in batch:
        parts += [{"text": f"프레임 {n}"},
                  {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(jpg).decode()}}]
    parts.append({"text": ASK})
    r = _post(model, {"contents": [{"role": "user", "parts": parts}],
                      "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA,
                                           "temperature": 0.1}})
    try:
        text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise FrogError(f"제미나이 비전 응답에 내용이 없다: {str(r)[:300]}")
    m = re.search(r"\[.*\]", text, re.S)
    return json.loads(m.group(0)) if m else []


def _to_box(b2d: list) -> list | None:
    """[ymin, xmin, ymax, xmax] (0~1000) → [x, y, w, h] (0~1). 형식이 틀리면 None."""
    if not isinstance(b2d, list) or len(b2d) != 4:
        return None
    y0, x0, y1, x1 = (min(1000.0, max(0.0, float(v))) / 1000 for v in b2d)
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def detect(clip: Path, cache_dir: Path, every: float = 0.5) -> dict:
    """프레임별 원 상자(캐시). 같은 클립·모델·간격이면 다시 묻지 않는다 — 묶기 값은 캐시를 두고 바꿔 볼 수 있다."""
    model = str(config.get("gemini", "vision_model") or "gemini-3.5-flash")
    p = Path(cache_dir) / "흐림" / f"{sha(Path(clip))}_{model}.json"
    if p.exists():
        c = read_json(p)
        if c.get("판정판") == VERSION and c.get("간격") == every:
            return c
    with tempfile.TemporaryDirectory() as td:
        frames = _frames(Path(clip), every, Path(td))
    if not frames:
        raise FrogError(f"프레임을 못 뽑았다: {clip}")
    calls = (len(frames) + PER_CALL - 1) // PER_CALL
    say("  " + gemini.billing(f"{model} · 프레임 {len(frames)}장 (요청 {calls}번)"))
    dets = []
    for i in range(0, len(frames), PER_CALL):
        batch = list(enumerate(frames[i:i + PER_CALL], start=i))
        for a in _ask(model, [(n, jpg) for n, (_, jpg) in batch]):
            n, box = a.get("frame"), _to_box(a.get("box_2d"))
            if box is None or not isinstance(n, int) or not (i <= n < i + len(batch)):
                continue
            dets.append({"t": frames[n][0], "box": box, "label": str(a.get("label", ""))[:40]})
    c = {"클립": str(clip), "모델": model, "at": now(), "간격": every, "프레임수": len(frames),
         "요청수": calls, "판정판": VERSION, "상자": dets}
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(c, p)
    return c


def scan(clip: Path, cache_dir: Path, every: float = 0.5) -> list[dict]:
    """클립에서 글씨 영역을 자동으로 찾는다 → 흐림 영역 목록(클립 원본 시간)."""
    c = detect(clip, cache_dir, every)
    regs = tracks(c["상자"], every, duration(Path(clip)))
    say(f"  흐림 자동: {Path(clip).name} 상자 {len(c['상자'])}개 → 영역 {len(regs)}개")
    return regs


# ── 손으로 적은 영역 + 자동 합치기 ───────────────────────────────────────────────

def regions_for_cut(cut_raw: dict, clip: Path, cache_dir: Path, auto: bool) -> list[dict]:
    """shorts.yaml 컷의 `흐림` + (auto 면) scan 결과."""
    out = []
    for k, e in enumerate(cut_raw.get("흐림") or []):
        cid = cut_raw.get("id", "?")
        box = e.get("영역") if isinstance(e, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            raise FrogError(f"컷 {cid} 흐림 {k + 1}번: 영역은 [x, y, w, h] (0~1 비율) 네 숫자여야 한다: {e}")
        x, y, w, h = (float(v) for v in box)
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x + 1e-6 and 0 < h <= 1 - y + 1e-6):
            raise FrogError(f"컷 {cid} 흐림 {k + 1}번: 영역이 화면(0~1) 밖이다: {box}")
        span = e.get("구간")
        if span is None:
            t0, t1 = 0.0, duration(Path(clip))
        elif isinstance(span, (list, tuple)) and len(span) == 2:
            t0, t1 = parse_range(f"{span[0]}-{span[1]}")
        else:
            t0, t1 = parse_range(span)
        out.append({"box": [x, y, w, h], "t0": t0, "t1": t1, "label": str(e.get("글씨", "")), "src": "손"})
    if auto:
        out += scan(Path(clip), Path(cache_dir))
    return out


# ── ffmpeg 필터 · 적용 · 검수 시트 ───────────────────────────────────────────────

def _even(v: float) -> int:
    return int(round(v / 2)) * 2


def _px(box: list, W: int, H: int) -> tuple[int, int, int, int]:
    """비율 상자 → 짝수 픽셀 (x, y, w, h), 화면 안으로."""
    x, y = _even(box[0] * W), _even(box[1] * H)
    w, h = max(4, _even(box[2] * W)), max(4, _even(box[3] * H))
    x, y = min(max(0, x), W - 4), min(max(0, y), H - 4)
    return x, y, min(w, W - x), min(h, H - y)


def filter_for(regions: list[dict], src_w: int, src_h: int, label_in: str, label_out: str) -> str:
    """[label_in] → 영역마다 잘라 강하게 흐려 제자리에 덮는다(그 구간에만) → [label_out].
    시간 t 는 입력 프레임 시각(클립 원본) — render 의 trim 앞에 끼운다."""
    li, lo = label_in.strip("[]"), label_out.strip("[]")
    if not regions:
        return f"[{li}]null[{lo}]"
    tag = re.sub(r"\W", "_", lo, flags=re.A)
    n = len(regions)
    parts = [f"[{li}]split={n + 1}[{tag}_m0]" + "".join(f"[{tag}_c{k}]" for k in range(n))]
    for k, r in enumerate(regions):
        x, y, w, h = _px(r["box"], src_w, src_h)
        sigma = max(6.0, min(w, h) / 2.5)          # 글씨 높이의 40% 이상 흐림 → 작은 글씨는 확실히 안 읽힌다
        dst = lo if k == n - 1 else f"{tag}_m{k + 1}"
        parts.append(f"[{tag}_c{k}]crop={w}:{h}:{x}:{y},gblur=sigma={sigma:.1f}:steps=3[{tag}_b{k}]")
        parts.append(f"[{tag}_m{k}][{tag}_b{k}]overlay={x}:{y}:enable='between(t,{r['t0']:.3f},{r['t1']:.3f})'[{dst}]")
    return ";".join(parts)


def size(clip: Path) -> tuple[int, int]:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
             "-of", "csv=p=0", clip])
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def apply(clip: Path, regions: list[dict], out: Path) -> Path:
    """검수용 — 클립 하나에 흐림만 입힌 사본(소리 그대로)."""
    w, h = size(Path(clip))
    fc = filter_for(regions, w, h, "0:v", "vb")
    run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-filter_complex", fc, "-map", "[vb]", "-map", "0:a?",
         "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "copy", out])
    return Path(out)


def preview(clip: Path, regions: list[dict], out_png: Path, cell_w: int = 360) -> Path:
    """검수 시트 6칸 — 그 시각에 켜진 흐림 영역을 빨간 테두리로. 글씨를 덮었는지, 얼굴·손을 덮지 않았는지 본다."""
    d = duration(Path(clip))
    times = [round(d * (i + 0.5) / 6, 2) for i in range(6)]
    font = ImageFont.truetype(str(FONT), 22)
    cells = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            f = Path(td) / f"{i}.jpg"
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", clip, "-frames:v", "1",
                 "-vf", f"scale={cell_w}:-2", "-q:v", "3", f])
            cells.append((t, Image.open(f).convert("RGB")))
    ch, lab = cells[0][1].height, 34
    sheet = Image.new("RGB", (3 * cell_w, 2 * (ch + lab)), (18, 18, 18))
    g = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(cells):
        x, y = (i % 3) * cell_w, (i // 3) * (ch + lab)
        sheet.paste(im, (x, y))
        on = [r for r in regions if r["t0"] <= t <= r["t1"]]
        for r in on:
            bx, by, bw, bh = r["box"]
            g.rectangle([x + bx * cell_w, y + by * ch, x + (bx + bw) * cell_w, y + (by + bh) * ch],
                        outline=(255, 40, 40), width=3)
        g.text((x + 8, y + ch + 5), f"{t:5.2f}초 · 영역 {len(on)}개", font=font, fill=(255, 220, 90))
    Path(out_png).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_png)
    return Path(out_png)
