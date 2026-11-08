"""make — 더빙(새대사) → 클립 생성(Omni) → 단어 정렬 → 앵커 해석 → 배경·부품·마감 → 검수 자료.
원본재현 모드는 더빙 대신 원본 시간·소리를 쓴다(reproduce.py).

돈 드는 호출(클립·TTS)은 --go 일 때만. 모두 캐시에 있으면 --go 없이도 렌더까지 간다(렌더는 무료).
"""
from __future__ import annotations

import re
from pathlib import Path

from . import align, captions, clips, parts, render, reproduce, timeline, voice
from .analyze import grid
from .providers import video
from .spec import Spec, load
from .util import FrogError, duration, job_root, now, read_json, run, say, warn, write_json


def lengths(spec: Spec, vo: dict) -> dict:
    """컷마다 생성 길이(초). 원본재현 = 구간 길이, 새대사 '자동' = 더빙 뒤 필요한 길이."""
    if spec.mode == "원본재현":
        return reproduce.gen_lengths(spec)
    return timeline.gen_lengths(spec, vo)


def guess_lengths(spec: Spec, cache: Path) -> dict:
    """더빙 전 '자동' 길이 어림(비용 계획용) — 말 길이 추정으로 가짜 단어 시각을 만들어 계산한다."""
    from . import speech
    if spec.mode != "새대사" or not any(c.gen_len == "자동" for c in spec.cuts):
        return {}
    est = speech.plan_lengths(spec, cache)
    fake = {}
    for ln in spec.lines:
        toks, d = ln.display.split(), est[ln.n]["초"]
        weights = [max(1, len(t)) for t in toks]
        acc, ws = 0, []
        for t, w in zip(toks, weights):
            a = d * acc / sum(weights)
            acc += w
            ws.append({"w": t, "s": a, "e": d * acc / sum(weights)})
        fake[ln.n] = {"words": ws, "dur": d, "wav": ""}
    return timeline.gen_lengths(spec, fake)


def _plan_report(spec: Spec, ks: dict, vo: dict) -> tuple[int, int]:
    new = [c for c in spec.cuts if not ks[c.id]["have"]]
    new_lines = len(vo.get("계획", []))
    say(f"■ {spec.raw.get('제목', spec.folder.name)} · 모드 {spec.mode} · 소리 {spec.sound}")
    if spec.mode == "원본재현":
        a, b = reproduce.check(spec)
        say(f"  원본 {a:g}–{b:g}초 재현 (완성본 {b - a:.1f}초)")
    for c in spec.cuts:
        k = ks[c.id]
        st = "캐시" if k["have"] else (f"대기 — {k['wait']}" if k.get("wait") else "새로 생성")
        src = ""
        if k["have"] and k["path"].with_suffix(".json").exists():
            src = " · " + read_json(k["path"].with_suffix(".json")).get("방법", "")
        where = f"원본 {c.src_range[0]:g}-{c.src_range[1]:g}" if c.src_range else c.start
        refs = ", ".join(f"{r.src}@{r.t0:g}" + (f"-{r.t1:g}(영상)" if r.video else "") for r in c.refs) or "없음"
        say(f"  컷 {c.id} [{where}] 길이 {k.get('secs') or ('자동' if c.gen_len == '자동' else '지정 안 함(~10초)')}"
            f" · 참조 {refs} · {st}{src}")
    if spec.mode == "새대사":
        say(f"  대사 {len(spec.lines)}줄 중 새 더빙 {new_lines}줄")
    if new:
        usd = sum(video.est_usd(ks[c.id].get("secs")) for c in new)
        say(f"  예상 비용: 클립 {len(new)}개 = 약 ${usd:.1f} (길이 미정은 10초로 계산"
            + (", + 더빙 몇 원)" if new_lines else ")"))
        secs = {ks[c.id].get("secs") for c in new}
        say("  " + video.billing_line(secs.pop() if len(secs) == 1 else None))
    return len(new), new_lines


def _out_name(spec: Spec) -> str:
    t = spec.raw.get("출력이름") or spec.raw.get("제목") or spec.folder.name
    return re.sub(r"[\\/:*?\"<>|\s—]+", "-", t).strip("-")[:60]


def make(folder: Path, go: bool = False, max_clips: int = 3) -> Path | None:
    spec = load(folder)
    cache = job_root(folder) / "캐시"
    dub = spec.mode == "새대사"
    vo = voice.prepare(spec, cache, go=False) if dub else {}
    lens0 = lengths(spec, vo) or guess_lengths(spec, cache)
    ks = clips.keys(spec, cache, lens0)
    new_clips, new_lines = _plan_report(spec, ks, vo)
    if (new_clips or new_lines) and not go:
        say("→ 계획만 출력했다(돈 드는 생성 없음). 실제로 만들려면 --go")
        return None
    todo = [c.id for c in spec.cuts if not ks[c.id]["have"] and "TODO" in c.prompt]
    if todo:                                            # 돈 쓰기 전에(더빙보다 먼저) 멈춘다
        raise FrogError(f"컷 {', '.join(todo)} 의 장면 묘사(프롬프트)가 TODO 다 — {folder / 'shorts.yaml'} 에서 채운 뒤 다시")
    if dub:
        vo = voice.prepare(spec, cache, go=True)        # 더빙 먼저 → '자동' 길이가 정해진다
    lens = lengths(spec, vo)
    ks = clips.ensure(spec, cache, go, max_clips, lens)
    tl = timeline.build(spec, vo, ks) if dub else reproduce.build(spec, ks)
    blur_sheets = _blur(spec, tl, cache, folder / "검수")
    build = folder / "build"
    build.mkdir(exist_ok=True)
    say(f"  타임라인 {tl['total']:.2f}초 · 컷 " + ", ".join(f"{c['id']} {c['start']:.2f}–{c['end']:.2f}" for c in tl["cuts"]))
    sheets = clips.sheets(spec, ks, folder / "검수")
    f = spec.finish
    base_mov, nar = build / "배경.mov", build / "더빙.wav"
    speeds = render.base(tl, spec.screen, f["fps"], f["현장음LUFS"] if dub else None, base_mov)
    if dub:
        render.narration(tl, nar)
        still_bad = voice.recheck(spec, tl, vo, nar)
        for n in vo:
            if isinstance(n, int) and vo[n].get("miss"):
                vo[n]["miss"] = still_bad.get(n, [])
    else:
        reproduce.audio(spec, tl, nar)
    hf = parts.compose(tl, spec.parts, int(spec.screen.get("상단띠", 0)), build / "hf")
    ovl = build / "부품.mov"
    say("  부품 렌더(하이퍼프레임스)…")
    render.overlay(hf, ovl, f["fps"])
    out = folder / "output" / f"{_out_name(spec)}.mp4"
    out.parent.mkdir(exist_ok=True)
    loud = render.master(base_mov, nar, ovl, tl["total"], f["LUFS"], out)
    final_sheet = folder / "검수" / "완성_시트.jpg"
    flat = [w for n in sorted(tl["words"]) for w in tl["words"][n]]
    grid(out, [round(tl["total"] * i / 16, 2) for i in range(16)], flat, final_sheet, tl["total"] / 16)
    _report(spec, folder, out, tl, ks, vo, speeds, loud, sheets + blur_sheets + [final_sheet])
    return out


def _blur(spec: Spec, tl: dict, cache: Path, review: Path) -> list[Path]:
    """컷마다 흐릴 영역(손으로 적은 것 + 마감.글씨흐림: 자동이면 제미나이 비전으로 찾은 것) → tl 컷에 붙인다."""
    from . import blur
    auto = str(spec.finish.get("글씨흐림", "끔")) == "자동"
    raws = spec.raw.get("컷") or []
    sheets = []
    for c, raw in zip(tl["cuts"], raws):
        clip = Path(c["clip"])
        regs = blur.regions_for_cut(raw or {}, clip, cache, auto)
        if not regs:
            continue
        c["blur"], c["src_wh"] = regs, blur.size(clip)
        review.mkdir(parents=True, exist_ok=True)
        sheet = review / f"흐림_{c['id']}_{clip.stem[:6]}.png"
        if not sheet.exists():
            blur.preview(clip, regs, sheet)
        sheets.append(sheet)
        say(f"  컷 {c['id']} 글씨 흐림 {len(regs)}곳 — 검수 {sheet.name}")
    return sheets


def _report(spec, folder, out, tl, ks, vo, speeds, loud, sheets) -> None:
    probe = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", out]).stdout.strip()
    src = {}
    for c in spec.cuts:
        meta = ks[c.id]["path"].with_suffix(".json")
        src[c.id] = read_json(meta).get("방법") if meta.exists() else "?"
    miss = {n: vo[n]["miss"] for n in vo if isinstance(n, int) and vo[n].get("miss")}
    rep = {"at": now(), "출력": str(out), "모드": spec.mode, "소리": spec.sound,
           "길이": round(duration(out), 2), "해상도": probe, **loud,
           "생성길이": {c.id: ks[c.id].get("secs") for c in spec.cuts},
           "컷": [{**c, "속도": s, "출처": src[c["id"]]} for c, s in zip(tl["cuts"], speeds)],
           "대사시작": {ln["n"]: ln["start"] for ln in tl["lines"]},
           "부품시각": [{k: v for k, v in p.items() if k in ("종류", "시작", "at")} for p in tl["parts"]],
           "발음경고": miss, "검수시트": [str(s) for s in sheets],
           "앵커메모": tl.get("notes", []), "자막검사": captions.check(tl["cues"]),
           "단어근거": {n: align.report(ws) for n, ws in tl["words"].items()},
           "흐림": {c["id"]: len(c.get("blur", [])) for c in tl["cuts"]}}
    write_json(rep, folder / "검수" / "보고.json")
    write_json({k: v for k, v in tl.items() if k != "cues"}, folder / "build" / "timeline.json")
    say(f"✓ 완성 {out}\n  {rep['길이']}초 · {probe} · {loud['LUFS']:.1f} LUFS · 컷 속도 {speeds}")
    if miss:
        warn(f"발음 경고 {miss} — 들어 보고 판단")
    for n in rep["앵커메모"] + rep["자막검사"]:
        warn(n)
    say(f"  눈 검수: {folder / '검수'}")


def adopt(folder: Path, pairs: list[str]) -> None:
    spec = load(folder)
    cache = job_root(folder) / "캐시"
    vo = voice.prepare(spec, cache, go=False) if spec.mode == "새대사" else {}
    clips.adopt(spec, cache, pairs, lengths(spec, vo))
