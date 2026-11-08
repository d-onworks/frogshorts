"""쇼츠 부품(판 종류) — 랭킹 보드 · 댓글 스티커.

parts.py 의 다른 부품과 같은 꼴이다: 함수가 (html, js) 를 돌려주고 compose() 가 한 index.html 로 묶는다.
BOARD_CSS 는 parts.CSS 뒤에 이어 붙여 쓴다(폰트 @font-face 는 parts.CSS 쪽에 있다).
정직성: 화면에 나오는 글·숫자는 사용자가 shorts.yaml 에 적은 것뿐이다. 빈칸을 그럴듯한 기본값으로 채우지 않는다.
"""
from __future__ import annotations

from functools import lru_cache
from html import escape

from PIL import ImageFont

from .util import PKG, FrogError

_FONT_FILE = {"PBlack": "Pretendard-Black.otf", "PBold": "Pretendard-Bold.otf", "PSemi": "Pretendard-SemiBold.otf"}

# 랭킹 보드가 쓰는 세로 구간 — 배너 아래 ~ 댓글·자막 위
RANK_TOP, RANK_BOTTOM = 300, 1100
RANK_X, RANK_W, RANK_PAD, RANK_GAP = 70, 940, 28, 14
RANK_MAX_ITEMS = 8

# 댓글 카드 — 아래 끝을 y 1440 에 고정하고 글이 길면 위로 자란다(자막 82% 영역과 안 겹치게). 너비는 글에 맞추되 최대 CMT_W
CMT_BOTTOM, CMT_X, CMT_W = 1440, 90, 900
CMT_AVATAR, CMT_PAD_L, CMT_PAD_R, CMT_GAP = 84, 28, 32, 22
CMT_TEXT_W = CMT_W - CMT_PAD_L - CMT_PAD_R - CMT_AVATAR - CMT_GAP - 14   # 14 = 폰트 차이 여유
CMT_MAX_LINES = 3
EXAMPLE_SRC = "예시"
_AVATAR_COLORS = ["#E8664A", "#3E8ED0", "#3AAE7A", "#9B6BD6", "#E0A030", "#D2557E"]


# ── 글자 너비 재기(Pillow 로 Pretendard 실제 너비) ──────────────────────────────
@lru_cache(maxsize=128)
def _font(face: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(PKG / "parts/fonts" / _FONT_FILE[face]), size)


def _width(text: str, face: str, size: int) -> float:
    return _font(face, size).getlength(text)


def _fit_line(text: str, face: str, width: float, maxpx: int, minpx: int = 24) -> int:
    """한 줄에 들어가는 가장 큰 글자 크기."""
    size = int(maxpx)
    while size > minpx and _width(text, face, size) > width:
        size -= 2
    return size


def _wrap(text: str, face: str, size: int, width: float) -> list[str]:
    """띄어쓰기 단위로 접는다. 낱말 하나가 줄보다 길면 그 낱말만 글자 단위로 자른다."""
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if _width(cand, face, size) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if cur and _width(cur + ch, face, size) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
    if cur:
        lines.append(cur)
    return lines


# ── 공통 ──────────────────────────────────────────────────────────────────
def _r(x: float) -> float:
    return round(float(x), 3)


def _span(p: dict, dur: float, what: str) -> tuple[float, float]:
    """부품이 화면에 있는 구간(초). 메인이 앵커를 초로 바꿔 p['at'], p['until'] 에 넣어 준다."""
    at = _r(p.get("at") or 0)
    until = _r(p["until"] if p.get("until") is not None else dur)
    if not (0 <= at < until <= dur + 1e-3):
        raise FrogError(f"{what} 시각이 이상하다: 시작 {at}초 · 끝 {until}초 (영상 길이 {dur}초)")
    return at, until


def _exit(sel: str, at: float, until: float, extra: str = "") -> str:
    """끝나기 직전에 살짝 사라지게(구간이 너무 짧으면 생략 — 그냥 끊긴다)."""
    if until - at < 1.0:
        return ""
    return f'tl.to("{sel}",{{opacity:0{extra},duration:0.25,ease:"power2.in"}},{_r(until - 0.25)});'


# ── 랭킹 보드 ─────────────────────────────────────────────────────────────
def _rank_items(p: dict, at: float, until: float) -> list[dict]:
    raw = p.get("항목") or []
    if not raw:
        raise FrogError("랭킹 보드에 항목이 없다 — 항목: 아래에 {순위, 이름} 을 적어라")
    if len(raw) > RANK_MAX_ITEMS:
        raise FrogError(f"랭킹 항목은 {RANK_MAX_ITEMS}개까지 — 지금 {len(raw)}개")
    out, seen = [], set()
    for it in raw:
        if not isinstance(it, dict):
            raise FrogError(f"랭킹 항목 모양이 이상하다: {it!r} — {{순위: 1, 이름: …}} 꼴로 적어라")
        try:
            rank = int(it.get("순위"))
        except (TypeError, ValueError):
            raise FrogError(f"랭킹 항목 순위가 숫자가 아니다: {it!r}") from None
        name = str(it.get("이름") or "").strip()
        if not name:
            raise FrogError(f"랭킹 {rank}위 이름이 비었다")
        if rank in seen:
            raise FrogError(f"랭킹 순위 {rank} 가 두 번 나온다")
        seen.add(rank)
        pre, t = bool(it.get("미리")), it.get("at")
        if pre and t is not None:
            raise FrogError(f"랭킹 '{name}' 에 '미리'와 '시작'을 같이 적었다 — 하나만")
        t = at if t is None else _r(t)
        if not (at <= t < until):
            raise FrogError(f"랭킹 '{name}' 시작 {t}초가 보드 구간({at}~{until}초) 밖이다")
        out.append({"rank": rank, "name": name, "at": t, "hot": bool(it.get("강조")),
                    "note": str(it.get("덧붙임") or "").strip()})
    return sorted(out, key=lambda x: x["rank"])


def ranking(p: dict, dur: float, idx: int) -> tuple[str, str]:
    """세로 순위표. 항목은 자기 시각에 나타나 보드가 끝날 때까지 자리를 지킨다(1위가 맨 위)."""
    at, until = _span(p, dur, "랭킹 보드")
    items = _rank_items(p, at, until)
    title = str(p.get("제목") or "").strip()
    inner_w = RANK_W - 2 * RANK_PAD
    title_h = 96 if title else 0
    avail = RANK_BOTTOM - RANK_TOP - 2 * RANK_PAD - title_h - RANK_GAP * len(items)
    weights = [1.25 if it["hot"] else 1.0 for it in items]
    unit = min(112.0, avail / sum(weights))

    root = f"rank{idx}"
    rows, js = [], []
    for k, (it, wt) in enumerate(zip(items, weights)):
        h = int(unit * wt)
        num_w, num_px = int(h * 1.1), int(h * 0.4)
        note_px = int(h * 0.3)
        note_w = (_width(it["note"], "PSemi", note_px) + 22) if it["note"] else 0
        name_w = inner_w - num_w - note_w - 28
        name_px = _fit_line(it["name"], "PBlack", name_w, int(h * (0.5 if it["hot"] else 0.44)))
        num = f'<span class="rk-num" style="width:{num_w}px;font-size:{num_px}px">{it["rank"]}위</span>'
        note = f'<span class="rk-note" style="font-size:{note_px}px">{escape(it["note"])}</span>' if it["note"] else ""
        fid = f"{root}_{k}"
        rows.append(
            f'<div class="rk-slot" style="height:{h}px;margin-top:{RANK_GAP}px">'
            f'<div class="rk-ghost">{num}</div>'
            f'<div id="{fid}" class="rk-fill{" hot" if it["hot"] else ""}">{num}'
            f'<span class="rk-name" style="font-size:{name_px}px">{escape(it["name"])}</span>{note}</div></div>')
        if it["hot"]:   # 강조: 톡 튀며 나타나고 한 번 더 들썩
            t = it["at"] if it["at"] > at else _r(at + 0.15)
            js.append(f'tl.fromTo("#{fid}",{{scale:0.7,opacity:0}},{{scale:1,opacity:1,duration:0.45,ease:"back.out(2.6)"}},{t});')
            js.append(f'tl.to("#{fid}",{{scale:1.04,duration:0.16,ease:"sine.inOut",yoyo:true,repeat:1}},{_r(t + 0.5)});')
        elif it["at"] > at:   # 미리 깔린 항목은 판과 같이 나오므로 따로 움직이지 않는다
            js.append(f'tl.fromTo("#{fid}",{{x:-50,opacity:0}},{{x:0,opacity:1,duration:0.35,ease:"power3.out"}},{it["at"]});')

    title_html = ""
    if title:
        title_px = _fit_line(title, "PBlack", inner_w, 60)
        title_html = f'<div class="rk-title" style="height:{title_h}px;font-size:{title_px}px">{escape(title)}</div>'
    html = (f'<div id="{root}" class="clip rank" data-start="{at}" data-duration="{_r(until - at)}" data-track-index="6">'
            f'<div class="rk-panel" style="left:{RANK_X}px;top:{RANK_TOP}px;width:{RANK_W}px;padding:{RANK_PAD}px;'
            f'padding-top:{RANK_PAD - RANK_GAP}px">{title_html}{"".join(rows)}</div></div>')
    js.insert(0, f'tl.fromTo("#{root} .rk-panel",{{y:-24,opacity:0}},{{y:0,opacity:1,duration:0.4,ease:"power3.out"}},{at});')
    js.append(_exit(f"#{root} .rk-panel", at, until))
    return html, "".join(js)


# ── 댓글 스티커 ───────────────────────────────────────────────────────────
def _check_source(p: dict) -> bool:
    """실제 댓글이면 출처 링크, 시연용이면 '예시'. 그 밖은 막는다. 돌려주는 값 = 예시 여부."""
    src = str(p.get("출처") or "").strip()
    if src == EXAMPLE_SRC:
        return True
    if src.startswith(("http://", "https://")):
        return False
    raise FrogError("댓글 스티커는 실제 댓글만 — 출처 링크를 적거나, 시연용이면 출처: 예시")


def comment(p: dict, dur: float, idx: int) -> tuple[str, str]:
    """흰 말풍선 댓글 카드. 아래서 톡 튀어 올라오고 끝에 사라진다."""
    is_example = _check_source(p)
    text = " ".join(str(p.get("글") or "").split())
    if not text:
        raise FrogError("댓글 스티커 '글' 이 비었다")
    at, until = _span(p, dur, "댓글 스티커")
    who = str(p.get("작성자") or "").strip()
    extra = str(p.get("덧붙임") or "").strip()

    size, lines = 50, []
    for size in range(50, 23, -2):          # 3줄 안에 들어갈 때까지 글자를 줄인다
        lines = _wrap(text, "PBold", size, CMT_TEXT_W)
        if len(lines) <= CMT_MAX_LINES:
            break

    initial = who.lstrip("@＠ ")[:1].upper()
    color = _AVATAR_COLORS[sum(map(ord, who)) % len(_AVATAR_COLORS)] if who else "#C9C9CF"
    root = f"cmt{idx}"
    body = "".join(f'<div class="cm-line">{escape(x)}</div>' for x in lines)
    html = (f'<div id="{root}" class="clip cmt" data-start="{at}" data-duration="{_r(until - at)}" data-track-index="7">'
            f'<div class="cm-card" style="left:{CMT_X}px;max-width:{CMT_W}px;bottom:{1920 - CMT_BOTTOM}px;'
            f'padding-left:{CMT_PAD_L}px;padding-right:{CMT_PAD_R}px;gap:{CMT_GAP}px">'
            f'<div class="cm-av" style="width:{CMT_AVATAR}px;height:{CMT_AVATAR}px;background:{color}">{escape(initial)}</div>'
            f'<div class="cm-body">'
            + (f'<div class="cm-who">{escape(who)}</div>' if who else "")
            + f'<div class="cm-text" style="font-size:{size}px">{body}</div>'
            + (f'<div class="cm-extra">{escape(extra)}</div>' if extra else "")
            + '</div>'
            + ('<div class="cm-ex">예시 댓글</div>' if is_example else "")
            + '</div></div>')
    tilt = 1.5 if idx % 2 else -1.5
    js = (f'tl.fromTo("#{root} .cm-card",{{y:90,scale:0.85,rotation:0,opacity:0}},'
          f'{{y:0,scale:1,rotation:{tilt},opacity:1,duration:0.42,ease:"back.out(1.9)"}},{at});'
          + _exit(f"#{root} .cm-card", at, until, ",y:24,scale:0.96"))
    return html, js


BOARD_CSS = """
.rk-panel{position:absolute;box-sizing:border-box;border-radius:28px;background:rgba(12,12,16,.74);
  display:flex;flex-direction:column;box-shadow:0 12px 36px rgba(0,0,0,.35)}
.rk-title{display:flex;align-items:center;justify-content:center;font-family:"PBlack";color:#fff;
  letter-spacing:-1px;white-space:nowrap}
.rk-slot{position:relative;flex:none}
.rk-ghost,.rk-fill{position:absolute;inset:0;box-sizing:border-box;border-radius:18px;display:flex;align-items:center}
.rk-ghost{border:3px dashed rgba(255,255,255,.22)}
.rk-fill{background:rgba(255,255,255,.13);transform-origin:50% 50%}
.rk-fill.hot{background:#FFE14D;box-shadow:0 0 0 4px #FFF3B0,0 10px 28px rgba(255,200,40,.45)}
.rk-num{flex:none;display:flex;align-items:center;justify-content:center;font-family:"PBlack";color:#FFE14D;letter-spacing:-1px}
.rk-ghost .rk-num{color:rgba(255,255,255,.32)}
.rk-name{flex:1;min-width:0;font-family:"PBlack";color:#fff;white-space:nowrap;letter-spacing:-0.5px}
.rk-note{flex:none;padding-right:22px;font-family:"PSemi";color:rgba(255,255,255,.78);white-space:nowrap}
.hot .rk-num,.hot .rk-name{color:#1A1408}
.hot .rk-note{color:#3A2E0A}
.cm-card{position:absolute;box-sizing:border-box;padding-top:26px;padding-bottom:28px;border-radius:34px;background:#fff;
  display:flex;align-items:flex-start;box-shadow:0 14px 34px rgba(0,0,0,.38);transform-origin:20% 100%}
.cm-card::after{content:"";position:absolute;left:64px;bottom:-22px;border-style:solid;border-width:24px 18px 0 0;
  border-color:#fff transparent transparent transparent}
.cm-av{flex:none;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:"PBlack";font-size:42px;color:#fff}
.cm-body{flex:1;min-width:0;display:flex;flex-direction:column}
.cm-who{font-family:"PSemi";font-size:32px;color:#6B6B75;margin-bottom:6px;white-space:nowrap}
.cm-text{font-family:"PBold";line-height:1.3;color:#16161A}
.cm-line{white-space:nowrap}
.cm-extra{font-family:"PSemi";font-size:30px;color:#85858F;margin-top:10px;white-space:nowrap}
.cm-ex{position:absolute;right:26px;top:-22px;padding:6px 16px;border-radius:999px;background:#2A2A30;
  font-family:"PSemi";font-size:28px;color:#fff;letter-spacing:-0.3px}
"""
