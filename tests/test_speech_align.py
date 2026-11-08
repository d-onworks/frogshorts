"""말 길이 추정 · 단어 정렬 — 돈 안 드는 부분."""
import pytest

from frogshorts import align, speech
from frogshorts.util import FrogError


@pytest.mark.parametrize("text,want", [
    ("4,000원", "사천원"), ("12,000원", "만이천원"), ("15g", "십오그램"), ("7.5%", "칠점오퍼센트"),
    ("10000", "만"), ("1,500원밖에", "천오백원밖에"), ("20kg에 11만원", "이십킬로그램에 십일만원"),
    ("101", "백일"), ("2026년", "이천이십육년"),
    ("오후 2시", "오후 두시"), ("딸기 3알", "딸기 세알"), ("20살", "스무살"), ("12개", "열두개"), ("2시간 30분", "두시간 삼십분"),
])
def test_numbers_read_as_spoken(text, want):
    assert speech.spoken(text) == want


def test_units_counts_hangul_numbers_and_latin():
    assert speech.units("딸기 3알") == 4          # 딸·기 + 삼 + 알
    assert speech.units("Big Sale") == 2
    assert speech.units("…!!") == 0


def test_estimate_grows_with_text_and_pauses():
    a = speech.estimate("오늘 빚은 것만 팝니다")
    b = speech.estimate("오늘 빚은 것만, 팝니다")
    assert b > a > 0
    assert speech.estimate("") == 0.0


def test_calibrate_needs_three_samples():
    s = [("가나다라마바사아자차", 2.0), ("가나다라마바사아자차카타", 2.4)]
    assert speech.calibrate(s) is None
    r = speech.calibrate(s + [("가나다라마바사", 1.5)])
    assert 4.0 < r < 7.0


def _heard(*pairs):
    return [{"w": w, "s": s, "e": e} for w, s, e in pairs]


def test_align_exact():
    out = align.align("오늘 빚은 것만", _heard(("오늘", 0, .4), ("빚은", .4, .8), ("것만", .8, 1.2)))
    assert [w["근거"] for w in out] == ["정확"] * 3
    assert out[1]["s"] == pytest.approx(.4)


def test_align_misheard_name_keeps_script_text():
    out = align.align("오토프로그 옷가게", _heard(("오토프로고", 0, .8), ("옷가게", .8, 1.3)))
    assert out[0]["w"] == "오토프로그" and out[0]["근거"] == "비슷"


def test_align_spoken_numbers_match_digits():
    out = align.align("삼백 원에서 구백 원 사이", _heard(("300원에서", 0, 1.0), ("900원", 1.0, 1.8), ("사이", 1.8, 2.2)))
    assert [w["근거"] for w in out][:2] == ["합침", "합침"]
    assert out[1]["s"] > out[0]["s"] and out[-1]["근거"] == "정확"


def test_align_split_by_pronunciation_table():
    out = align.align("오토프로그 떡집", _heard(("오토", 0, .3), ("프로그", .3, .7), ("떡집", .7, 1.1)),
                      {"오토프로그": "오토 프로그"})
    assert out[0]["근거"] == "나눔" and out[0]["e"] == pytest.approx(.7)


def test_align_missing_word_is_estimated_between_neighbours():
    out = align.align("가 나 다", _heard(("가", 0, .5), ("다", 1.0, 1.5)))
    mid = out[1]
    assert mid["근거"] == "추정" and .5 <= mid["s"] <= mid["e"] <= 1.0


def test_align_empty_heard_stops():
    with pytest.raises(FrogError):
        align.align("가 나", [])
