"""구글 연결(ADC·계정 지정) · 제미나이 호출 길(vertex/apikey) · 준비 마법사 — 네트워크는 전부 가짜로."""
from __future__ import annotations

import base64
import io
import json
import urllib.error
from pathlib import Path

import pytest

from frogshorts import config, gauth, onboard
from frogshorts.providers import gemini, tts
from frogshorts.util import FrogError


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    for d in (repo, home):
        d.mkdir()
    (repo / "frogshorts.example.toml").write_text(
        '[vertex]\nproject = ""\naccount = ""\nlocation = "global"\n\n[gemini]\nvia = "vertex"\n', encoding="utf-8")
    monkeypatch.setattr(config, "REPO", repo)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLOUDSDK_CONFIG", str(home / "gcloud"))
    monkeypatch.setenv("APPDATA", str(home / "AppData"))
    for k in ("FROGSHORTS_CONFIG", "GOOGLE_APPLICATION_CREDENTIALS", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(home)
    gauth._cache.clear()
    config.load.cache_clear()
    yield {"repo": repo, "home": home}
    gauth._cache.clear()
    config.load.cache_clear()


def _adc(path: Path, **extra) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "authorized_user", "client_id": "cid", "client_secret": "sec",
                                "refresh_token": "rt-비밀", **extra}), encoding="utf-8")
    return path


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(routes: dict, seen: list):
    def opener(req, data=None, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        seen.append(req)
        for key, val in routes.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return _Resp(json.dumps(val).encode())
        raise AssertionError(f"예상 못 한 호출: {url}")
    return opener


def test_ADC_파일이_없으면_setup_을_안내한다(env):
    assert gauth.mode() == "adc"
    with pytest.raises(FrogError, match="frogshorts setup"):
        gauth.token()
    assert "연결 안 됨" in gauth.who()


def test_ADC_토큰을_파일로_새로_받고_재사용한다(env, monkeypatch):
    _adc(env["home"] / "gcloud" / "application_default_credentials.json", quota_project_id="qp-1")
    seen: list = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(
        {"oauth2.googleapis.com/token": {"access_token": "at-1", "expires_in": 3600},
         "tokeninfo": {"email": "me@example.com"}}, seen))
    assert gauth.token() == "at-1" and gauth.token() == "at-1"
    assert sum("oauth2.googleapis.com/token" in getattr(r, "full_url", r) for r in seen) == 1
    assert gauth.project() == "qp-1"                          # 설정이 비면 로그인 파일의 quota 프로젝트
    w = gauth.who()
    assert "me@example.com(ADC)" in w and "qp-1" in w and "rt-비밀" not in w and "at-1" not in w


def test_GOOGLE_APPLICATION_CREDENTIALS_가_먼저(env, monkeypatch):
    p = _adc(env["home"] / "따로" / "adc.json")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(p))
    assert gauth.adc_path() == p and gauth.adc_info()["type"] == "authorized_user"


def test_만료된_로그인은_다시_연결하라고_한다(env, monkeypatch):
    _adc(env["home"] / "gcloud" / "application_default_credentials.json")
    err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"invalid_grant"}'))
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen({"oauth2": err}, []))
    with pytest.raises(FrogError, match="만료"):
        gauth.token()


def test_계정을_적으면_gcloud_계정지정_방식(env, monkeypatch):
    (env["repo"] / "frogshorts.toml").write_text('[vertex]\nproject = "p"\naccount = "boss@example.com"\n',
                                                 encoding="utf-8")
    config.load.cache_clear()
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return type("R", (), {"returncode": 0, "stdout": "tok-g\n", "stderr": ""})()
    monkeypatch.setattr(gauth, "gcloud", lambda: "gcloud")
    monkeypatch.setattr(gauth.subprocess, "run", fake_run)
    assert gauth.mode() == "account" and gauth.token() == "tok-g"
    assert calls[0][1:] == ["auth", "print-access-token", "--account=boss@example.com"]
    assert gauth.email() == "boss@example.com"


def test_제미나이_길_vertex_와_apikey(env, monkeypatch):
    (env["repo"] / "frogshorts.toml").write_text('[vertex]\nproject = "pj"\n', encoding="utf-8")
    config.load.cache_clear()
    assert gemini.via() == "vertex"
    assert gemini._vertex_url("m1") == ("https://aiplatform.googleapis.com/v1/projects/pj/locations/global"
                                        "/publishers/google/models/m1:generateContent")
    (env["repo"] / "frogshorts.toml").write_text('[gemini]\nvia = "엉뚱"\n', encoding="utf-8")
    config.load.cache_clear()
    with pytest.raises(FrogError):
        gemini.via()


def test_TTS_는_vertex_에서_말투를_연출메모로_넣는다(env, monkeypatch):
    sent = {}

    def fake_generate(model, body, timeout=180):
        sent["model"], sent["body"] = model, body
        return {"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(b"RIFFxx").decode()}}]}}]}
    monkeypatch.setattr(gemini, "generate", fake_generate)
    assert tts._audio("오늘 아침 빵이에요.", "Kore", "밝고 다정하게") == b"RIFFxx"
    said = sent["body"]["contents"][0]["parts"][0]["text"]
    assert said == "### DIRECTOR'S NOTES\nStyle: 밝고 다정하게\n\n### TRANSCRIPT\n오늘 아침 빵이에요."
    assert sent["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
    tts._audio("말투 없음", "Kore", "")
    assert sent["body"]["contents"][0]["parts"][0]["text"] == "말투 없음"


def test_오류_설명은_초보가_고칠_수_있게(env):
    def err(code, body):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps(body).encode()))
    assert "결제" in gemini.explain(err(403, {"error": {"message": "This API method requires billing to be enabled",
                                                       "status": "PERMISSION_DENIED"}}), "m")
    assert "꺼져" in gemini.explain(err(403, {"error": {"message": "x", "details": [{"reason": "SERVICE_DISABLED"}]}}), "m")
    assert "모델" in gemini.explain(err(404, {"error": {"message": "not found"}}), "m")


def test_프로젝트_저장은_예시를_복사해_project_만_바꾼다(env):
    f = onboard.save_project("my-proj-123")
    s = f.read_text(encoding="utf-8")
    assert 'project = "my-proj-123"' in s and 'account = ""' in s and "[gemini]" in s
    assert config.get("vertex", "project") == "my-proj-123"
    onboard.save_project("other-9")
    s = f.read_text(encoding="utf-8")
    assert s.count("project =") == 1 and 'project = "other-9"' in s


def test_프로젝트_줄이_없으면_vertex_칸에_넣는다(env):
    (env["repo"] / "frogshorts.toml").write_text('[vertex]\nlocation = "global"\n\n[ledger]\npath = "x"\n',
                                                 encoding="utf-8")
    onboard.save_project("p-1")
    s = (env["repo"] / "frogshorts.toml").read_text(encoding="utf-8")
    assert s.index('project = "p-1"') < s.index("[ledger]")
    assert config.get("vertex", "project") == "p-1" and config.get("ledger", "path") == "x"


def test_시험_호출_성공과_결제_안내(env, monkeypatch, capsys):
    onboard.save_project("pj")
    monkeypatch.setattr(gauth, "who", lambda: "계정 me · 프로젝트 pj")
    monkeypatch.setattr(gauth, "call", lambda url, body=None, **k:
                        {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})
    assert onboard.step_test("pj")
    assert "연결되었습니다" in capsys.readouterr().out

    def billing(url, body=None, **k):
        raise urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(
            b'{"error":{"message":"This API method requires billing to be enabled.","details":[{"reason":"BILLING_DISABLED"}]}}'))
    monkeypatch.setattr(gauth, "call", billing)
    assert not onboard.step_test("pj")
    out = capsys.readouterr().out
    assert "결제" in out and "billing/linkedaccount?project=pj" in out and "freetrial" in out


def test_프로젝트가_없으면_무료체험부터_안내(env, monkeypatch, capsys):
    monkeypatch.setattr(onboard, "_projects", lambda: [])
    assert onboard.step_project(True, None) is None
    assert "freetrial" in capsys.readouterr().out
    monkeypatch.setattr(onboard, "_projects", lambda: [{"projectId": "new-1", "name": "My First Project"}])
    assert onboard.step_project(True, None) == "new-1"
    assert config.get("vertex", "project") == "new-1"


def test_도구_목록은_맥_윈도우_안내를_모두_가진다():
    from frogshorts import install
    keys = [t.key for t in install.TOOLS]
    assert keys[:5] == ["ffmpeg", "yt-dlp", "node", "hyperframes", "gcloud"] and "whisper 모델" in keys
    for t in install.TOOLS:
        assert t.mac and t.win and t.why
    assert install._winget("Gyan.FFmpeg")[:5] == ["winget", "install", "--id", "Gyan.FFmpeg", "-e"]


def test_프로젝트_ID_모양이_틀리면_안내하고_멈춘다(env, capsys):
    assert onboard.step_project(True, "한글-프로젝트") is None
    assert "ID 모양이 아니다" in capsys.readouterr().out
    assert onboard.step_project(True, "My Project") is None
    assert onboard.step_project(True, "my-shop-123456") == "my-shop-123456"
