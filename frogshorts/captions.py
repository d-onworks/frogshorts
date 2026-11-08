"""자막 묶기 — 단어 시각을 받아 '한 번에 보이는 자막 덩어리(큐)'와 그 안의 줄을 정한다.

글자 수가 아니라 실제 글꼴 너비(프리텐다드 블랙 60px, 테두리 포함)로 재서 2줄을 넘지 않게 한다.
  - 대본의 '|' 자리에서는 반드시 끊는다(작가가 정한 넘김).
  - 숫자와 단위(구백 원, 15g, 7.5%), 가게 이름처럼 붙어야 읽히는 말은 떼지 않는다.
  - 쉼표·마침표 뒤를 먼저 끊는 자리로 고르고, 한두 글자만 남는 꼬리 덩어리는 만들지 않는다.
  - 두 줄이면 줄 길이가 비슷하게 나눈다(위가 길고 아래 한 단어만 남는 모양 금지).
시각: 큐는 첫 단어보다 조금 먼저 나타나고 마지막 단어 뒤 조금 남되, 다음 큐가 오면 끊는다.
단어 강조 시각은 겹치지 않게 한 번에 한 단어만 켜지도록 정리한다.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

FONT = Path(__file__).parent / "parts/fonts/Pretendard-Black.otf"
SIZE, STROKE, TRACK = 60, 9, -0.5          # parts.py 의 .cue-in 과 같은 값
BOX_W = 1080 - 2 * 70                      # 자막 상자 너비(px)
MAX_LINES = 2
LEAD, TAIL = 0.08, 0.5                     # 큐가 먼저 뜨는 초 · 끝나고 남는 초
SOFT_CHARS = 9                             # 이보다 짧으면 쉼표에서 끊지 않는다(너무 잘게 나뉨 방지)

_NUMWORD = re.compile(r"^[\d,.]+$|^[영일이삼사오육칠팔구십백천만억조점]+$")
_UNIT = re.compile(r"^(원|그램|킬로그램|g|kg|개|알|명|번|초|분|시간|퍼센트|%|배|살|장|벌|켤레|박스|봉|병|잔|층|평|cm|m|ml)")
_PUNCT = re.compile(r"[,.?!…~]$")
_PHRASE_END = re.compile(r"(이|가|은|는|을|를|에|에서|의|도|로|으로|와|과|랑|고|면|서|데|요)$")   # 말마디 끝


@lru_cache(maxsize=1)
def _font():
    return ImageFont.truetype(str(FONT), SIZE)


@lru_cache(maxsize=4096)
def width(text: str) -> float:
    """화면에서 차지하는 너비(px) — 글자 간격·테두리 포함."""
    w = _font().getlength(text)
    return w + TRACK * max(0, len(text) - 1) + STROKE


def _space() -> float:
    return _font().getlength(" ")


def lines_for(words: list[str], names: list[list[str]] | None = None) -> list[list[int]] | None:
    """단어들 → 줄(단어 번호 목록). 1줄에 들어가면 1줄, 아니면 길이가 비슷한 2줄. 안 들어가면 None."""
    if not words:
        return []
    full = " ".join(words)
    if width(full) <= BOX_W:
        return [list(range(len(words)))]
    best = None
    for cut in range(1, len(words)):
        a, b = " ".join(words[:cut]), " ".join(words[cut:])
        wa, wb = width(a), width(b)
        if wa <= BOX_W and wb <= BOX_W:
            prev = words[cut - 1]
            imbalance = abs(wa - wb) / BOX_W * 100           # 두 줄 길이 차이(상자 너비 대비 %)
            score = (imbalance + (0 if _PUNCT.search(prev) else 12 if _PHRASE_END.search(prev) else 40)
                     + (200 if _glued(words, cut - 1) or _in_name(words, cut - 1, names or []) else 0))
            if best is None or score < best[0]:
                best = (score, cut)
    if best is None:
        return None
    return [list(range(best[1])), list(range(best[1], len(words)))]


def _glued(words: list[str], i: int) -> bool:
    """words[i] 와 words[i+1] 사이를 끊으면 안 되나(숫자+단위)."""
    if i + 1 >= len(words):
        return False
    a, b = words[i].rstrip(",.?!"), words[i + 1]
    return bool(_NUMWORD.match(a) and _UNIT.match(b))


def _names(spec) -> list[list[str]]:
    """붙어 다녀야 하는 여러 단어 이름(가게 이름·꼭읽기)."""
    out = []
    vals = [v for v in getattr(spec, "vars", {}).values() if isinstance(v, str) and re.search(r"[가-힣]", v)]
    for t in [*vals, *getattr(spec, "must_say", [])]:
        parts = str(t).split()
        if 1 < len(parts) <= 6:
            out.append(parts)
    return out


def _in_name(words: list[str], i: int, names: list[list[str]]) -> bool:
    """words[i] 뒤를 끊으면 이름이 쪼개지나."""
    for nm in names:
        L = len(nm)
        for start in range(max(0, i - L + 1), i + 1):
            seg = [w.rstrip(",.?!") for w in words[start:start + L]]
            same = len(seg) == L and seg[:-1] == nm[:-1] and seg[-1].startswith(nm[-1])   # '가디건입니다' 도 이름
            if same and start <= i < start + L - 1:
                return True
    return False


def group_line(words: list[str], forced: frozenset, names: list[list[str]]) -> list[list[int]]:
    """한 대사의 단어 → 큐(단어 번호 목록)들."""
    cues, cur = [], []
    for i in range(len(words)):
        cur.append(i)
        text = [words[k] for k in cur]
        if lines_for(text, names) is None:                 # 넘쳤다 → 마지막 단어 전에서 끊는다(붙은 말이면 더 앞에서)
            back = len(cur) - 1
            while back > 1 and (_glued(words, cur[back - 1]) or _in_name(words, cur[back - 1], names)):
                back -= 1
            cues.append(cur[:back])
            cur = cur[back:]
            continue
        no_cut = _glued(words, i) or _in_name(words, i, names)
        chars = sum(len(words[k]) for k in cur)
        if i in forced or (not no_cut and _PUNCT.search(words[i]) and chars >= SOFT_CHARS):
            cues.append(cur)
            cur = []
    if cur:
        cues.append(cur)
    return _no_tiny_tail(cues, words, forced, names)


def _no_tiny_tail(cues: list[list[int]], words: list[str], forced: frozenset,
                  names: list[list[str]]) -> list[list[int]]:
    """두 글자 이하만 남은 큐는 앞 큐에 붙인다(들어가면). 작가가 끊은 자리는 존중."""
    out: list[list[int]] = []
    for c in cues:
        tiny = sum(len(words[k].rstrip(",.?!")) for k in c) <= 2
        if out and tiny and (out[-1][-1] not in forced) and lines_for([words[k] for k in out[-1] + c], names):
            out[-1] = out[-1] + c
        else:
            out.append(c)
    return out


def build_cues(spec, wbl: dict, total: float, mode: str | None = None) -> list[dict]:
    """대사별 단어 시각 → [{s, e, words, lines, 강조}] (lines = 큐 안 단어 번호 줄 목록)."""
    names = _names(spec)
    mode = mode or str(spec.finish.get("자막강조", "현재"))
    forced_of = {ln.n: ln.breaks for ln in getattr(spec, "lines", [])}
    raw = []
    for n in sorted(wbl):
        ws = wbl[n]
        for grp in group_line([w["w"] for w in ws], forced_of.get(n, frozenset()), names):
            raw.append([ws[k] for k in grp])
    out = []
    for i, ws in enumerate(raw):
        s = max(0.0, ws[0]["s"] - LEAD)
        nxt = raw[i + 1][0]["s"] - LEAD if i + 1 < len(raw) else total
        e = min(nxt, ws[-1]["e"] + TAIL)
        lines = lines_for([w["w"] for w in ws], names) or [list(range(len(ws)))]
        out.append({"s": round(s, 3), "e": round(max(e, s + 0.4), 3), "words": _exclusive(ws),
                    "lines": lines, "강조": mode})
    return out


def _exclusive(ws: list[dict], gap: float = 0.04) -> list[dict]:
    """강조 시각이 겹치지 않게 — 각 단어 시작은 앞 단어 시작보다 늦게."""
    out, last = [], -1.0
    for w in ws:
        s = max(w["s"], last + gap)
        out.append({**w, "s": round(s, 3), "e": round(max(w["e"], s + gap), 3)})
        last = s
    return out


def check(cues: list[dict]) -> list[str]:
    """검수 — 줄 수 초과·한 글자 줄."""
    bad = []
    for c in cues:
        if len(c["lines"]) > MAX_LINES:
            bad.append(f"{c['s']:.1f}초 자막이 {len(c['lines'])}줄")
        for ln in c["lines"]:
            text = "".join(c["words"][k]["w"] for k in ln).rstrip(",.?!")
            if len(c["lines"]) > 1 and len(text) <= 1:
                bad.append(f"{c['s']:.1f}초 자막 줄에 한 글자만: '{text}'")
    return bad
