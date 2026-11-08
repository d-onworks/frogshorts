"""쇼츠 부품 — 하이퍼프레임스 합성 한 장(투명 배경)을 만든다. 배경 영상은 ffmpeg 가 따로 깐다.

부품: 배너 · 자막(노래방) · 배지 · 엔딩 · AI표시. 편마다 오버레이를 새로 짜지 않고
shorts.yaml 의 '부품:' 목록과 타임라인 숫자만 바꿔 같은 틀을 다시 쓴다.
"""
from __future__ import annotations

import json
import shutil
from html import escape
from pathlib import Path

from . import parts_board
from .util import PKG

CAPTION_Y = 0.82          # 자막 중심 = 화면 높이 82% (편집 규칙)
W, H = 1080, 1920


def _fit(text: str, width: int, maxpx: int) -> int:
    units = sum(0.32 if ch == " " else (0.62 if ch.isascii() else 1.0) for ch in text) or 1
    return int(min(maxpx, width / units))


def banner(p: dict, dur: float, top: int) -> tuple[str, str]:
    big, small, deco = p.get("큰글", ""), p.get("작은글", ""), p.get("장식", "")
    bs = _fit(big, 960, 84)
    html = f"""
<div id="banner" class="clip" data-start="0" data-duration="{dur}" data-track-index="1">
  <div class="bn-shade" style="height:{top + 40}px"></div>
  <div class="bn-in">
    {f'<div class="bn-deco">{escape(deco)}<svg class="bn-star" viewBox="0 0 20 20"><path d="M10 0L12 8L20 10L12 12L10 20L8 12L0 10L8 8Z"/></svg></div>' if deco else ''}
    <div class="bn-small">{escape(small)}</div>
    <div class="bn-big" style="font-size:{bs}px">{escape(big)}</div>
  </div>
</div>"""
    js = 'tl.fromTo("#banner .bn-in",{y:-30,opacity:0},{y:0,opacity:1,duration:0.45,ease:"power3.out"},0.05);' \
         'tl.fromTo("#banner .bn-deco",{rotation:-14,scale:0.6},{rotation:-8,scale:1,duration:0.5,ease:"back.out(2)"},0.2);'
    return html, js


def captions(cues: list[dict]) -> tuple[str, str]:
    """노래방 자막 — 줄은 captions.py 가 실제 글꼴 너비로 정한 대로(줄마다 블록, <br> 없음).
    강조: 현재 = 말하는 단어만 노랗게(지나간 말은 흰색) · 누적 = 말한 단어는 노란색으로 남는다."""
    html, js = [], []
    for i, c in enumerate(cues):
        rows = c.get("lines") or [list(range(len(c["words"])))]
        body = "".join('<div class="cue-line">' + " ".join(
            f'<span class="w" id="w{i}_{j}">{escape(c["words"][j]["w"])}</span>' for j in row) + "</div>"
            for row in rows)
        html.append(f'<div id="cue{i}" class="clip cue" data-start="{c["s"]}" data-duration="{round(c["e"] - c["s"], 3)}" '
                    f'data-track-index="2"><div class="cue-box"><div class="cue-in">{body}</div></div></div>')
        js.append(f'tl.fromTo("#cue{i} .cue-in",{{y:18,opacity:0}},{{y:0,opacity:1,duration:0.14,ease:"power2.out"}},{c["s"]});')
        keep = c.get("강조") == "누적"
        for j, w in enumerate(c["words"]):
            js.append(f'tl.to("#w{i}_{j}",{{color:"#FFE14D",duration:0.05}},{w["s"]});')
            if not keep:
                nxt = c["words"][j + 1]["s"] if j + 1 < len(c["words"]) else w["e"] + 0.15
                js.append(f'tl.to("#w{i}_{j}",{{color:"#FFFFFF",duration:0.08}},{round(max(nxt, w["s"] + 0.06), 3)});')
    return "\n".join(html), "\n".join(js)


def badge(p: dict, dur: float, idx: int) -> tuple[str, str]:
    at = p.get("at", 0)
    html = (f'<div id="badge{idx}" class="clip badge" data-start="{at}" data-duration="{round(dur - at, 3)}" '
            f'data-track-index="3"><div class="bd-in">{escape(p.get("글", ""))}</div></div>')
    js = f'tl.fromTo("#badge{idx} .bd-in",{{scale:0.4,opacity:0,rotation:-12}},{{scale:1,opacity:1,rotation:-4,duration:0.42,ease:"back.out(2.2)"}},{at});'
    return html, js


def ending(p: dict, dur: float) -> tuple[str, str]:
    at = p.get("at", max(0, dur - 2.5))
    html = (f'<div id="ending" class="clip" data-start="{at}" data-duration="{round(dur - at, 3)}" data-track-index="4">'
            f'<div class="en-in"><div class="en-big">{escape(p.get("글", ""))}</div>'
            f'<div class="en-small">{escape(p.get("작은글", ""))}</div></div></div>')
    js = (f'tl.fromTo("#ending .en-in",{{y:40,opacity:0}},{{y:0,opacity:1,duration:0.5,ease:"power3.out"}},{at});'
          f'tl.fromTo("#ending .en-small",{{opacity:0}},{{opacity:1,duration:0.4}},{round(at + 0.35, 3)});')
    return html, js


def ai_mark(dur: float) -> tuple[str, str]:
    return (f'<div id="aimark" class="clip" data-start="0" data-duration="{dur}" data-track-index="5">'
            f'<div class="ai-in">AI 생성 영상</div></div>', "")


CSS = """
@font-face{font-family:"PBlack";src:url("fonts/Pretendard-Black.otf")}
@font-face{font-family:"PBold";src:url("fonts/Pretendard-Bold.otf")}
@font-face{font-family:"PSemi";src:url("fonts/Pretendard-SemiBold.otf")}
html,body{margin:0;background:transparent}
#root{position:relative;width:100%;height:100%;overflow:hidden;background:transparent}
.clip{position:absolute;inset:0}
.bn-shade{position:absolute;left:0;top:0;width:100%;background:linear-gradient(rgba(70,48,28,.62),rgba(70,48,28,.48) 75%,rgba(70,48,28,0))}
.bn-in{position:absolute;left:0;right:0;top:46px;display:flex;flex-direction:column;align-items:center}
.bn-deco{position:absolute;left:30px;top:-6px;font-family:"PBlack";font-style:italic;font-size:60px;color:#B9F5DA;
  -webkit-text-stroke:2px #173029;text-shadow:0 0 14px rgba(120,220,255,.55);transform-origin:left center}
.bn-star{position:absolute;right:-34px;top:-14px;width:34px;height:34px;fill:#FFEBA0}
.bn-small{font-family:"PBold";font-size:46px;color:#FFE38A;-webkit-text-stroke:5px #23160A;paint-order:stroke fill;
  margin-left:230px;letter-spacing:-0.5px}
.bn-big{font-family:"PBlack";color:#fff;-webkit-text-stroke:8px #14141C;paint-order:stroke fill;margin-top:8px;
  letter-spacing:-1px;white-space:nowrap;text-shadow:0 4px 10px rgba(0,0,0,.45)}
.cue-box{position:absolute;left:70px;right:70px;top:0;height:CAPC2px;display:flex;align-items:center;justify-content:center}
.cue-in{text-align:center;
  font-family:"PBlack";font-size:60px;line-height:1.28;color:rgba(255,255,255,.62);
  -webkit-text-stroke:9px rgba(10,10,12,.92);paint-order:stroke fill;letter-spacing:-0.5px;word-break:keep-all}
.w{display:inline}
.cue-line{display:block;white-space:nowrap}
.badge .bd-in{position:absolute;right:46px;top:430px;padding:16px 30px;border-radius:14px;background:#FFE14D;
  color:#1A1408;font-family:"PBlack";font-size:50px;letter-spacing:-0.5px;box-shadow:0 8px 22px rgba(0,0,0,.35)}
.en-in{position:absolute;left:90px;right:90px;top:1180px;padding:30px 20px 34px;border-radius:22px;
  background:rgba(14,14,16,.78);text-align:center}
.en-big{font-family:"PBlack";font-size:74px;color:#fff;letter-spacing:-1px}
.en-small{font-family:"PSemi";font-size:40px;color:#E8E1D6;margin-top:10px}
.ai-in{position:absolute;right:36px;bottom:40px;font-family:"PSemi";font-size:30px;color:rgba(255,255,255,.85);
  -webkit-text-stroke:3px rgba(0,0,0,.55);paint-order:stroke fill}
"""


def compose(tl: dict, spec_parts: list[dict], top_band: int, out_dir: Path) -> Path:
    """하이퍼프레임스 프로젝트 폴더를 만든다(index.html + 폰트 + gsap)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PKG / "parts/fonts", out_dir / "fonts", dirs_exist_ok=True)
    shutil.copy2(PKG / "parts/vendor/gsap.min.js", out_dir / "gsap.min.js")
    dur = tl["total"]
    blocks, js = [], []
    for i, p in enumerate(tl["parts"]):
        kind = p.get("종류")
        if kind == "배너":
            h, j = banner(p, dur, top_band)
        elif kind == "자막":
            h, j = captions(tl["cues"])
        elif kind == "배지":
            h, j = badge(p, dur, i)
        elif kind == "엔딩":
            h, j = ending(p, dur)
        elif kind == "AI표시":
            h, j = ai_mark(dur)
        elif kind == "랭킹":
            h, j = parts_board.ranking(p, dur, i)
        elif kind == "댓글":
            h, j = parts_board.comment(p, dur, i)
        else:
            raise ValueError(f"모르는 부품: {kind}")
        blocks.append(h)
        js.append(j)
    cap_c2 = int(H * CAPTION_Y) * 2       # 0~2×중심 상자 안에서 세로 가운데 → 중심이 82% 에 온다(transform 없이)
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width={W}, height={H}">
<title>frogshorts parts</title><script src="gsap.min.js"></script>
<style>{CSS.replace("CAPC2", str(cap_c2))}{parts_board.BOARD_CSS}</style></head>
<body>
<div id="root" data-composition-id="main" data-start="0" data-width="{W}" data-height="{H}" data-duration="{dur}" data-fps="24">
{''.join(blocks)}
</div>
<script>
const tl = gsap.timeline({{ paused: true }});
{chr(10).join(x for x in js if x)}
window.__timelines["main"] = tl;
</script>
<!-- data: {escape(json.dumps({"total": dur, "cues": len(tl["cues"])}))} -->
</body></html>"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    return out_dir
