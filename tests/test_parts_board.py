"""랭킹 보드·댓글 스티커 부품 시험 — 글이 들어가는지, 정직성 규칙을 어기면 막히는지."""
import re

import pytest

from frogshorts.parts_board import comment, ranking
from frogshorts.util import FrogError

DUR = 10.0


def _board(**over):
    p = {"종류": "랭킹", "제목": "이번 주 많이 팔린 니트", "at": 1.0, "until": 8.0,
         "항목": [{"순위": 3, "이름": "꽈배기 가디건", "at": 2.0},
                {"순위": 2, "이름": "라운드 니트", "at": 3.5},
                {"순위": 1, "이름": "울 블렌드 카디건", "at": 5.0, "강조": True},
                {"순위": 4, "이름": "기본 니트", "미리": True}]}
    p.update(over)
    return p


# ── 랭킹 ──
def test_ranking_order_and_text():
    html, js = ranking(_board(), DUR, 0)
    names = re.findall(r'class="rk-name"[^>]*>([^<]+)<', html)
    assert names == ["울 블렌드 카디건", "라운드 니트", "꽈배기 가디건", "기본 니트"]   # 1위가 맨 위
    assert "이번 주 많이 팔린 니트" in html
    assert 'id="rank0"' in html and 'data-start="1.0"' in html and 'data-duration="7.0"' in html
    assert html.count('class="rk-fill hot"') == 1


def test_ranking_items_appear_at_their_time_and_stay():
    html, js = ranking(_board(), DUR, 0)
    assert "},2.0);" in js and "},3.5);" in js and "},5.0);" in js
    # 항목 하나하나는 사라지는 트윈이 없다(보드 끝까지 남음) — 사라짐은 판 전체에만
    assert not re.search(r'tl\.to\("#rank0_\d+",\{opacity:0', js)


def test_ranking_preloaded_row_has_no_entry_tween():
    html, js = ranking(_board(), DUR, 0)
    assert '"#rank0_3"' not in js          # 4위(미리)는 판과 같이 나온다


def test_ranking_escapes_html():
    p = _board(제목="<b>할인</b>", 항목=[{"순위": 1, "이름": "니트 & <조끼>"}])
    html, _ = ranking(p, DUR, 2)
    assert "<b>" not in html and "&lt;b&gt;" in html and "니트 &amp; &lt;조끼&gt;" in html
    assert 'id="rank2"' in html


def test_ranking_note_only_when_written():
    p = _board(항목=[{"순위": 1, "이름": "니트", "덧붙임": "재입고"}, {"순위": 2, "이름": "조끼"}])
    html, _ = ranking(p, DUR, 0)
    assert html.count('class="rk-note"') == 1 and "재입고" in html


@pytest.mark.parametrize("items, msg", [
    ([{"순위": 1, "이름": "니트", "at": 9.0}], "밖"),             # 보드 끝(8초) 뒤
    ([{"순위": 1, "이름": "니트", "at": 0.5}], "밖"),             # 보드 시작(1초) 앞
    ([{"순위": 1, "이름": "니트"}, {"순위": 1, "이름": "조끼"}], "두 번"),
    ([{"순위": "일", "이름": "니트"}], "숫자"),
    ([{"순위": 1, "이름": ""}], "비었다"),
    ([{"순위": 1, "이름": "니트", "미리": True, "at": 2.0}], "같이"),
    ([], "항목이 없다"),
])
def test_ranking_rejects_bad_items(items, msg):
    with pytest.raises(FrogError, match=msg):
        ranking(_board(항목=items), DUR, 0)


def test_ranking_rejects_bad_span():
    with pytest.raises(FrogError):
        ranking(_board(at=8.0, until=4.0), DUR, 0)


def test_ranking_until_defaults_to_end():
    p = _board()
    p.pop("until")
    html, _ = ranking(p, DUR, 0)
    assert 'data-duration="9.0"' in html


# ── 댓글 ──
def _cmt(**over):
    p = {"종류": "댓글", "글": "색이 화면이랑 똑같아요", "작성자": "@손님123",
         "출처": "https://youtube.com/watch?v=x&lc=abc", "덧붙임": "좋아요 12", "at": 3.0, "until": 6.0}
    p.update(over)
    return p


def test_comment_text_and_lines():
    html, js = comment(_cmt(), DUR, 1)
    assert "색이 화면이랑 똑같아요" in html and "@손님123" in html and "좋아요 12" in html
    assert 'id="cmt1"' in html and "예시 댓글" not in html
    assert "youtube.com" not in html          # 출처 링크는 화면에 안 그린다
    assert '"#cmt1 .cm-card"' in js


def test_comment_requires_source():
    for src in (None, "", "   ", "어디서 봤음"):
        with pytest.raises(FrogError, match="출처 링크를 적거나, 시연용이면 출처: 예시"):
            comment(_cmt(출처=src), DUR, 0)


def test_comment_example_mark():
    html, _ = comment(_cmt(출처="예시"), DUR, 0)
    assert "예시 댓글" in html


def test_comment_no_fake_defaults():
    p = _cmt()
    p.pop("작성자")
    p.pop("덧붙임")
    html, _ = comment(p, DUR, 0)
    assert 'class="cm-who"' not in html and 'class="cm-extra"' not in html
    assert re.search(r'class="cm-av"[^>]*></div>', html)     # 동그라미 안에 지어낸 글자 없음


def test_comment_long_text_shrinks_to_three_lines():
    long = "이 니트 진짜 부드럽고 색감이 화면에서 본 거랑 완전히 똑같아서 깜짝 놀랐어요 다음에 또 살게요 친구도 데려갈게요"
    html, _ = comment(_cmt(글=long), DUR, 0)
    size = int(re.search(r'class="cm-text" style="font-size:(\d+)px"', html).group(1))
    assert size < 50 and html.count('class="cm-line"') <= 3


def test_comment_escapes_and_rejects_empty():
    html, _ = comment(_cmt(글="<script>x</script>", 작성자="<i>"), DUR, 0)
    assert "<script>x" not in html and "&lt;script&gt;" in html
    with pytest.raises(FrogError, match="비었다"):
        comment(_cmt(글="  "), DUR, 0)
