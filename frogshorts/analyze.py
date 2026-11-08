"""analyze — 참고 영상 받기 → 단어 단위 받아쓰기 → 장면 전환 → 단어 붙은 프레임 격자 → 분석.md / 틀.yaml 초안.

도구는 '사실'(시각·단어·프레임)만 만든다. "이 컷이 이 단어에 반응한다" 같은 해석은 클로드(사람)가
분석.md 의 해석 칸과 틀.yaml 에 적는다. 참고 영상의 초 단위 타이밍을 우리 작업에 복사하지 않는다.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import words
from .util import PKG, FrogError, dump_yaml, need, run, say, write_json

FONT = PKG / "parts/fonts/Pretendard-Bold.otf"


def fetch(url: str, out_dir: Path) -> tuple[Path, dict]:
    out = out_dir / "참고.mp4"
    info_p = out_dir / "참고.info.json"
    if not out.exists():
        need("yt-dlp")
        run(["yt-dlp", "-q", "--no-playlist", "-f", "bv*[height<=1920][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
             "--merge-output-format", "mp4", "--write-info-json", "-o", out_dir / "참고.%(ext)s", url])
    from .util import read_json
    info = read_json(info_p) if info_p.exists() else {}
    keep = {k: info.get(k) for k in ("title", "uploader", "channel", "upload_date", "duration",
                                      "view_count", "description", "webpage_url")}
    return out, keep


def probe(path: Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "default=nw=1", path])
    kv = dict(line.split("=", 1) for line in r.stdout.split() if "=" in line)
    num, den = kv["r_frame_rate"].split("/")
    return {"w": int(kv["width"]), "h": int(kv["height"]), "fps": round(int(num) / int(den), 3),
            "dur": round(float(kv["duration"]), 3)}


def scene_cuts(path: Path, thresh: float = 0.30) -> list[float]:
    r = subprocess_stderr(["ffmpeg", "-hide_banner", "-i", path, "-vf",
                           f"select='gt(scene,{thresh})',showinfo", "-an", "-f", "null", "-"])
    return [round(float(t), 2) for t in re.findall(r"pts_time:([\d.]+)", r)]


def subprocess_stderr(cmd: list) -> str:
    import subprocess
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True).stderr


def words_at(heard: list[dict], t0: float, t1: float) -> str:
    return " ".join(h["w"] for h in heard if h["e"] > t0 and h["s"] < t1)


def grid(path: Path, times: list[float], heard: list[dict], out: Path, step: float,
         cols: int = 4, cell_w: int = 270) -> None:
    """칸마다 [프레임 + 시각 + 그 시각 ±step/2 에 나온 단어] — 원본 자막·그래픽은 가리지 않게 단어는 아래에."""
    font = ImageFont.truetype(str(FONT), 20)
    small = ImageFont.truetype(str(FONT), 17)
    cells = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            f = Path(td) / f"{i}.jpg"
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                 "-vf", f"scale={cell_w}:-2", "-q:v", "3", f])
            cells.append((t, Image.open(f).convert("RGB")))
    ch = cells[0][1].height
    lab = 66
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * (ch + lab)), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for i, (t, im) in enumerate(cells):
        x, y = (i % cols) * cell_w, (i // cols) * (ch + lab)
        sheet.paste(im, (x, y))
        d.text((x + 6, y + ch + 4), f"{t:5.1f}s", font=font, fill=(255, 220, 90))
        w = words_at(heard, t - step / 2, t + step / 2)
        d.text((x + 6, y + ch + 32), (w[:19] + "…") if len(w) > 20 else w, font=small, fill=(235, 235, 235))
    sheet.save(out, quality=88)


def analyze(url: str, out_dir: Path, step: float = 1.0) -> Path:
    need("ffmpeg", "ffprobe", words.whisper_bin())
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "격자").mkdir(exist_ok=True)
    src, meta = (Path(url), {"title": Path(url).name}) if Path(url).exists() else fetch(url, out_dir)
    say(f"① 받기 완료: {src}")
    p = probe(src)
    heard = words.transcribe(src)
    write_json(heard, out_dir / "받아쓰기.json")
    say(f"② 받아쓰기 {len(heard)}단어")
    cuts = scene_cuts(src)
    say(f"③ 장면 전환 {len(cuts)}곳: {cuts}")
    grids = []
    ts = [round(i * step, 2) for i in range(int(p["dur"] / step) + 1) if i * step < p["dur"] - 0.05]
    for gi in range(0, len(ts), 12):
        g = out_dir / "격자" / f"격자_{gi // 12 + 1:02d}_{ts[gi]:.0f}s.jpg"
        grid(src, ts[gi:gi + 12], heard, g, step)
        grids.append(g)
    shots = [0.0, *cuts, p["dur"]]
    mids = [round((a + b) / 2, 2) for a, b in zip(shots, shots[1:])]
    grid(src, mids, heard, out_dir / "격자" / "컷대표.jpg", 2.0)
    say(f"④ 격자 {len(grids)}장 + 컷대표")
    spoken = sum(h["e"] - h["s"] for h in heard)
    syll = sum(len(re.findall(r"[가-힣]", h["w"])) for h in heard)
    facts = {"원본": url, "메타": meta, "규격": p, "장면전환": cuts,
             "컷": [{"from": a, "to": b, "단어": words_at(heard, a, b)} for a, b in zip(shots, shots[1:])],
             "말빠르기_음절초": round(syll / spoken, 2) if spoken else None, "받아쓰기": heard}
    write_json(facts, out_dir / "analysis.json")
    _write_md(out_dir, facts, grids)
    if not (out_dir / "틀.yaml").exists():
        dump_yaml(_frame_draft(facts), out_dir / "틀.yaml")
    say(f"⑤ 분석.md · 틀.yaml 초안 → {out_dir}\n   ※ 해석 칸과 틀.yaml 은 사람이(클로드가) 격자를 보고 채운다.")
    return out_dir


def _write_md(out_dir: Path, f: dict, grids: list[Path]) -> None:
    m, p = f["메타"], f["규격"]
    L = [f"# 참고 영상 분석 — {m.get('title') or ''}", "",
         f"- 원본: {f['원본']}  ·  채널: {m.get('channel') or m.get('uploader') or '-'}",
         f"- 규격: {p['w']}×{p['h']} · {p['fps']}fps · {p['dur']}초 · 말빠르기 {f['말빠르기_음절초']} 음절/초", "",
         "## 사실 — 컷과 그때 나온 말 (도구 출력)", "", "| 컷 | 구간(초) | 말 |", "|---|---|---|"]
    for i, c in enumerate(f["컷"], 1):
        L.append(f"| {i} | {c['from']:.2f}–{c['to']:.2f} | {c['단어'] or '(말 없음)'} |")
    L += ["", "## 사실 — 단어 시각", "", " ".join(f"`{h['s']:.1f}` {h['w']}" for h in f["받아쓰기"]), "",
          "## 근거 이미지", "", "- 컷 대표: `격자/컷대표.jpg`",
          *[f"- 1초 격자: `{g.relative_to(out_dir)}`" for g in grids], "",
          "## 해석 (클로드가 격자를 보고 채움 — 근거 시각을 같이 적는다)", "",
          "- 이 영상이 하려는 것:", "- 화면 구성(띠·배너·자막 위치):", "- 카메라·손·동작:",
          "- 말과 그림의 관계(어떤 말에 무엇이 바뀌나):", "- 우리 버전에 옮길 것 / 바꿀 것:", ""]
    (out_dir / "분석.md").write_text("\n".join(L), encoding="utf-8")


def _frame_draft(f: dict) -> dict:
    n = len(f["컷"])
    return {
        "설명": "참고 영상에서 옮길 '틀'. 해석은 사람이 채운다. 초 단위 타이밍은 옮기지 않는다.",
        "화면": {"상단띠": 0, "하단띠": 0},
        "카메라": "TODO: 모든 컷 프롬프트 앞에 붙일 촬영 방식 한 문단(카메라 높이·움직임, 누가·무엇이 나오는지 참고 영상대로)",
        "프롬프트끝": "The frame contains no text, no logos and no signage lettering. Audio: soft ambience only, no speech, no music.",
        "목표길이_초": round(f["규격"]["dur"], 1),     # 참고 영상 내용을 다 담으려면 길이도 비슷해야 한다
        "컷수_참고": n,
        "말빠르기_음절초": f["말빠르기_음절초"],
        "부품": ["배너", "자막", "AI표시"],
    }


def main(url: str, out: str | None) -> None:
    if out is None:
        slug = re.sub(r"[^0-9A-Za-z_-]", "", url.rstrip("/").split("/")[-1].split("=")[-1])[:20] or "ref"
        out = f"work/{slug}"
    try:
        analyze(url, Path(out))
    except FrogError as e:
        raise SystemExit(f"✗ {e}")
