"""설정 읽기 순서·안내 메시지·키 찾기, 그리고 공급자 모듈이 설정 없이도 가져와지는지(돈 안 드는 것만)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from frogshorts import config
from frogshorts.util import FrogError


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    """저장소·사용자 폴더의 진짜 설정(개발 PC 의 frogshorts.local.toml 등)을 못 보게 가린다."""
    repo, home, cwd = tmp_path / "repo", tmp_path / "home", tmp_path / "work" / "가게"
    for d in (repo, home, cwd):
        d.mkdir(parents=True)
    monkeypatch.setattr(config, "REPO", repo)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for k in ("FROGSHORTS_CONFIG", "GEMINI_API_KEY", "MY_TTS_KEY", "FROGSHORTS_WHISPER_MODEL",
              "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(home / "gcloud"))     # 진짜 gcloud 로그인 파일을 못 보게
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    monkeypatch.chdir(cwd)
    from frogshorts import gauth
    gauth._cache.clear()
    config.load.cache_clear()
    yield {"repo": repo, "home": home, "cwd": cwd}
    config.load.cache_clear()


def _toml(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_기본값만_있어도_돈다(clean):
    cfg = config.load()
    assert cfg["_files"] == []
    assert config.get("vertex", "project") == ""
    assert config.get("gemini", "default_voice") == "Kore"


def test_우선순위_환경변수_로컬_일반(clean, monkeypatch):
    cwd = clean["cwd"]
    _toml(cwd / "frogshorts.toml", '[vertex]\nproject = "일반"\naccount = "a@example.com"\nlocation = "x"\n')
    _toml(cwd / "frogshorts.local.toml", '[vertex]\nproject = "로컬"\naccount = "b@example.com"\n')
    config.load.cache_clear()
    assert config.get("vertex", "project") == "로컬"            # local 이 일반을 이긴다
    assert config.get("vertex", "location") == "x"              # 없는 항목은 약한 파일에서 채운다

    env = _toml(clean["home"] / "elsewhere.toml", '[vertex]\nproject = "환경변수"\n')
    monkeypatch.setenv("FROGSHORTS_CONFIG", str(env))
    config.load.cache_clear()
    assert config.get("vertex", "project") == "환경변수"         # FROGSHORTS_CONFIG 가 제일 세다
    assert config.get("vertex", "account") == "b@example.com"
    assert config.load()["_files"][0] == str(env)


def test_위_폴더와_저장소_사용자폴더도_찾는다(clean):
    _toml(clean["cwd"].parent / "frogshorts.toml", '[vertex]\nproject = "윗폴더"\n')
    _toml(clean["repo"] / "frogshorts.local.toml", '[vertex]\naccount = "repo@example.com"\n')
    _toml(clean["home"] / ".frogshorts" / "config.toml", '[vertex]\nproject = "집"\nlocation = "home-loc"\n')
    config.load.cache_clear()
    assert config.get("vertex", "project") == "윗폴더"
    assert config.get("vertex", "account") == "repo@example.com"
    assert config.get("vertex", "location") == "home-loc"


def test_need_는_어디에_적을지_안내한다(clean):
    with pytest.raises(FrogError) as e:
        config.need("vertex", "project", "GCP 콘솔의 프로젝트 ID")
    msg = str(e.value)
    assert "[vertex] project" in msg and "GCP 콘솔의 프로젝트 ID" in msg
    assert "frogshorts.example.toml" in msg


def test_제미나이_키_환경변수가_먼저(clean, monkeypatch):
    _toml(clean["cwd"] / "frogshorts.toml", '[gemini]\napi_key_env = "MY_TTS_KEY"\n')
    _toml(clean["cwd"] / ".env", 'MY_TTS_KEY="파일값"\n')
    config.load.cache_clear()
    assert config.gemini_key() == "파일값"                       # 환경변수 없으면 .env
    monkeypatch.setenv("MY_TTS_KEY", "환경값")
    assert config.gemini_key() == "환경값"                       # 환경변수가 이긴다


def test_제미나이_키_env_file_설정과_없을때_안내(clean):
    f = _toml(clean["home"] / "secret" / "keys.env", "GEMINI_API_KEY=xyz\n")
    with pytest.raises(FrogError) as e:
        config.gemini_key()
    assert "GEMINI_API_KEY" in str(e.value)
    _toml(clean["cwd"] / "frogshorts.toml", f'[gemini]\nenv_file = "{f.as_posix()}"\n')
    config.load.cache_clear()
    assert config.gemini_key() == "xyz"


def test_video_는_설정_없이도_가져와지고_본문을_만든다(clean, monkeypatch, tmp_path):
    from frogshorts.providers import omni, video
    monkeypatch.setattr(omni, "_cache_file", lambda: tmp_path / "omni.json")
    omni._mem.clear()
    assert video.MODEL == omni.FALLBACK                         # 로그인 전엔 목록을 못 물어 알려진 최신으로
    body = video.build_body("니트 소개", [], "1080p", "9:16", None)
    assert body["model"] == video.MODEL and body["input"] == "니트 소개"
    body = video.build_body("니트 소개", [], "1080p", "9:16", 7)
    assert body["response_format"][0]["duration"] == "7s"
    assert video.est_usd(None) == pytest.approx(10 * omni.spec(omni.FALLBACK)[1])
    omni._mem.clear()
    assert "연결 안 됨" in video.billing_line()                  # 계획 출력은 멈추지 않는다
    with pytest.raises(FrogError, match="setup"):
        video.PROJECT                                            # 실제 호출 시점엔 안내하며 멈춘다


def test_video_과금줄에_설정_계정이_나온다(clean):
    _toml(clean["cwd"] / "frogshorts.toml",
          '[vertex]\nproject = "my-gcp-project-id"\naccount = "me@example.com"\nusd_per_sec = 0.2\n'
          '[ledger]\npath = "~/장부/v.jsonl"\n')
    config.load.cache_clear()
    from frogshorts.providers import video
    line = video.billing_line(5)
    assert "me@example.com" in line and "my-gcp-project-id" in line and "$1.0" in line
    assert video.LEDGER == clean["home"] / "장부" / "v.jsonl"
    assert "/projects/my-gcp-project-id/locations/global/" in video.BASE


def test_tts_과금줄에_키_값이_안_나온다(clean, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "비밀키-1234")
    from frogshorts.providers import tts
    line = tts.billing_line("")
    assert "Vertex AI" in line and "Kore" in line                # 기본은 영상과 같은 Vertex 프로젝트
    _toml(clean["cwd"] / "frogshorts.toml", '[gemini]\nvia = "apikey"\n')
    config.load.cache_clear()
    line = tts.billing_line("")
    assert "GEMINI_API_KEY" in line and "비밀키" not in line and "Kore" in line
    assert tts.MODEL == "gemini-3.8-flash-tts"
    assert not tts.is_clone("아무개")                            # [voice_clone] 없으면 복제 기능 없음


def test_tts_복제_명령_자리표시자(clean):
    _toml(clean["cwd"] / "frogshorts.toml",
          '[voice_clone]\nnames = ["아무개"]\n'
          'command = ["python3", "~/tts.py", "--voice", "{voice}", "--text", "{text}", "--out", "{out}"]\n'
          'style_args = ["--style", "{style}"]\n')
    config.load.cache_clear()
    from frogshorts.providers import tts
    assert tts.is_clone("아무개") and not tts.is_clone("Kore")
    cmd = tts._clone_cmd("아무개", "가격은 {out} 원", Path("o.wav"), "밝게")
    assert cmd[1] == str(clean["home"] / "tts.py")
    assert cmd[3:] == ["아무개", "--text", "가격은 {out} 원", "--out", "o.wav", "--style", "밝게"]
    assert "--style" not in tts._clone_cmd("아무개", "안녕", Path("o.wav"), "")


def test_위스퍼_모델은_설정이_먼저(clean, monkeypatch):
    from frogshorts import words
    with pytest.raises(FrogError):
        words.model_path()
    env_m = _toml(clean["home"] / "env.bin", "x")
    monkeypatch.setenv("FROGSHORTS_WHISPER_MODEL", str(env_m))
    assert words.model_path() == str(env_m)
    cfg_m = _toml(clean["home"] / "모델" / "ggml-large-v3-turbo.bin", "x")
    _toml(clean["cwd"] / "frogshorts.toml", f'[whisper]\nmodel = "{cfg_m.as_posix()}"\n')
    config.load.cache_clear()
    assert Path(words.model_path()) == cfg_m


@pytest.mark.skipif(sys.platform == "win32", reason="실행 권한 비트는 맥·리눅스만")
def test_위스퍼_실행파일_설정(clean):
    from frogshorts import words
    exe = _toml(clean["home"] / "bin" / "my-whisper", "#!/bin/sh\n")
    os.chmod(exe, 0o755)
    _toml(clean["cwd"] / "frogshorts.toml", f'[whisper]\nbin = "{exe.as_posix()}"\n')
    config.load.cache_clear()
    assert words.whisper_bin() == str(exe)
    _toml(clean["cwd"] / "frogshorts.toml", '[whisper]\nbin = "없는-위스퍼-xyz"\n')
    config.load.cache_clear()
    with pytest.raises(FrogError):
        words.whisper_bin()
