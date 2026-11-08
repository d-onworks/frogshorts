"""`frogshorts doctor` — 설치·로그인·권한을 한 번에 점검하고, 없는 것은 맥/윈도우 설치 명령을 보여 준다.

돈 드는 호출은 하지 않는다: gcloud 토큰 발급(무료)과 Vertex 지역 정보 조회(GET, 무료)까지만.
토큰·API 키 값은 어디에도 출력하지 않는다. 셸 문법 없이(리스트 인자) 돌려 윈도우에서도 같다.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .util import FrogError, which


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True
    mac: list[str] = field(default_factory=list)      # 맥 설치·해결 명령
    win: list[str] = field(default_factory=list)      # 윈도우 설치·해결 명령(또는 공식 링크)


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    """명령 실행 → (종료 코드, 출력 첫 줄). 실행 파일이 없으면 (-1, '')."""
    exe = which(cmd[0])
    if not exe:
        return -1, ""
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -2, str(e)[:120]
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return r.returncode, (out[0][:120] if out else "")


def _tool(name: str, ver: list[str], mac: list[str], win: list[str], why: str) -> Check:
    code, first = _run([name, *ver])
    if code == -1:
        return Check(name, False, f"없음 — {why}", mac=mac, win=win)
    return Check(name, code == 0, first or "있음", mac=mac, win=win)


def _python() -> Check:
    v = sys.version_info
    return Check("파이썬 3.11 이상", v >= (3, 11), f"{platform.python_version()} ({sys.executable})",
                 mac=["brew install python@3.12"], win=["winget install Python.Python.3.12"])


def _google() -> list[Check]:
    """구글 연결 — 로그인(ADC 또는 계정 지정) · 프로젝트 · Vertex 권한."""
    from . import gauth
    fix_login = ["frogshorts setup   (브라우저에서 내 구글 계정으로 로그인·허용 한 번)"]
    out: list[Check] = []                              # gcloud 설치 여부는 도구 목록에서 이미 본다
    how = f"계정 지정 {gauth.account()}" if gauth.mode() == "account" else f"ADC 로그인 파일 {gauth.adc_path()}"
    try:
        token = gauth.token()
        out.append(Check("구글 계정 연결", True, f"{gauth.email() or '연결됨'} · {how}"))
    except FrogError as e:
        out.append(Check("구글 계정 연결", False, str(e).splitlines()[0][:120], mac=fix_login, win=fix_login))
        return out
    prj = gauth.project(required=False)
    fix_prj = ["frogshorts setup   (프로젝트 목록에서 고르면 frogshorts.toml 에 적어 준다)"]
    out.append(Check("구글 클라우드 프로젝트", bool(prj), prj or "정해지지 않음", mac=fix_prj, win=fix_prj))
    if prj:
        out.append(_vertex_probe(prj, token))
    return out


def _vertex_probe(prj: str, token: str) -> Check:
    """돈 안 드는 조회 하나 — 이 프로젝트에서 Vertex AI 지역 정보를 읽을 수 있는지(권한·API 사용 설정 확인)."""
    loc = config.get("vertex", "location") or "global"
    url = f"https://aiplatform.googleapis.com/v1/projects/{prj}/locations/{loc}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "x-goog-user-project": prj})
    fix = ["frogshorts setup   (Vertex AI API 를 켜고 결제 연결 여부를 알려 준다)",
           "프로젝트 소유자가 아니면 GCP 콘솔 IAM 에서 이 계정에 'Vertex AI 사용자' 역할을 받는다"]
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            name = json.load(r).get("locationId") or loc
        return Check("Vertex AI 권한", True, f"프로젝트 {prj} · 지역 {name} 조회됨", mac=fix, win=fix)
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")[:120]
        except ValueError:
            pass
        if e.code in (401, 403):
            return Check("Vertex AI 권한", False, f"HTTP {e.code} {msg}", mac=fix, win=fix)
        return Check("Vertex AI 권한", True, f"토큰은 됨 · 지역 조회는 HTTP {e.code}(확인 못 함 — 생성 때 다시 본다)",
                     required=False)
    except (urllib.error.URLError, OSError) as e:
        return Check("Vertex AI 권한", False, f"연결 실패: {str(e)[:100]}", required=False)


def _gemini_key() -> Check | None:
    from .providers import gemini
    if gemini.via() == "vertex":
        return None                                    # 더빙·글씨 찾기도 Vertex 로 부른다 — 키가 필요 없다
    name = config.get("gemini", "api_key_env") or "GEMINI_API_KEY"
    fix = [f"https://aistudio.google.com/apikey 에서 키 발급 → 저장소 폴더 .env 에 {name}=발급받은키 한 줄"]
    try:
        config.gemini_key()
        where = "환경변수" if os.environ.get(name) else ".env 파일"
        return Check(f"제미나이 API 키({name})", True, f"있음 · {where}(값은 출력하지 않음)", mac=fix, win=fix)
    except FrogError:
        return Check(f"제미나이 API 키({name})", False, "없음 — 새 대사 더빙에 필요", mac=fix, win=fix)


def _korean_path() -> Check:
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "한글 폴더" / "가게-니트_테스트.txt"
            p.parent.mkdir(parents=True)
            p.write_text("오토 한글 쓰기 ✓", encoding="utf-8")
            ok = p.read_text(encoding="utf-8") == "오토 한글 쓰기 ✓" and any(Path(td).glob("*/가게-*.txt"))
        return Check("한글 경로·파일명 쓰기", ok, f"파일 시스템 인코딩 {sys.getfilesystemencoding()}",
                     mac=["터미널 언어 설정을 UTF-8 로"], win=["설정 > 시간 및 언어 > 관리 언어 설정 > "
                                                         "'Beta: 세계 언어 지원을 위해 Unicode UTF-8 사용' 켜기"])
    except OSError as e:
        return Check("한글 경로·파일명 쓰기", False, str(e)[:120],
                     mac=["터미널 언어 설정을 UTF-8 로"], win=["PYTHONUTF8=1 환경변수 설정 후 다시"])


def checks() -> list[Check]:
    from . import install
    out = [_python()]
    for t in install.TOOLS:
        found = t.find()
        detail = found or f"없음 — {t.why}에 필요"
        if found and t.key in ("ffmpeg", "yt-dlp", "node", "hyperframes"):
            code, first = _run([t.key, "-version" if t.key == "ffmpeg" else "--version"])
            detail = first or found
        out.append(Check(t.key, bool(found), detail, mac=t.mac, win=t.win))
    out += _google()
    key = _gemini_key()
    out += [key] if key else []
    out.append(_korean_path())
    return out


def _cols(s: str) -> int:
    """화면 폭 — 한글은 두 칸이라 글자 수가 아니라 폭으로 맞춘다."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


_CMDS = ("brew", "winget", "npm", "gcloud", "mkdir", "curl", "powershell", "copy", "cp", "frogshorts")


def _cmd(line: str, prompt: str) -> str:
    """명령이면 프롬프트 기호를 붙이고, 설명 문장이면 그대로."""
    return f"{prompt} {line}" if line.split(" ", 1)[0] in _CMDS else f"  {line}"


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")      # 윈도우 옛 콘솔(cp949)에서 ✓ 가 안 찍혀도 죽지 않게
    except (AttributeError, ValueError):
        pass
    files = config.load()["_files"]
    print(f"frogshorts doctor — {platform.system()} {platform.release()} · {platform.machine()}")
    print(f"설정 파일: {', '.join(files) if files else '없음(기본값만) — frogshorts.example.toml 을 frogshorts.toml 로 복사해 적는다'}")
    print()
    res = checks()
    w = max(_cols(c.name) for c in res) + 2
    for c in res:
        mark = "✓" if c.ok else ("✗" if c.required else "△")
        print(f" {mark} {c.name}{" " * (w - _cols(c.name))} {c.detail}")
    bad = [c for c in res if not c.ok]
    if bad:
        print("\n고칠 것 — 맥 / 윈도우 명령")
        for c in bad:
            print(f"\n [{c.name}]{'' if c.required else ' (선택)'}")
            for m in c.mac:
                print(f"   맥     {_cmd(m, '$')}")
            for m in c.win:
                print(f"   윈도우 {_cmd(m, '>')}")
    need = [c for c in bad if c.required]
    print(f"\n{'필수 항목 모두 통과' if not need else f'필수 항목 {len(need)}개가 빠졌다'} "
          f"(✓ 통과 · ✗ 필수 빠짐 · △ 선택/확인 못 함)")
    if need:
        print("가장 쉬운 길: `frogshorts setup` — 빠진 프로그램을 자동으로 깔고 구글 연결까지 차례로 안내한다.")
    return 1 if need else 0
