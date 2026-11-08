"""글씨 흐림(blur.py) 시험 — 트랙 묶기, 큰 상자 버리기, ffmpeg 필터 실제 실행, 캐시(두 번째엔 API 안 부름)."""
from __future__ import annotations

import io
import json
import subprocess
import urllib.request

import shutil

import pytest

from frogshorts import blur, gauth


def _clip(path, secs=3, w=360, h=640):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=24:duration={secs}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_tracks_join_same_text_over_time():
    dets = [{"t": 0.0, "box": [0.40, 0.20, 0.10, 0.04], "label": "라벨"},
            {"t": 0.5, "box": [0.41, 0.20, 0.10, 0.04], "label": "라벨"},
            {"t": 1.0, "box": [0.42, 0.21, 0.10, 0.04], "label": "라벨"},
            {"t": 0.5, "box": [0.70, 0.60, 0.08, 0.05], "label": "택"}]      # 다른 자리 = 다른 트랙
    regs = blur.tracks(dets, 0.5, 10.0)
    assert len(regs) == 2
    lab = next(r for r in regs if r["label"] == "라벨")
    assert (lab["t0"], lab["t1"]) == (0.0, 1.5)              # 첫 등장 - 간격 ~ 마지막 + 간격(0 밑으로 안 감)
    x, y, w, h = lab["box"]
    assert x < 0.40 and x + w > 0.52 and y < 0.20 and y + h > 0.25   # 세 상자를 다 덮고 여유까지
    tag = next(r for r in regs if r["label"] == "택")
    assert (tag["t0"], tag["t1"]) == (0.0, 1.0)


def test_tracks_split_when_text_moves_far():
    dets = [{"t": i * 0.5, "box": [0.05 + i * 0.15, 0.5, 0.06, 0.03], "label": "택"} for i in range(5)]
    regs = blur.tracks(dets, 0.5, 10.0)
    assert len(regs) >= 2                                     # 하나로 덮으면 띠처럼 커지니 끊는다
    assert all(r["box"][2] < 0.35 for r in regs)


def test_tracks_gap_breaks_track():
    dets = [{"t": 0.0, "box": [0.4, 0.2, 0.1, 0.04]}, {"t": 5.0, "box": [0.4, 0.2, 0.1, 0.04]}]
    assert len(blur.tracks(dets, 0.5, 10.0)) == 2


def test_big_box_dropped(capsys):
    dets = [{"t": 0.0, "box": [0.0, 0.0, 0.6, 0.6], "label": "옷 전체"},
            {"t": 0.0, "box": [0.4, 0.2, 0.1, 0.04], "label": "라벨"}]
    regs = blur.tracks(dets, 0.5, 10.0)
    assert [r["label"] for r in regs] == ["라벨"]
    assert "오탐" in capsys.readouterr().err


def test_filter_for_empty_is_null():
    assert blur.filter_for([], 1080, 1920, "0:v", "bl0") == "[0:v]null[bl0]"


NO_FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 가 없다(doctor 로 설치 안내)")


@NO_FFMPEG
def test_filter_for_runs_in_ffmpeg(tmp_path):
    src = _clip(tmp_path / "src.mp4")
    regs = [{"box": [0.1, 0.1, 0.2, 0.05], "t0": 0.0, "t1": 1.5},
            {"box": [0.55, 0.7, 0.33, 0.09], "t0": 1.0, "t1": 3.0},
            {"box": [0.97, 0.98, 0.2, 0.2], "t0": 0.0, "t1": 3.0}]       # 화면 끝 넘는 상자도 안으로 잘린다
    fc = blur.filter_for(regs, 360, 640, "[0:v]", "[bl0]")
    assert "crop=72:32:36:64" in fc                            # 짝수 픽셀
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
                        fc + ";[bl0]trim=start=0.5,setpts=PTS-STARTPTS[v]", "-map", "[v]", "-f", "null", "-"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = blur.apply(src, regs, tmp_path / "out.mp4")
    assert out.exists() and out.stat().st_size > 0
    assert blur.preview(src, regs, tmp_path / "p.png").exists()


@NO_FFMPEG
def test_regions_for_cut_manual(tmp_path):
    src = _clip(tmp_path / "src.mp4")
    cut = {"id": "A", "흐림": [{"영역": [0.42, 0.18, 0.10, 0.05], "구간": "0-1.5"}, {"영역": [0.1, 0.1, 0.1, 0.1]}]}
    regs = blur.regions_for_cut(cut, src, tmp_path, auto=False)
    assert regs[0]["t0"] == 0.0 and regs[0]["t1"] == 1.5 and regs[0]["src"] == "손"
    assert regs[1]["t1"] == pytest.approx(3.0, abs=0.1)        # 구간 없으면 클립 전체
    with pytest.raises(blur.FrogError):
        blur.regions_for_cut({"id": "B", "흐림": [{"영역": [0.9, 0.1, 0.5, 0.1]}]}, src, tmp_path, auto=False)


@NO_FFMPEG
def test_scan_cache_skips_api_second_time(tmp_path, monkeypatch):
    src = _clip(tmp_path / "src.mp4", secs=2)
    calls = []

    def fake_urlopen(req, timeout=0):
        body = json.loads(req.data)
        n = sum(1 for p in body["contents"][0]["parts"] if "inline_data" in p)
        first = int(body["contents"][0]["parts"][0]["text"].split()[-1])
        calls.append(n)
        ans = [{"frame": first + k, "box_2d": [200, 400, 240, 500], "label": "라벨"} for k in range(n)]
        return io.BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": json.dumps(ans)}]}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(blur.config, "gemini_key", lambda: "시험용")
    # 기본 길(vertex) — 진짜 로그인·설정을 안 쓰게 가짜 토큰·프로젝트(개발 PC 의 설정 유무와 상관없이 같은 결과)
    monkeypatch.setattr(blur.gemini, "via", lambda: "vertex")
    monkeypatch.setattr(gauth, "token", lambda: "시험토큰")
    monkeypatch.setattr(gauth, "project", lambda required=True: "test-proj")
    monkeypatch.setattr(gauth, "email", lambda: "")
    regs = blur.scan(src, tmp_path / "캐시", every=0.5)
    assert len(calls) == 1 and sum(calls) == 4                 # 2초 ÷ 0.5 = 프레임 4장, 한 요청
    assert len(regs) == 1 and regs[0]["t1"] == pytest.approx(2.0)
    cached = list((tmp_path / "캐시" / "흐림").glob("*.json"))
    assert len(cached) == 1 and json.loads(cached[0].read_text())["프레임수"] == 4
    assert blur.scan(src, tmp_path / "캐시", every=0.5) == regs
    assert len(calls) == 1                                     # 두 번째는 캐시 — API 안 부름
