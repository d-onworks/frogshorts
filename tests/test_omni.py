"""Omni 최신 버전 고르기 — 목록은 가짜로."""
from __future__ import annotations

import pytest

from frogshorts import config
from frogshorts.providers import omni, video
from frogshorts.util import FrogError


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(omni, "_cache_file", lambda: tmp_path / "omni.json")
    omni._mem.clear()
    yield
    omni._mem.clear()


def test_가장_높은_버전_같은_버전이면_정식():
    names = ["gemini-3.5-flash", "gemini-omni-1.1-flash-preview", "gemini-omni-1.2-flash-preview",
             "gemini-omni-1.2-flash", "veo-3.1"]
    assert omni.pick(names) == "gemini-omni-1.2-flash"
    assert omni.pick(names + ["gemini-omni-1.10-flash-preview"]) == "gemini-omni-1.10-flash-preview"
    assert omni.pick(["veo-3.1"]) is None


def test_버전별_길이_단가():
    assert omni.spec("gemini-omni-1.1-flash-preview") == (10, 0.10)
    assert omni.spec("gemini-omni-1.2-flash")[0] == 15
    assert omni.spec("gemini-omni-1.5-flash")[0] == 15          # 모르는 새 버전 → 표의 가장 높은 값


def test_latest_는_목록을_하루_한_번만_묻는다(monkeypatch):
    calls = []
    monkeypatch.setattr(omni, "_list_names", lambda: calls.append(1) or ["gemini-omni-1.2-flash"])
    assert omni.latest() == "gemini-omni-1.2-flash"
    omni._mem.clear()
    assert omni.latest() == "gemini-omni-1.2-flash" and len(calls) == 1     # 파일 캐시


def test_목록을_못_물으면_알려진_최신(monkeypatch):
    def boom():
        raise FrogError("로그인 안 됨")
    monkeypatch.setattr(omni, "_list_names", boom)
    assert omni.latest() == omni.FALLBACK


def test_설정에_이름을_적으면_그_버전_고정_길이도_따라간다(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get", lambda sec, key, *a: {"model": "gemini-omni-1.1-flash-preview"}.get(key)
                        if sec == "vertex" else None)
    assert video.MODEL == "gemini-omni-1.1-flash-preview" and video.MAX_SEC == 10
    with pytest.raises(FrogError, match="3~10초"):
        video.build_body("x", [], "720p", "9:16", 12)
    monkeypatch.setattr(config, "get", lambda sec, key, *a: {"model": "latest"}.get(key) if sec == "vertex" else None)
    monkeypatch.setattr(omni, "_list_names", lambda: ["gemini-omni-1.2-flash"])
    assert video.MAX_SEC == 15
    assert video.build_body("x", [], "720p", "9:16", 15)["response_format"][0]["duration"] == "15s"
