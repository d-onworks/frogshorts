"""클립 — 프롬프트 해시 캐시, 참조 프레임, 생성(예산 안에서), 이미 만든 클립 가져오기(adopt), 눈 검수 시트.

캐시 키 = (모델, 해상도, 비율, 프롬프트, 생성 길이, 참조들). 참조 컷이 바뀌면 그 프레임·구간이 바뀌니
뒤 컷도 저절로 다시 만들어진다. 이미 있는 클립 파일은 절대 덮지 않는다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .analyze import grid
from .providers import video
from .spec import Spec
from .util import FrogError, duration, now, run, say, sha, warn, write_json

ASPECT = "9:16"
EXCERPT_WARN = 5.0          # 영상 참조는 짧게(인라인 전송) — 넘으면 경고


def _src_sha(p: Path, _memo: dict = {}) -> str:
    k = (str(p), p.stat().st_mtime, p.stat().st_size)
    if k not in _memo:
        _memo[k] = sha(p)
    return _memo[k]


def _ref_file(cache: Path, tag: str, src: Path, r) -> Path:
    """참조 하나 → 파일. 정지 이미지(png) 또는 영상 구간(mp4, 소리 없음, 가로 720)."""
    d = cache / "참조"
    d.mkdir(parents=True, exist_ok=True)
    if not r.video:
        p = d / f"{tag}_{r.t0:g}.png"
        if not p.exists():
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{r.t0:.3f}", "-i", src, "-frames:v", "1", p])
        return p
    p = d / f"{tag}_{r.t0:g}-{r.t1:g}.mp4"
    if not p.exists():
        if r.t1 - r.t0 > EXCERPT_WARN:
            warn(f"영상 참조 {r.t1 - r.t0:.1f}초 — 길면 요청이 커진다(실측 3초면 충분)")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{r.t0:.3f}", "-i", src, "-t", f"{r.t1 - r.t0:.3f}", "-an",
             "-vf", "scale=720:-2", "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-pix_fmt", "yuv420p", p])
    return p


def keys(spec: Spec, cache: Path, lens: dict | None = None) -> dict[str, dict]:
    """컷마다 {key, refs, secs, path, have}. 앞 컷 클립·생성 길이가 아직 없으면 key=None(wait=이유).

    캐시 키: 길이 지정·여러 참조·영상 참조가 없으면 예전 키 그대로(이미 만든 클립을 다시 쓰려고),
    있으면 (모델, 해상도, 비율, 프롬프트, 길이, 참조들) — 참조 이미지는 바이트, 영상 구간은 원본 해시+구간.
    """
    res = spec.finish["해상도"]
    lens = lens or {}
    out: dict[str, dict] = {}
    for c in spec.cuts:
        secs = lens.get(c.id, c.gen_len if isinstance(c.gen_len, int) else None)
        if c.gen_len == "자동" and secs is None:
            out[c.id] = {"key": None, "refs": [], "secs": None, "path": None, "have": False, "wait": "길이(더빙 뒤 결정)"}
            continue
        files, toks, wait = [], [], None
        for r in c.refs:
            if r.src == "원본":
                if not spec.source:
                    raise FrogError(f"컷 {c.id}: 원본 참조인데 원본 영상이 없다(작업 폴더의 참고.mp4)")
                src, tag = spec.source, "원본" + _src_sha(spec.source)[:8]
            else:
                if r.src not in out:
                    raise FrogError(f"컷 {c.id} 의 참조 {r.src} 는 앞쪽 컷이어야 한다")
                if not out[r.src]["have"]:
                    wait = r.src
                    break
                src, tag = out[r.src]["path"], f"{r.src}_{out[r.src]['key']}"
            f = _ref_file(cache, tag, src, r)
            files.append(f)
            toks.append(["video", tag, r.t0, r.t1] if r.video else sha(f))
        if wait:
            out[c.id] = {"key": None, "refs": [], "secs": secs, "path": None, "have": False, "wait": wait}
            continue
        legacy = secs is None and len(files) <= 1 and not any(r.video for r in c.refs)
        k = (sha("clip", video.MODEL, res, ASPECT, c.prompt, files[0] if files else None) if legacy
             else sha("clip2", video.MODEL, res, ASPECT, c.prompt, secs, toks))
        made = {"모델": video.MODEL, "해상도": res, "프롬프트": c.prompt, "길이": secs, "참조": toks}
        pin = (spec.raw.get("컷")[spec.cuts.index(c)] or {}).get("고정")
        if pin:                                   # 바뀌었어도 이 클립을 그대로 쓴다(명시적 재사용)
            pp = Path(str(pin)).expanduser()
            pp = pp if pp.suffix == ".mp4" else cache / "클립" / f"{pin}.mp4"
            if not pp.exists():
                raise FrogError(f"컷 {c.id} 고정 클립이 없다: {pp}")
            out[c.id] = {"key": pp.stem, "refs": files, "secs": secs, "path": pp, "have": True,
                         "재료": made, "고정": True}
            continue
        p = cache / "클립" / f"{k}.mp4"
        out[c.id] = {"key": k, "refs": files, "secs": secs, "path": p, "have": p.exists(), "재료": made}
    return out


def ensure(spec: Spec, cache: Path, go: bool, max_clips: int, lens: dict | None = None) -> dict[str, dict]:
    """없는 클립을 생성한다(go 이고 상한 안일 때만). 결과 keys 를 돌려준다."""
    made = 0
    while True:
        ks = keys(spec, cache, lens)
        todo = [c for c in spec.cuts if not ks[c.id]["have"] and ks[c.id]["key"]]
        waiting = [c.id for c in spec.cuts if ks[c.id].get("wait")]
        if not todo:
            if waiting and go:
                raise FrogError(f"참조·길이 대기 컷이 남았다: {waiting}")
            return ks
        if not go:
            return ks
        if made >= max_clips:
            raise FrogError(f"클립 상한 {max_clips}개에 도달 — 더 만들려면 --max-clips 를 올려라(돈 듦)")
        c = todo[0]
        if "TODO" in c.prompt:
            raise FrogError(f"컷 {c.id} 장면 묘사가 TODO 다 — 격자를 보고 채운 뒤 생성")
        k = ks[c.id]
        kinds = ", ".join(("영상" if video.is_video(f) else "이미지") for f in k["refs"]) or "없음"
        say(f"  클립 {c.id} 생성 ({made + 1}/{max_clips}) 길이={k['secs'] or '지정 안 함'} 참조={kinds}")
        video.generate(c.prompt, k["path"], k["refs"], spec.finish["해상도"], ASPECT, k["secs"],
                       tag=f"frogshorts/{spec.folder.name}/{c.id}")
        write_json({"컷": c.id, "방법": "생성", "at": now(), "프롬프트": c.prompt, "길이": k["secs"],
                    "참조": [str(f) for f in k["refs"]]}, k["path"].with_suffix(".json"))
        made += 1


def sheets(spec: Spec, ks: dict, out_dir: Path) -> list[Path]:
    """클립마다 12칸 시트 — 가짜 글자·손가락·로고를 눈으로 본다(판정은 사람/클로드)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for c in spec.cuts:
        k = ks[c.id]
        if not k["have"]:
            continue
        p = out_dir / f"클립{c.id}_{k['key'][:6]}.jpg"
        if not p.exists():
            d = duration(k["path"])
            grid(k["path"], [round(d * i / 12, 2) for i in range(12)], [], p, 1.0)
        res.append(p)
    return res


def adopt(spec: Spec, cache: Path, pairs: list[str], lens: dict | None = None) -> None:
    """이미 만든 클립을 현재 프롬프트의 캐시로 등록. 컷 순서대로(참조 프레임이 앞 컷에서 나오므로)."""
    want = dict(p.split("=", 1) for p in pairs)
    for c in spec.cuts:
        if c.id not in want:
            continue
        src = Path(want[c.id]).expanduser()
        if not src.exists():
            raise FrogError(f"없는 파일: {src}")
        k = keys(spec, cache, lens)[c.id]
        if not k["key"]:
            raise FrogError(f"컷 {c.id} 는 참조 컷 {k['wait']} 를 먼저 가져와야 한다")
        if k["have"]:
            warn(f"컷 {c.id} 는 이미 캐시에 있다 — 건너뜀")
            continue
        k["path"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, k["path"])
        write_json({"컷": c.id, "방법": "가져옴(adopt)", "출처": str(src.resolve()), "at": now(),
                    "프롬프트": c.prompt}, k["path"].with_suffix(".json"))
        say(f"  컷 {c.id} ← {src} (캐시 {k['key']})")
