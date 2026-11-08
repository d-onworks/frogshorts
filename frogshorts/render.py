"""렌더 — ①배경 영상(ffmpeg: 컷 이어붙이기·속도 맞춤·흐림 띠) ②더빙 트랙 ③부품 오버레이(하이퍼프레임스 투명 MOV)
④겹치기 + 현장음 + 2단계 라우드니스(-14 LUFS / -1.5 dBTP) → 1080×1920 mp4.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .util import FrogError, duration, has_audio, need, run, say, which

W, H = 1080, 1920


def _cut_filter(i: int, c: dict, screen: dict, fps: int) -> tuple[str, str, float]:
    """컷 하나 → [v{i}] [a{i}] 필터. 클립이 짧으면 속도하한까지 늦추고, 그래도 모자라면 마지막 장면을 잡아 둔다."""
    need_s = c["end"] - c["start"]
    skip = c.get("skip", 0.0)            # 클립 앞부분 건너뛰기(앞 컷 장면을 되풀이하는 도입부 등)
    L = c["clip_dur"] - skip
    f = 1.0 if need_s <= L else max(L / need_s, c["speed_floor"])
    top, bot = int(screen.get("상단띠", 0)), int(screen.get("하단띠", 0))
    mid = H - top - bot
    src = f"[{i}:v]"
    pre = ""
    if c.get("blur"):                       # 생성 클립 속 가짜 글씨 흐림(클립 원본 시각 기준이라 trim 앞)
        from .blur import filter_for
        pre = filter_for(c["blur"], *c["src_wh"], f"{i}:v", f"bl{i}") + ";"
        src = f"[bl{i}]"
    v = f"{pre}{src}trim=start={skip:.3f},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setpts=PTS/{f:.4f},fps={fps}," \
        f"tpad=stop_mode=clone:stop_duration={need_s:.3f},trim=duration={need_s:.3f},setpts=PTS-STARTPTS"
    if top or bot:
        off = int(c["crop"]) if c.get("crop") is not None else (H - mid) // 2
        v += f",split[bg{i}][fg{i}];[bg{i}]boxblur=40:2,eq=brightness=-0.04[bb{i}];" \
             f"[fg{i}]crop={W}:{mid}:0:{off}[ff{i}];[bb{i}][ff{i}]overlay=0:{top}"
    v += f",format=yuv420p[v{i}]"
    if c["has_audio"]:
        a = f"[{i}:a]atrim=start={skip:.3f},asetpts=PTS-STARTPTS,atempo={max(f, 0.5):.4f},apad,atrim=duration={need_s:.3f},asetpts=PTS-STARTPTS," \
            f"aresample=48000,aformat=channel_layouts=stereo[a{i}]"
    else:
        a = f"anullsrc=r=48000:cl=stereo,atrim=duration={need_s:.3f}[a{i}]"
    return v, a, f


def base(tl: dict, screen: dict, fps: int, amb_lufs: float | None, out: Path) -> list[float]:
    cuts = tl["cuts"]
    ins, vs, as_, chain, speeds = [], [], [], [], []
    for i, c in enumerate(cuts):
        c["clip_dur"] = duration(Path(c["clip"]))
        c["has_audio"] = has_audio(Path(c["clip"]))
        ins += ["-i", c["clip"]]
        v, a, f = _cut_filter(i, c, screen, fps)
        chain += [v, a]
        vs.append(f"[v{i}]")
        as_.append(f"[a{i}]")
        speeds.append(round(f, 3))
    n = len(cuts)
    chain.append(f"{''.join(vs)}concat=n={n}:v=1:a=0[vcat]")
    amb = (f"lowpass=7000,loudnorm=I={amb_lufs}:TP=-6:LRA=11" if amb_lufs is not None
           else "volume=0")                       # 원본 소리를 쓰면 생성 클립 현장음은 끈다
    chain.append(f"{''.join(as_)}concat=n={n}:v=0:a=1,{amb},aresample=48000[amb]")
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(chain), "-map", "[vcat]", "-map", "[amb]",
         "-t", f"{tl['total']:.3f}", "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-c:a", "pcm_s16le", out])
    return speeds


def narration(tl: dict, out: Path) -> None:
    ins, parts = [], []
    for i, ln in enumerate(tl["lines"]):
        ins += ["-i", ln["wav"]]
        ms = int(ln["start"] * 1000)
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={ms}|{ms}[n{i}]")
    mix = "".join(f"[n{i}]" for i in range(len(tl["lines"])))
    parts.append(f"{mix}amix=inputs={len(tl['lines'])}:normalize=0:duration=longest,apad,atrim=duration={tl['total']:.3f}[nar]")
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", ";".join(parts), "-map", "[nar]",
         "-c:a", "pcm_s16le", out])


def overlay(hf_dir: Path, out: Path, fps: int) -> None:
    need("hyperframes")
    exe = which("hyperframes") or "hyperframes"          # 윈도우는 hyperframes.cmd
    r = subprocess.run([exe, "render", str(hf_dir), "--format", "mov", "-o", str(out), "--fps", str(fps),
                        "--quiet"], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise FrogError(f"하이퍼프레임스 렌더 실패: {(r.stderr or r.stdout)[-1200:]}")


def master(base_mov: Path, nar: Path, ovl: Path | None, total: float, lufs: float, out: Path) -> dict:
    fade = max(total - 0.45, 0)
    vf = "[0:v][2:v]overlay=0:0:format=auto[vo];[vo]" if ovl else "[0:v]"
    fc = (f"{vf}fade=t=out:st={fade:.3f}:d=0.45,format=yuv420p[v];"
          f"[1:a][0:a]amix=inputs=2:normalize=0:duration=first,afade=t=out:st={fade:.3f}:d=0.45[a]")
    mix = out.with_name(out.stem + "_무마스터.mov")
    ins = ["-i", base_mov, "-i", nar] + (["-i", ovl] if ovl else [])
    run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-c:a", "pcm_s16le", mix])
    m = _measure(mix, lufs)
    an = (f"loudnorm=I={lufs}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
          f"linear=true,aresample=48000")
    run(["ffmpeg", "-v", "error", "-y", "-i", mix, "-c:v", "copy", "-af", an, "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", out])
    mix.unlink(missing_ok=True)
    got = _measure(out, lufs)
    say(f"  마감 {out.name}: {float(got['input_i']):.1f} LUFS / {float(got['input_tp']):.1f} dBTP")
    return {"LUFS": float(got["input_i"]), "dBTP": float(got["input_tp"])}


def _measure(path: Path, lufs: float) -> dict:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                        f"loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    s = r.stderr
    return json.loads(s[s.rindex("{"):s.rindex("}") + 1])
