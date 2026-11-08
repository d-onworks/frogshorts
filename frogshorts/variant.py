"""variant — 같은 틀로 변수·대사만 바꾼 변형. 바뀐 항목만 다시 생성(캐시는 원본 작업과 공유).

  --set 상품=차콜니트          변수
  --set 대사.3="새 문장"        3번 대사 통째로
  --set 컷.B.프롬프트="…"       컷 프롬프트 직접
  --set 컷.B.길이=7             생성 길이(3~10초·자동)
  --set 컷.B.참조="원본@4,원본@4-7"   참조(쉼표로 여러 개)
  --keep B                      재료가 바뀌어도 원본의 컷 B 클립을 그대로 쓴다(돈 0)
결과 폴더 = <작업폴더>/변형/<이름>/ (shorts.yaml · output · 검수)
"""
from __future__ import annotations

from pathlib import Path

from . import changes, clips, make, voice
from .spec import load
from .util import FrogError, dump_yaml, job_root, load_yaml, say


def _apply(doc: dict, sets: list[str]) -> list[str]:
    changed = []
    for s in sets:
        if "=" not in s:
            raise FrogError(f"--set 형식은 키=값: {s}")
        k, v = s.split("=", 1)
        if k.startswith("대사."):
            i = int(k.split(".", 1)[1])
            if not 1 <= i <= len(doc["대사"]):
                raise FrogError(f"대사{i} 가 없다(1~{len(doc['대사'])})")
            doc["대사"][i - 1] = v
        elif k.startswith("컷."):
            _, cid, field = k.split(".", 2)
            cut = next((c for c in doc["컷"] if str(c["id"]) == cid), None)
            if not cut:
                raise FrogError(f"컷 {cid} 가 없다")
            if field in ("클립시작", "속도하한", "크롭위"):
                cut[field] = float(v)
            elif field == "길이":
                cut[field] = v if v == "자동" else int(float(v))
            elif field == "참조":
                cut[field] = [x.strip() for x in v.split(",") if x.strip()]
            else:
                cut[field] = v
        else:
            if k not in doc.get("변수", {}) and k != "가게":
                raise FrogError(f"변수 '{k}' 가 없다 — 있는 변수: {', '.join(doc.get('변수', {}))}")
            if k == "가게":
                doc["가게"] = v
            else:
                doc["변수"][k] = v
        changed.append(k)
    return changed


def variant(folder: Path, name: str, sets: list[str], go: bool = False, max_clips: int = 3,
            keep: list[str] | None = None) -> Path | None:
    src = load_yaml(folder / "shorts.yaml")
    vdir = folder / "변형" / name
    vdir.mkdir(parents=True, exist_ok=True)
    target = vdir / "shorts.yaml"
    doc = load_yaml(target) if target.exists() else src
    changed = _apply(doc, sets)
    doc["출력이름"] = f"{src.get('출력이름') or folder.name}-변형-{name}"
    doc["제목"] = f"{doc.get('가게', '')} — {doc.get('변수', {}).get('상품', '')} (변형 {name})"
    cache = job_root(folder) / "캐시"
    base = load(folder)
    if keep:                                   # 원본 클립을 그대로 쓰라고 못박는다(재료가 바뀌어도)
        kb = clips.keys(base, cache, make.lengths(base, voice.prepare(base, cache, go=False, quiet=True)
                                                    if base.mode == "새대사" else {}))
        for cid in keep:
            cut = next((c for c in doc["컷"] if str(c["id"]) == cid), None)
            if not cut or not kb.get(cid, {}).get("have"):
                raise FrogError(f"--keep {cid}: 원본에 그 컷의 클립이 없다")
            cut["고정"] = kb[cid]["key"]
            changed.append(f"컷.{cid}.고정")
    dump_yaml(doc, target)
    say(f"■ 변형 '{name}' — 바꾼 것: {', '.join(changed) or '없음'}")
    changes.show(changes.explain(base, load(vdir), cache), say)
    return make.make(vdir, go=go, max_clips=max_clips)
