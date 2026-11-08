"""대본 단어 ↔ 받아쓴 단어 맞추기 — 시각은 받아쓰기에서, 글자는 대본에서.

위스퍼는 고유명사를 틀리게 적고(오토프로그→오토프로고), 띄어쓰기도 대본과 다르게 끊는다(오토 프로그).
그래서 대본 단어 1~3개와 받아쓴 단어 1~3개를 한 묶음으로 짝지을 수 있게 하고(동적 계획법),
가장 덜 틀린 짝짓기를 고른다. 단어마다 근거를 남긴다:
  정확  글자가 같은 한 단어
  비슷  한 단어끼리인데 글자가 조금 다름(받아쓰기 오류)
  합침  대본 여러 단어가 받아쓴 한 단어에 들어감 / 나눔  대본 한 단어가 받아쓴 여러 단어로 나뉨
  추정  받아쓰기에 없어 앞뒤 단어 사이를 글자 수 비례로 채움 — 이 단어에 앵커를 걸면 경고한다
비교는 소리 기준(숫자는 읽는 소리로, 발음 표 적용)으로 한다.
"""
from __future__ import annotations

import re

from .speech import spoken
from .util import FrogError

MAX_GROUP = 3
SKIP_SOURCE, SKIP_HEARD = 0.6, 0.4          # 대본 단어가 안 들림 / 받아쓰기에만 있는 군말
GROUP_PENALTY = 0.10
_KEEP = re.compile(r"[0-9A-Za-z가-힣]")


def norm(text: str) -> str:
    return "".join(_KEEP.findall(spoken(text))).lower()


def edit(a: str, b: str) -> int:
    """글자 편집 거리(레벤시테인) — 두 줄만 들고 계산."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _pair_cost(src: str, heard: str, ns: int, nh: int) -> float:
    if src == heard:
        return 0.0 if ns == nh == 1 else GROUP_PENALTY
    width = max(len(src), len(heard), 1)
    return edit(src, heard) / width + GROUP_PENALTY * (ns + nh - 2)


def _kind(ns: int, nh: int, same: bool) -> str:
    if ns == 1 and nh == 1:
        return "정확" if same else "비슷"
    return "합침" if ns > nh else "나눔" if nh > ns else ("정확" if same else "비슷")


def _path(src: list[str], heard: list[str]) -> list[tuple]:
    """최소 비용 짝짓기 → [(대본 시작, 대본 끝, 받아쓴 시작, 받아쓴 끝, 종류)] (끝은 포함 안 함)."""
    n, m = len(src), len(heard)
    INF = float("inf")
    best = [[(INF, 0, None)] * (m + 1) for _ in range(n + 1)]   # (비용, -정확 수, 이전)
    best[0][0] = (0.0, 0, None)
    for i in range(n + 1):
        for j in range(m + 1):
            cost, exact, _ = best[i][j]
            if cost == INF:
                continue

            def offer(ni, nj, add, kind, ex=0):
                cand = (cost + add, exact - ex, (i, j, kind))
                if cand[:2] < best[ni][nj][:2]:
                    best[ni][nj] = cand
            for a in range(1, MAX_GROUP + 1):
                if i + a > n:
                    break
                s = "".join(src[i:i + a])
                for b in range(1, MAX_GROUP + 1):
                    if j + b > m:
                        break
                    h = "".join(heard[j:j + b])
                    same = s == h
                    offer(i + a, j + b, _pair_cost(s, h, a, b), _kind(a, b, same), int(same and a == b == 1))
            if i < n:
                offer(i + 1, j, SKIP_SOURCE, "추정")
            if j < m:
                offer(i, j + 1, SKIP_HEARD, "군말")
    out, i, j = [], n, m
    while (i, j) != (0, 0):
        pi, pj, kind = best[i][j][2]
        out.append((pi, i, pj, j, kind))
        i, j = pi, pj
    return out[::-1]


def align(display: str, heard: list[dict], pron: dict | None = None) -> list[dict]:
    """대본 한 줄(띄어쓰기 단위) + 받아쓴 단어 [{w,s,e}] → [{w, s, e, 근거}]."""
    words = display.split()
    if not words:
        return []
    if not heard:
        raise FrogError(f"받아쓰기가 비었다 — 음성 확인 필요: {display[:30]}")
    pron = pron or {}

    def sound(w: str) -> str:
        for k, v in pron.items():
            w = w.replace(k, v)
        return norm(w)
    src = [sound(w) for w in words]
    hrd = [norm(h["w"]) for h in heard]
    times: list = [None] * len(words)
    kinds = ["추정"] * len(words)
    for si, se, hi, he, kind in _path(src, hrd):
        if se == si or he == hi:
            continue
        t0, t1 = heard[hi]["s"], heard[he - 1]["e"]
        weights = [max(1, len(src[k])) for k in range(si, se)]
        tot, acc = sum(weights), 0
        for k, wgt in zip(range(si, se), weights):
            a = t0 + (t1 - t0) * acc / tot
            acc += wgt
            times[k] = (a, t0 + (t1 - t0) * acc / tot)
            kinds[k] = kind
    _fill(times, [max(1, len(s)) for s in src], heard[0]["s"], heard[-1]["e"])
    out = []
    for w, (s, e), k, sv in zip(words, times, kinds, src):
        if not sv and out:                  # 문장부호만 있는 토큰 → 앞 단어에 붙인다
            out[-1]["w"] += " " + w
            continue
        out.append({"w": w, "s": round(s, 3), "e": round(max(e, s + 0.02), 3), "근거": k})
    return out


def _fill(times: list, weights: list[int], t0: float, t1: float) -> None:
    """못 맞춘 단어를 앞뒤 맞춘 단어 사이에서 글자 수 비례로 채운다(말소리 범위 밖으로는 안 나간다)."""
    i, n = 0, len(times)
    while i < n:
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < n and times[j] is None:
            j += 1
        left = times[i - 1][1] if i > 0 else t0
        right = max(times[j][0] if j < n else t1, left)
        tot, acc = sum(weights[i:j]), 0
        for k in range(i, j):
            a = left + (right - left) * acc / tot
            acc += weights[k]
            times[k] = (a, left + (right - left) * acc / tot)
        i = j


def report(words: list[dict]) -> dict:
    """근거별 단어 수 — 검수 보고에 남긴다."""
    out: dict = {}
    for w in words:
        out[w.get("근거", "?")] = out.get(w.get("근거", "?"), 0) + 1
    return out
