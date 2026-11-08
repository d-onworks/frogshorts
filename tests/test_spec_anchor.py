"""작업파일 읽기 · 단어 앵커 · 자막 묶기 · 변형 판정 — 돈 안 드는 부분."""
from pathlib import Path

import pytest
import yaml

from frogshorts import captions, changes, clips
from frogshorts.spec import load, parse_ref, resolve, split_breaks, split_offset
from frogshorts.util import FrogError

WBL = {1: [{"w": "이", "s": 0.3, "e": 0.4}, {"w": "상품은요,", "s": 0.4, "e": 1.0},
           {"w": "차콜도", "s": 1.2, "e": 1.8, "근거": "추정"}, {"w": "차콜", "s": 2.0, "e": 2.5}],
       2: [{"w": "가격", "s": 3.0, "e": 3.4}]}


def test_anchor_forms():
    assert resolve("대사1", WBL) == 0.3
    assert resolve("대사1:끝", WBL) == 2.5
    assert resolve("대사1:차콜#2", WBL) == 2.0
    assert resolve("대사1:차콜#2(끝)", WBL) == 2.5
    assert resolve("대사2:가격 -0.2초", WBL) == pytest.approx(2.8)
    assert resolve("대사2:가격 +12f", WBL, fps=24) == pytest.approx(3.5)
    assert resolve("12.5", WBL) == 12.5


def test_anchor_broken_word_stops():
    with pytest.raises(FrogError, match="앵커 깨짐"):
        resolve("대사1:네이비", WBL)
    with pytest.raises(FrogError):
        resolve("대사9", WBL)
    with pytest.raises(FrogError, match="문법"):
        resolve("세번째문장", WBL)


def test_anchor_on_estimated_word_leaves_note():
    notes = []
    resolve("대사1:차콜", WBL, notes=notes)
    assert notes and "추정" in notes[0]


def test_offset_and_breaks_parsing():
    assert split_offset("대사3:차콜 -0.2초") == ("대사3:차콜", -0.2)
    assert split_offset("대사3") == ("대사3", 0.0)
    assert split_breaks("이 상품은요, | 꽈배기 가디건") == ("이 상품은요, 꽈배기 가디건", frozenset({1}))


def test_ref_parsing():
    assert parse_ref("A@0").video is False
    r = parse_ref("원본@4-7")
    assert r.src == "원본" and r.video and (r.t0, r.t1) == (4, 7)
    with pytest.raises(FrogError):
        parse_ref("A")


def _job(tmp: Path, lines, cuts, **extra) -> Path:
    (tmp / "분석.md").write_text("시험", encoding="utf-8")       # 작업 루트 표시
    doc = {"제목": "시험", "가게": "초록연못 빵집", "변수": {"상품": "우유 식빵"}, "대사": lines,
           "컷": cuts, "부품": [{"종류": "자막"}], "목소리": {"보이스": "Kore"}, **extra}
    (tmp / "shorts.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return tmp


def test_spec_loads_vars_breaks_and_lengths(tmp_path):
    j = _job(tmp_path, ["오늘은 {상품}, | 단돈 천 원"], [{"id": "A", "프롬프트": "bread {상품}", "길이": "7"},
                                                   {"id": "B", "프롬프트": "x", "시작": "대사1:단돈", "길이": "자동",
                                                    "참조": ["A@0", "원본@1-3"]}])
    s = load(j)
    assert s.lines[0].display == "오늘은 우유 식빵, 단돈 천 원" and s.lines[0].breaks == frozenset({2})
    assert s.cuts[0].gen_len == 7 and s.cuts[1].gen_len == "자동" and s.cuts[1].refs[1].video


def test_spec_rejects_unknown_var_and_mode(tmp_path):
    with pytest.raises(FrogError, match="변수"):
        load(_job(tmp_path, ["{없는값}"], [{"id": "A", "프롬프트": "x"}]))
    with pytest.raises(FrogError, match="모드"):
        load(_job(tmp_path, ["가"], [{"id": "A", "프롬프트": "x"}], 모드="아무거나"))
    with pytest.raises(FrogError, match="원본구간"):
        load(_job(tmp_path, [], [{"id": "A", "프롬프트": "x"}], 모드="원본재현"))


def test_captions_fit_two_lines_and_keep_number_with_unit():
    words = "그래서 한 알 재료비는 삼백 원에서 구백 원 사이. 오토프로그 떡집, 오늘 빚은 것만 팝니다.".split()
    groups = captions.group_line(words, frozenset(), [["오토프로그", "떡집"]])
    for g in groups:
        text = [words[k] for k in g]
        lines = captions.lines_for(text)
        assert lines is not None and len(lines) <= 2
        for row in lines:                                   # 숫자 바로 뒤에서 줄이 끝나면 안 된다
            assert not captions._glued(text, row[-1]) or row[-1] == len(text) - 1
    flat = [k for g in groups for k in g]
    assert flat == list(range(len(words)))


def test_captions_forced_break_and_tiny_tail():
    words = "가나다라 마바사, 아".split()
    assert captions.group_line(words, frozenset({0}), [])[0] == [0]        # 작가가 끊은 자리
    assert captions.group_line(words, frozenset(), [])[-1][-1] == 2        # '아' 한 글자 꼬리는 앞에 붙는다


def test_captions_exclusive_activation():
    ws = captions._exclusive([{"w": "a", "s": 1.0, "e": 1.2}, {"w": "b", "s": 1.0, "e": 1.1}])
    assert ws[1]["s"] > ws[0]["s"]


def test_variant_reasons_and_pin(tmp_path):
    base = _job(tmp_path, ["가 나 다"], [{"id": "A", "프롬프트": "red bread"}])
    cache = tmp_path / "캐시"
    k = clips.keys(load(base), cache)["A"]
    k["path"].parent.mkdir(parents=True, exist_ok=True)
    k["path"].write_bytes(b"x")                                            # 가짜 클립(캐시에 있음)
    var = tmp_path / "변형" / "v"
    var.mkdir(parents=True)
    doc = yaml.safe_load((base / "shorts.yaml").read_text(encoding="utf-8"))
    doc["컷"][0]["프롬프트"] = "blue bread"
    (var / "shorts.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    plan = changes.explain(load(base), load(var), cache)
    assert "프롬프트" in plan["클립"][0][1] and plan["돈"]["클립"] > 0
    doc["컷"][0]["고정"] = k["key"]
    (var / "shorts.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    plan = changes.explain(load(base), load(var), cache)
    assert plan["클립"][0][1].startswith("고정") and plan["돈"]["클립"] == 0


def test_timeline_keeps_word_evidence():
    """단어 근거(정확·추정…)가 타임라인까지 살아 있어야 추정 단어 앵커 경고가 켜진다."""
    from types import SimpleNamespace
    from frogshorts import timeline
    spec = SimpleNamespace(lines=[SimpleNamespace(n=1)], finish={"대사사이": 0.3, "꼬리": 0.8})
    vo = {1: {"words": [{"w": "가", "s": 0.0, "e": 0.2, "근거": "추정"}], "dur": 0.5, "wav": "x.wav"}}
    _, wbl, _ = timeline.layout(spec, vo)
    assert wbl[1][0]["근거"] == "추정" and wbl[1][0]["s"] == timeline.LEAD_IN
