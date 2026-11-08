"""shorts.yaml 읽기 — 변수 치환, 표기/소리(발음) 분리, 앵커 해석.

앵커 문법 — 화면이 "몇 초"가 아니라 "대사의 어느 단어"에 붙는다:
  대사3          3번 대사의 첫 단어 시작
  대사3:차콜     3번 대사에서 '차콜'이 들어간 첫 단어의 시작
  대사3:차콜#2   두 번째로 나온 것
  대사3:끝       3번 대사의 마지막 단어 끝
  원본:딸기#2    (원본재현 모드) 원본 받아쓰기에서 '딸기'가 두 번째로 나온 단어
  12.5           (원본재현 모드) 완성본 기준 초
붙어 있던 단어가 대사에서 사라지면 조용히 넘기지 않고 멈춘다.

제작 모드(모드:):
  새대사   (기본) 새 대사를 더빙하고, 그 단어 시각으로 컷·부품 시각을 정한다.
  원본재현  원본의 구간·길이·소리를 따른다. 컷마다 원본구간: a-b, 대사는 없어도 된다.
참조(참조:) — 하나 또는 목록. '@초' 는 정지 이미지, '@a-b' 는 영상 구간:
  A@0 · A@0-3 · 원본@4.2 · 원본@4-7
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .util import FrogError, job_root, load_yaml

DEFAULT_FINISH = {"LUFS": -14, "꼬리": 0.8, "대사사이": 0.3, "앞당김": 0.15, "해상도": "1080p",
                  "현장음LUFS": -32, "fps": 24}


class _Strict(dict):
    def __missing__(self, k):
        raise FrogError(f"변수 {{{k}}} 가 shorts.yaml 의 변수: 에 없다")


@dataclass
class Line:
    n: int
    display: str      # 자막에 보이는 글
    speech: str       # TTS 에 들어가는 글(발음 표 적용)
    breaks: frozenset = frozenset()   # 이 단어 번호(0부터) 뒤에서 자막을 끊는다 — 대본의 '|'


@dataclass
class Ref:
    src: str                          # 컷 id 또는 "원본"
    t0: float
    t1: float | None = None           # 있으면 영상 구간, 없으면 정지 이미지

    @property
    def video(self) -> bool:
        return self.t1 is not None


@dataclass
class Cut:
    id: str
    prompt: str
    start: str
    refs: list = field(default_factory=list)   # [Ref]
    speed_floor: float = 0.8
    gen_len: int | str | None = None  # 생성 길이(초) · "자동" · None(지정 안 함 = 약 10초)
    src_range: tuple | None = None    # (원본재현) 원본 구간 (a, b)


@dataclass
class Spec:
    folder: Path
    raw: dict
    vars: dict
    lines: list[Line]
    cuts: list[Cut]
    parts: list[dict]
    must_say: list[str]
    voice: dict
    screen: dict
    finish: dict = field(default_factory=dict)
    mode: str = "새대사"
    sound: str = "더빙"               # 더빙 · 원본 · 없음
    source: Path | None = None        # 원본 영상(원본재현·원본 참조용)
    source_range: tuple | None = None


def fmt(s: str, v: dict) -> str:
    return str(s).format_map(_Strict(v))


def load(folder: Path) -> Spec:
    p = Path(folder) / "shorts.yaml"
    if not p.exists():
        raise FrogError(f"{p} 가 없다 — 먼저 frogshorts plan")
    raw = load_yaml(p)
    v = {**(raw.get("변수") or {}), "가게": raw.get("가게", "")}
    pron = raw.get("발음") or {}
    lines = []
    for i, t in enumerate(raw.get("대사") or [], 1):
        disp, brk = split_breaks(fmt(t, v))
        sp = disp
        for k, val in pron.items():
            sp = sp.replace(k, val)
        lines.append(Line(i, disp, sp, brk))
    mode = str(raw.get("모드") or "새대사")
    if mode not in MODES:
        raise FrogError(f"모드 '{mode}' 를 모른다 — {' · '.join(MODES)}")
    if not lines and mode == "새대사":
        raise FrogError("대사가 비었다 (원본을 그대로 따라가려면 모드: 원본재현)")
    sound = str(raw.get("소리") or ("원본" if mode == "원본재현" else "더빙"))
    if (mode, sound) not in {("새대사", "더빙"), ("원본재현", "원본"), ("원본재현", "없음")}:
        raise FrogError(f"모드 {mode} 에서 소리: {sound} 는 안 된다 (새대사=더빙, 원본재현=원본|없음)")
    cuts = []
    for c in raw.get("컷") or []:
        refs = c.get("참조") or []
        refs = [parse_ref(str(x)) for x in (refs if isinstance(refs, list) else [refs])]
        L = c.get("길이")
        if L is not None and str(L) != "자동":
            L = int(float(L))
        rng = parse_range(c["원본구간"]) if c.get("원본구간") is not None else None
        if mode == "원본재현" and rng is None:
            raise FrogError(f"원본재현 모드: 컷 {c['id']} 에 원본구간: a-b 가 없다")
        cuts.append(Cut(str(c["id"]), fmt(c["프롬프트"], v), str(c.get("시작", "대사1")), refs,
                        float(c.get("속도하한", 0.8)), L, rng))
    parts = [{k: (fmt(x, v) if isinstance(x, str) and k != "종류" else x) for k, x in d.items()}
             for d in raw.get("부품") or []]
    root = job_root(Path(folder))
    src = root / str(raw.get("원본") or "참고.mp4")
    return Spec(Path(folder), raw, v, lines, cuts, parts,
                [fmt(x, v) for x in raw.get("꼭읽기") or []], raw.get("목소리") or {},
                raw.get("화면") or {"상단띠": 0, "하단띠": 0}, {**DEFAULT_FINISH, **(raw.get("마감") or {})},
                mode, sound, src if src.exists() else None,
                parse_range(raw["원본범위"]) if raw.get("원본범위") is not None else None)


MODES = ("새대사", "원본재현")


def split_breaks(text: str) -> tuple[str, frozenset]:
    """'이 상품은요, | 꽈배기 가디건' → ('이 상품은요, 꽈배기 가디건', {1}) — '|' 앞 단어 번호."""
    toks = text.replace("|", " | ").split()
    words, brk = [], set()
    for t in toks:
        if t == "|":
            if words:
                brk.add(len(words) - 1)
        else:
            words.append(t)
    return " ".join(words), frozenset(brk)


def parse_range(x) -> tuple[float, float]:
    a, sep, b = str(x).replace("~", "-").partition("-")
    try:
        r = (float(a), float(b))
    except ValueError:
        raise FrogError(f"구간 형식은 a-b (초): '{x}'")
    if not sep or r[1] <= r[0]:
        raise FrogError(f"구간 끝이 시작보다 커야 한다: '{x}'")
    return r


def parse_ref(s: str) -> Ref:
    src, at, t = s.partition("@")
    if not at:
        raise FrogError(f"참조 형식은 컷@초 · 컷@a-b · 원본@초 · 원본@a-b: '{s}'")
    if "-" in t or "~" in t:
        a, b = parse_range(t)
        return Ref(src.strip(), a, b)
    return Ref(src.strip(), float(t or 0))


_ANCHOR = re.compile(r"^대사(\d+)(?::(.+?))?(?:#(\d+))?(\(끝\))?$")
_SRC = re.compile(r"^원본:(.+?)(?:#(\d+))?(\(끝\))?$")
_OFFSET = re.compile(r"\s*([+-]\s*\d+(?:\.\d+)?)\s*(초|s|f|프레임)?\s*$")


def split_offset(anchor: str, fps: int = 24) -> tuple[str, float]:
    """'대사3:차콜 -0.2초' → ('대사3:차콜', -0.2) · '+5f' 는 5프레임. 숫자만 있는 앵커는 그대로."""
    a = str(anchor).strip()
    try:
        float(a)
        return a, 0.0
    except ValueError:
        pass
    m = _OFFSET.search(a)
    if not m or m.start() == 0:
        return a, 0.0
    val = float(m.group(1).replace(" ", ""))
    return a[:m.start()].strip(), val / fps if m.group(2) in ("f", "프레임") else val


def _pick(ws: list[dict], word: str, k: int, end: bool, where: str) -> dict:
    hits = [w for w in ws if word in w["w"]]
    if len(hits) < k:
        raise FrogError(f"앵커 깨짐: '{where}' — '{word}'(#{k}) 가 없다. 대사를 고쳤다면 앵커도 고쳐라. "
                        f"지금: {' '.join(w['w'] for w in ws)[:80]}")
    return hits[k - 1]


def resolve(anchor, words_by_line: dict[int, list[dict]], src_words: list[dict] | None = None,
            fps: int = 24, notes: list | None = None) -> float:
    """앵커 → 절대 초. words_by_line[n] = [{w, s, e, 근거}] (이미 절대 시각). src_words = 원본재현 받아쓰기.
    '(끝)' 을 붙이면 그 단어가 끝나는 시각, 뒤에 '+0.2초'·'-5f' 를 붙이면 그만큼 옮긴다.
    받아쓰기에 없어 시각을 '추정'한 단어에 걸리면 notes 에 남긴다(조용히 넘기지 않는다)."""
    a, off = split_offset(anchor, fps)
    try:
        return max(0.0, float(a) + off)        # 숫자 = 완성본 기준 초(원본재현에서 쓴다)
    except ValueError:
        pass
    sm = _SRC.match(a)
    if sm:
        w = _pick(src_words or [], sm.group(1), int(sm.group(2) or 1), bool(sm.group(3)), a)
        return max(0.0, (w["e"] if sm.group(3) else w["s"]) + off)
    m = _ANCHOR.match(a)
    if not m:
        raise FrogError(f"앵커 문법 오류: '{anchor}' (예: 대사3, 대사3:차콜, 대사3:차콜(끝), 대사3:끝, 대사3:차콜 -0.2초)")
    n, word, k, end = int(m.group(1)), m.group(2), int(m.group(3) or 1), bool(m.group(4))
    ws = words_by_line.get(n)
    if not ws:
        raise FrogError(f"앵커 '{anchor}': 대사{n} 이 없다")
    if not word:
        w, t = ws[0], ws[0]["e" if end else "s"]
    elif word == "끝":
        w, t = ws[-1], ws[-1]["e"]
    else:
        w = _pick(ws, word, k, end, a)
        t = w["e"] if end else w["s"]
    if w.get("근거") == "추정" and notes is not None:
        notes.append(f"앵커 '{anchor}' 의 단어 '{w['w']}' 는 받아쓰기에 없어 시각을 추정했다 — 들어 보고 확인")
    return max(0.0, t + off)
