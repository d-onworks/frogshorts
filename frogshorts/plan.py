"""plan — 참고 영상의 틀(틀.yaml) + 우리 가게 브리프 → shorts.yaml 초안.

틀이 주는 것: 화면 구성(흐림 띠), 모든 컷에 붙는 촬영 방식 문단, 프롬프트 끝 문단, 부품 목록.
브리프가 주는 것: 가게·상품 변수, 발음, 꼭읽기, 목소리, 대사, 컷 장면, 부품 글.
브리프에 대사나 컷이 없으면 `claude -p` 로 초안을 받는다(있을 때만, 결과는 사람이 검토).
"""
from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

import yaml

from .providers import video
from .spec import DEFAULT_FINISH, load, parse_range
from .util import FrogError, dump_yaml, load_yaml, read_json, say, warn


def _filled(v) -> str:
    """틀·브리프 문단 — 아직 안 채운 TODO 자리는 빈 것으로 본다."""
    v = str(v or "").strip()
    return "" if v.upper().startswith("TODO") else v


def _prompt(frame: dict, scene: str) -> str:
    parts = [_filled(frame.get("카메라")), scene.strip(), _filled(frame.get("프롬프트끝"))]
    return " ".join(" ".join(p.split()) for p in parts if p)


def _target(folder: Path, frame: dict) -> tuple[float | None, float]:
    """참고 영상 길이(목표)와 말빠르기(음절/초). 길이를 임의로 정하지 않고 참고 영상을 따른다."""
    t = frame.get("목표길이_초")
    if t is None and (folder / "analysis.json").exists():
        t = read_json(folder / "analysis.json")["규격"]["dur"]
    return (float(t) if t else None), float(frame.get("말빠르기_음절초") or 6.0)


def _draft(folder: Path, brief: dict, frame: dict) -> dict:
    """대사·컷이 빈 브리프 → claude -p 로 초안. 길이·컷 수는 참고 영상 길이에 맞춘다."""
    if not shutil.which("claude"):
        raise FrogError("브리프에 대사/컷이 없고 claude CLI 도 없다 — 브리프에 직접 적어라")
    t, rate = _target(folder, frame)
    size = (f"참고 영상과 비슷한 길이(약 {t:.0f}초, 한글 약 {int(t * rate * 0.85)}음절)로 참고 영상 내용을 빠짐없이 옮기고, "
            f"컷은 약 {math.ceil(t / video.MAX_SEC)}개(클립 하나 최대 {video.MAX_SEC}초)"
            if t else "참고 영상 내용을 빠짐없이 옮기는 길이로")
    ask = (
        "너는 쇼츠 대본 작가다. 아래 참고 영상 분석과 틀, 가게 브리프를 보고 우리 가게 버전 쇼츠의 "
        f"'대사'(한국어 문장 리스트, 숫자는 한글로)와 '컷'(id·시작 앵커 '대사N'·영어 장면 묘사 '장면', "
        f"2번째 컷부터 '참조: A@0') 을 YAML 로만 답하라. {size}. 가짜 후기·없는 할인율 금지.\n\n"
        f"## 분석\n{(folder / '분석.md').read_text(encoding='utf-8')[:6000]}\n\n"
        f"## 틀\n{yaml.safe_dump(frame, allow_unicode=True)}\n\n## 브리프\n{yaml.safe_dump(brief, allow_unicode=True)}")
    r = subprocess.run(["claude", "-p", ask], capture_output=True, text=True, timeout=600)
    body = r.stdout.strip().removeprefix("```yaml").removeprefix("```").removesuffix("```")
    got = yaml.safe_load(body) or {}
    if not got.get("대사") or not got.get("컷"):
        raise FrogError(f"초안 받기 실패: {r.stdout[:300]}")
    return got


def _ids(n: int) -> list[str]:
    return [chr(65 + i) if i < 26 else f"Z{i - 25}" for i in range(n)]


def _segments(a: float, b: float, cuts: list[float], lo: float = 3, hi: float = 10) -> list[tuple]:
    """원본 장면 전환을 3~10초 덩어리로 묶는다(생성 한 번 = 덩어리 하나)."""
    pts = [t for t in cuts if a + lo <= t <= b - lo] + [b]
    segs, s, last = [], a, None
    for t in pts:
        if t - s <= hi:
            last = t
            continue
        if last is not None and last - s >= lo:
            segs.append((s, last))
            s = last
        while t - s > hi:
            e = s + hi if t - (s + hi) >= lo else t - lo
            segs.append((s, e))
            s = e
        last = t
    segs.append((s, b))
    return [(round(x, 2), round(y, 2)) for x, y in segs]


def _repro_cuts(folder: Path, brief: dict) -> list[dict]:
    """원본재현 컷 초안 — 구간마다 첫 프레임 이미지 + 앞 3초 동작 영상을 참조로(실측 방식)."""
    facts = read_json(folder / "analysis.json")
    a, b = parse_range(brief["원본범위"]) if brief.get("원본범위") else (0.0, float(facts["규격"]["dur"]))
    segs = _segments(a, b, facts.get("장면전환", []))
    return [{"id": i, "원본구간": f"{s:g}-{e:g}",
             "참조": [f"원본@{s + 0.1:.2f}", f"원본@{s:.2f}-{min(s + 3, e):.2f}"],
             "장면": f"TODO: 원본 {s:g}–{e:g}초 장면(격자 확인)을 인물·동작·카메라까지 원본대로 묘사"}
            for i, (s, e) in zip(_ids(len(segs)), segs)]


def plan(folder: Path, brief_path: Path, force: bool = False, mode: str | None = None) -> Path:
    out = folder / "shorts.yaml"
    if out.exists() and not force:
        raise FrogError(f"{out} 가 이미 있다 — 덮으려면 --force (손으로 고친 내용이 사라진다)")
    frame = load_yaml(folder / "틀.yaml") if (folder / "틀.yaml").exists() else {}
    brief = load_yaml(brief_path)
    for k in ("카메라", "프롬프트끝"):              # 브리프에 적으면 틀보다 먼저
        if _filled(brief.get(k)):
            frame = {**frame, k: brief[k]}
    if not _filled(frame.get("카메라")):
        say("  (틀.yaml 의 '카메라' 칸이 비어 있어 브리프의 장면 묘사만으로 프롬프트를 만든다 — "
            "참고 영상의 촬영 방식을 따르고 싶으면 틀.yaml 이나 브리프에 '카메라:' 한 문단을 적는다)")
    mode = mode or brief.get("모드") or "새대사"
    if mode == "원본재현":
        if not brief.get("컷"):
            brief = {**brief, "컷": _repro_cuts(folder, brief)}
            say(f"원본재현: 장면 전환으로 컷 {len(brief['컷'])}개 초안 — 장면 묘사(TODO)는 격자를 보고 채운다")
    elif not brief.get("대사") or not brief.get("컷"):
        say("브리프에 대사/컷이 없어 claude 로 초안을 받는다…")
        brief = {**brief, **_draft(folder, brief, frame)}
    ref = read_json(folder / "analysis.json")["원본"] if (folder / "analysis.json").exists() else None
    cuts = []
    for c in brief["컷"]:
        d = {"id": c["id"], "시작": c.get("시작", "대사1")}
        if c.get("참조"):
            d["참조"] = c["참조"]
        d["프롬프트"] = _prompt(frame, c.get("장면", ""))
        d.update({k: v for k, v in c.items() if k not in ("id", "시작", "참조", "장면")})   # 크롭위·속도하한 등
        cuts.append(d)
    wanted = frame.get("부품")
    parts = brief.get("부품") or [{"종류": k} for k in (wanted or [])]
    doc = {
        "제목": (f"{brief.get('가게', '')} — {brief.get('변수', {}).get('상품', '')}".strip(" —")
                 or f"원본재현 — {folder.name}"),
        "참고": ref,
        "가게": brief.get("가게", ""),
        "화면": frame.get("화면", {"상단띠": 0, "하단띠": 0}),
        "변수": brief.get("변수", {}),
        "발음": brief.get("발음", {}),
        "꼭읽기": brief.get("꼭읽기", []),
        "목소리": brief.get("목소리") or {"엔진": "gemini"},
        "모드": mode,
        **({"소리": brief["소리"]} if brief.get("소리") else {}),
        **({"원본범위": brief["원본범위"]} if brief.get("원본범위") else {}),
        "대사": brief.get("대사") or [],
        "컷": cuts,
        "부품": parts,
        "마감": {**DEFAULT_FINISH, **(brief.get("마감") or {})},
    }
    dump_yaml(doc, out)
    spec = load(folder)            # 변수·앵커 문법을 지금 검사(틀리면 여기서 멈춘다)
    say(f"✓ {out} — 대사 {len(spec.lines)}줄 · 컷 {len(spec.cuts)}개 · 부품 {len(spec.parts)}개")
    for ln in spec.lines:
        say(f"  대사{ln.n}: {ln.display}" + (f"   (읽기: {ln.speech})" if ln.speech != ln.display else ""))
    _length_check(folder, frame, spec)
    todo = [c.id for c in spec.cuts if "TODO" in c.prompt]
    if todo and spec.mode != "원본재현":
        warn(f"장면 묘사가 TODO 인 컷: {', '.join(todo)} — shorts.yaml 의 '프롬프트'를 채우기 전엔 생성하지 않는다")
    return out


def _length_check(folder: Path, frame: dict, spec) -> None:
    """참고 영상 길이 대비 우리 대사 예상 길이·필요 클립 수를 보여 준다(짧게 줄였으면 경고)."""
    t, rate = _target(folder, frame)
    if spec.mode == "원본재현":
        from . import reproduce
        a, b = reproduce.check(spec)
        lens = reproduce.gen_lengths(spec)
        say(f"  원본재현: 원본 {a:g}–{b:g}초({b - a:.1f}초) · 컷 {len(spec.cuts)}개 · 생성 합계 {sum(lens.values())}초 "
            f"≈ ${sum(video.est_usd(x) for x in lens.values()):.1f} · 소리 {spec.sound}")
        todo = [c.id for c in spec.cuts if "TODO" in c.prompt]
        if todo:
            warn(f"장면 묘사 TODO 컷 {len(todo)}개: {', '.join(todo)} — 채우기 전엔 생성하지 않는다")
        if len(spec.cuts) > 3:
            say(f"  ※ make 의 클립 상한 기본값은 3개 — 실제로 만들 땐 --max-clips {len(spec.cuts)} 로 지정")
        return
    from . import speech
    from .util import job_root
    lens = speech.plan_lengths(spec, job_root(folder) / "캐시")       # 우리 목소리 기준(더빙 있으면 실제 길이)
    est = speech.total(spec, lens)
    need = math.ceil(est / video.MAX_SEC)
    say("  말 길이: " + " · ".join(f"대사{n} {v['초']:.1f}초" for n, v in lens.items())
        + f" ({next(iter(lens.values()))['근거'] if lens else ''})")
    say(f"  길이: 참고 {t:.1f}초 / 우리 대사 예상 {est:.1f}초 · 클립 최소 {need}개 필요(지금 {len(spec.cuts)}개)"
        if t else f"  길이: 우리 대사 예상 {est:.1f}초 · 클립 최소 {need}개 필요(지금 {len(spec.cuts)}개)")
    if t and abs(est - t) > 0.2 * t:
        warn(f"대사 예상 길이({est:.0f}초)가 참고 영상({t:.0f}초)과 20% 넘게 다르다 — 내용을 줄이거나 늘린 게 맞는지 확인")
    if len(spec.cuts) < need:
        warn(f"컷 {len(spec.cuts)}개로는 {est:.0f}초를 못 채운다(클립 하나 최대 {video.MAX_SEC}초) — 느려지거나 멈춘 화면이 생긴다")
    if len(spec.cuts) > 3:
        say(f"  ※ make 의 클립 상한 기본값은 3개 — 실제로 만들 땐 --max-clips {len(spec.cuts)} 로 지정")
