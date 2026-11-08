"""말 길이 미리 재기 — 더빙하기 전에 대사 길이(초)를 어림해 클립 길이·비용·전체 길이를 정한다.

한국어 기준: 한글 음절 + 숫자를 읽는 소리(4,000원 → 사천원) + 영어 단어 음절 → 말빠르기(음절/초)로 나눈다.
쉼표·문장 끝의 쉼과 앞뒤 여유는 따로 더한다. 기본값은 우리 더빙 14줄 실측(2026-11-06)으로 맞췄다:
  쉼표 0.10초 · 문장 끝 0.15초 · 여유 0.10초 · 차분한 목소리 5.0 / 보통 5.7 / 빠른 목소리 6.3 음절/초 (줄당 오차 약 0.3초)
같은 목소리로 더빙한 캐시가 3줄 이상 있으면 그 목소리의 실제 빠르기로 다시 맞춘다(calibrate).
"""
from __future__ import annotations

import math
import re
from pathlib import Path

PACE = {"느림": 5.0, "보통": 5.7, "빠름": 6.3}
COMMA, END, PAD = 0.10, 0.15, 0.10
MIN_SAMPLES = 3

_DIGIT = "영일이삼사오육칠팔구"
_SMALL = ["", "십", "백", "천"]
_BIG = ["", "만", "억", "조"]
_UNITS = {"%": "퍼센트", "kg": "킬로그램", "g": "그램", "ml": "밀리리터", "l": "리터", "cm": "센티미터",
          "mm": "밀리미터", "m": "미터", "km": "킬로미터", "x": "배"}
_NATIVE_ONES = ["", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"]
_NATIVE_TENS = ["", "열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"]
_COUNTER = re.compile(r"\s*(시간|시|개|명|살|마리|번째|번|잔|병|권|켤레|벌|송이|장|알|봉지|가지|달|군데|그릇|채)")
_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(kg|ml|cm|mm|km|%|g|l|m|x)?(?![A-Za-z])", re.I)
_LATIN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _int_reading(n: int) -> str:
    """정수 → 한자어 읽기(만 단위로 끊는다). 1 은 앞에서 '일' 을 생략(십·백·천·만)."""
    if n == 0:
        return "영"
    out, group = "", 0
    while n > 0:
        n, chunk = divmod(n, 10000)
        if chunk:
            s = ""
            for i, d in enumerate(reversed(f"{chunk:04d}")):
                d = int(d)
                if d:
                    s = ("" if d == 1 and i > 0 else _DIGIT[d]) + _SMALL[i] + s
            if group == 1 and s == "일":
                s = ""                              # 10000 → '만'(일만 아님)
            out = s + _BIG[group] + out
        group += 1
    return out


def read_number(token: str) -> str:
    """'4,000' → '사천', '7.5' → '칠점오', '15' → '십오'."""
    raw = token.replace(",", "")
    whole, _, frac = raw.partition(".")
    s = _int_reading(int(whole or 0))
    if frac:
        s += "점" + "".join(_DIGIT[int(c)] for c in frac)
    return s


def native(n: int) -> str | None:
    """1~99 → 우리말 숫자(세는 말 앞 꼴: 한·두·세·네·스무). 범위 밖이면 None."""
    if not 1 <= n <= 99:
        return None
    t, o = divmod(n, 10)
    if t == 2 and o == 0:
        return "스무"
    return _NATIVE_TENS[t] + _NATIVE_ONES[o]


def spoken(text: str) -> str:
    """화면 글 → 소리 나는 글(숫자·단위만 바꾼다). 발음 표는 spec 이 먼저 적용한다.
    시·개·명·살·알처럼 세는 말 앞의 1~99 는 우리말로 읽는다(2시 → 두시, 3알 → 세알)."""
    def one(m: re.Match) -> str:
        num, unit = m.group(1), (m.group(2) or "").lower()
        if not unit and "." not in num and "," not in num and _COUNTER.match(text, m.end()):
            nat = native(int(num))
            if nat:
                return nat
        return read_number(num) + _UNITS.get(unit, "")
    return _NUM.sub(one, text)


def _latin_syllables(word: str) -> int:
    w = word.lower()
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def units(text: str) -> int:
    """소리 나는 음절 수."""
    t = spoken(text)
    return len(re.findall(r"[가-힣]", t)) + sum(_latin_syllables(w) for w in _LATIN.findall(t))


def pauses(text: str) -> float:
    inner = text.rstrip(" .?!…~")
    return COMMA * len(re.findall(r"[,，]", inner)) + END * len(re.findall(r"[.?!…]+", inner))


def estimate(text: str, rate: float = PACE["보통"], pad: float = PAD) -> float:
    """대사 한 줄의 예상 길이(초)."""
    n = units(text)
    if n == 0:
        return 0.0
    return round(n / rate + pauses(text) + pad, 2)


def pace_of(voice: dict) -> float:
    """shorts.yaml 목소리: → 기본 빠르기. 빠르기: 숫자(음절/초) 또는 느림·보통·빠름."""
    p = voice.get("빠르기", "보통")
    try:
        return float(p)
    except (TypeError, ValueError):
        return PACE.get(str(p), PACE["보통"])


def calibrate(samples: list[tuple[str, float]]) -> float | None:
    """(읽은 글, 실제 초) 목록 → 이 목소리의 음절/초. 표본이 모자라면 None."""
    rs = []
    for text, dur in samples:
        n = units(text)
        rest = dur - pauses(text) - PAD
        if n >= 5 and rest > 0.5:
            rs.append(n / rest)
    if len(rs) < MIN_SAMPLES:
        return None
    rs.sort()
    return round(rs[len(rs) // 2], 2)                 # 가운데 값 — 튀는 줄에 덜 흔들린다


def rate_for(spec, cache: Path) -> tuple[float, str]:
    """이 작업 목소리의 빠르기와 근거. 캐시에 같은 목소리 더빙이 있으면 그걸로 맞춘다."""
    from . import voice                                # 순환 import 피하기
    from .util import duration, read_json
    d = cache / "음성"
    samples = []
    for ln in spec.lines:
        k = voice.key(ln, spec)
        wav, meta = d / f"{k}.wav", d / f"{k}.json"
        if wav.exists() and meta.exists():
            samples.append((read_json(meta).get("읽기", ln.speech), duration(wav)))
    r = calibrate(samples)
    if r:
        return r, f"이 목소리 실측 {len(samples)}줄"
    return pace_of(spec.voice), f"기본값({spec.voice.get('빠르기', '보통')})"


def plan_lengths(spec, cache: Path) -> dict:
    """대사마다 예상 초 — 더빙이 이미 있으면 실제 길이를 쓴다."""
    from . import voice
    from .util import duration
    rate, why = rate_for(spec, cache)
    out = {}
    for ln in spec.lines:
        wav = cache / "음성" / f"{voice.key(ln, spec)}.wav"
        out[ln.n] = {"초": round(duration(wav), 2), "근거": "더빙"} if wav.exists() else \
            {"초": estimate(ln.speech, rate), "근거": f"추정 · {why} {rate}음절/초"}
    return out


def total(spec, lens: dict) -> float:
    f = spec.finish
    return round(0.3 + sum(v["초"] for v in lens.values()) + f["대사사이"] * max(0, len(lens) - 1) + f["꼬리"], 2)


def clips_needed(seconds: float, per_clip: int = 10) -> int:
    return max(1, math.ceil(seconds / per_clip))
