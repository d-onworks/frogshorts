"""변형 계획 — 원본과 비교해 무엇을 그대로 쓰고 무엇을 왜 다시 만드는지(돈 드는 것과 무료인 것 구분).

판정 단위(바뀌면 다시 만드는 범위):
  더빙   대사 한 줄 = (읽는 글, 목소리, 말투, TTS 모델)        → 그 줄만 다시 + 그 줄 단어 정렬
  클립   컷 하나 = (모델, 해상도, 프롬프트, 생성 길이, 참조)    → 그 컷만 다시. 참조한 앞 컷이 바뀌면 뒤 컷도
  화면   자막 묶음·부품·컷 시각·마감                            → 항상 다시 계산(무료, 렌더만)
'고정:' 을 적은 컷은 재료가 바뀌어도 지정한 클립을 그대로 쓴다(일부러 재사용).
"""
from __future__ import annotations

from pathlib import Path

from . import clips, voice
from .providers import video, tts
from .spec import Spec

FIELDS = ("프롬프트", "길이", "참조", "해상도", "모델")


def _lengths(spec: Spec, cache: Path) -> dict:
    from .make import lengths
    vo = voice.prepare(spec, cache, go=False, quiet=True) if spec.mode == "새대사" else {}
    return lengths(spec, vo)


def clip_reasons(a: dict | None, b: dict) -> list[str]:
    if not a or not a.get("재료"):
        return ["원본에 없는 컷"]
    why = [f for f in FIELDS if a["재료"].get(f) != b["재료"].get(f)]
    if why == ["참조"]:
        return ["참조(앞 컷 클립이 바뀌었거나 참조 구간이 바뀜)"]
    return why or ["알 수 없음"]


def explain(a: Spec, b: Spec, cache: Path) -> dict:
    """원본 a 대비 변형 b 의 계획 → {클립: [...], 더빙: [...], 돈: {...}}."""
    ka, kb = clips.keys(a, cache, _lengths(a, cache)), clips.keys(b, cache, _lengths(b, cache))
    rows, usd = [], 0.0
    for c in b.cuts:
        k, o = kb[c.id], ka.get(c.id)
        if k.get("고정"):
            rows.append((c.id, "고정 — 지정한 클립 그대로", 0.0))
        elif k.get("wait"):
            rows.append((c.id, f"대기 — {k['wait']}", video.est_usd(None)))
            usd += video.est_usd(None)
        elif o and k["key"] == o.get("key"):
            rows.append((c.id, "같음 — 캐시 재사용", 0.0))
        elif k["have"]:
            rows.append((c.id, "바뀜(" + ", ".join(clip_reasons(o, k)) + ") — 같은 재료로 전에 만든 클립이 캐시에 있음", 0.0))
        else:
            cost = video.est_usd(k.get("secs"))
            usd += cost
            rows.append((c.id, "다시 생성 — 바뀐 것: " + ", ".join(clip_reasons(o, k)), cost))
    va = {ln.n: (ln.speech, voice.key(ln, a)) for ln in a.lines}
    lines, todo = [], 0
    for ln in b.lines:
        k = voice.key(ln, b)
        if va.get(ln.n, (None, None))[1] == k:
            lines.append((ln.n, "같음", ln.display))
            continue
        why = "글" if va.get(ln.n, (None,))[0] != ln.speech else "목소리·말투"
        if (cache / "음성" / f"{k}.wav").exists():
            lines.append((ln.n, f"바뀜({why}) — 캐시에 있음", ln.display))
        else:
            todo += 1
            lines.append((ln.n, f"다시 더빙({why} 바뀜)", ln.display))
    return {"클립": rows, "더빙": lines, "돈": {"클립": round(usd, 2), "더빙줄": todo},
            "과금": video.billing_line() if usd else "", "더빙과금": tts.billing_line(b.voice.get("보이스", ""))}


def show(plan: dict, say) -> None:
    for cid, st, _ in plan["클립"]:
        say(f"  컷 {cid}: {st}")
    for n, st, text in plan["더빙"]:
        say(f"  대사{n}: {st} — {text}")
    say("  화면(자막 묶음·부품·컷 시각·마감): 항상 다시 계산 — 무료")
    m = plan["돈"]
    say(f"  돈 드는 것: 클립 약 ${m['클립']:.1f} · 더빙 {m['더빙줄']}줄" + (f"\n  {plan['과금']}" if plan["과금"] else ""))
