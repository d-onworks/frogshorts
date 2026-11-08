"""plan 이 틀의 안 채운 TODO 문단을 프롬프트에 붙이지 않는지(초보가 틀을 안 채워도 생성이 막히지 않게)."""
from frogshorts import plan


def test_TODO_카메라는_빼고_장면만():
    frame = {"카메라": "TODO: 모든 컷 프롬프트 앞에 붙일 촬영 방식", "프롬프트끝": "todo 나중에"}
    assert plan._prompt(frame, "  Hands lift a loaf.\n  Steam rises. ") == "Hands lift a loaf. Steam rises."


def test_채운_카메라는_앞뒤로_붙는다():
    frame = {"카메라": "Handheld vertical phone video.", "프롬프트끝": "No text on screen."}
    assert plan._prompt(frame, "A loaf.") == "Handheld vertical phone video. A loaf. No text on screen."
