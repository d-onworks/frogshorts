"""필요한 프로그램 자동 설치 — 맥은 Homebrew, 윈도우는 winget(+위스퍼는 공식 zip).

도구 목록 TOOLS 는 `frogshorts doctor`(점검·수동 명령 안내)와 `frogshorts setup`(자동 설치)이 같이 쓴다.
설치 명령은 셸 문법 없이 리스트로 실행한다(윈도우에서도 같다). 받은 파일은 ~/.frogshorts/tools 아래에만 둔다.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import words
from .util import FrogError, which

WIN = os.name == "nt"
WHISPER_MODEL = "ggml-large-v3-turbo.bin"
WHISPER_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_MODEL}"
WHISPER_RELEASES = "https://api.github.com/repos/ggml-org/whisper.cpp/releases?per_page=20"
BREW_SCRIPT = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
BREW_INSTALL = f'/bin/bash -c "$(curl -fsSL {BREW_SCRIPT})"'


@dataclass
class Tool:
    key: str
    why: str
    find: Callable[[], str | None]
    mac: list[str] = field(default_factory=list)      # 사람이 읽는 명령(doctor 안내용)
    win: list[str] = field(default_factory=list)
    auto: Callable[[], None] | None = None            # setup 자동 설치(없으면 mac/win 명령을 그대로 실행)


def _which(name: str) -> Callable[[], str | None]:
    return lambda: which(name)


def _whisper_find() -> str | None:
    try:
        return words.whisper_bin()
    except FrogError:
        return None


def _model_find() -> str | None:
    try:
        return words.model_path()
    except FrogError:
        return None


def refresh_path() -> None:
    """윈도우: 방금 깐 프로그램이 이 창에서도 보이게 레지스트리의 PATH 를 다시 읽는다.
    맥: Homebrew 기본 위치를 PATH 앞에 둔다."""
    if WIN:
        import winreg
        parts = []
        for root, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                          (winreg.HKEY_CURRENT_USER, "Environment")):
            try:
                with winreg.OpenKey(root, sub) as k:
                    parts.append(os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]))
            except OSError:
                pass
        links = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"
        npm = Path(os.environ.get("APPDATA", "")) / "npm"
        extra = [str(p) for p in (links, npm) if p.is_dir()]
        os.environ["PATH"] = os.pathsep.join([*parts, *extra, os.environ.get("PATH", "")])
    else:
        for d in ("/opt/homebrew/bin", "/usr/local/bin"):
            if Path(d).is_dir() and d not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def run(cmd: list[str], quiet: bool = False) -> int:
    """설치 명령 실행 — 화면에 진행을 그대로 보여 준다(quiet 면 숨김)."""
    exe = which(cmd[0]) or cmd[0]
    try:
        r = subprocess.run([exe, *cmd[1:]], stdout=subprocess.DEVNULL if quiet else None,
                           stderr=subprocess.DEVNULL if quiet else None)
    except OSError as e:
        print(f"     실행 못 함: {e}")
        return -1
    return r.returncode


def _download(url: str, out: Path, label: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "frogshorts-setup"})
    with urllib.request.urlopen(req, timeout=60) as r, tmp.open("wb") as f:
        total, got, shown = int(r.headers.get("Content-Length") or 0), 0, -1
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            pct = int(got * 100 / total) if total else -1
            if total and pct // 5 != shown:
                shown = pct // 5
                print(f"\r     {label} 받는 중 {pct:3d}% ({got >> 20}/{total >> 20}MB)", end="", flush=True)
    print()
    tmp.replace(out)


def _whisper_win() -> None:
    """윈도우 위스퍼: 공식 릴리스에서 whisper-bin-x64.zip 을 찾아 ~/.frogshorts/tools/whisper 에 푼다."""
    req = urllib.request.Request(WHISPER_RELEASES, headers={"User-Agent": "frogshorts-setup"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rels = json.load(r)
    url = next((a["browser_download_url"] for rel in rels for a in rel.get("assets", [])
                if a["name"] == "whisper-bin-x64.zip"), None)
    if not url:
        raise FrogError("whisper.cpp 릴리스에서 whisper-bin-x64.zip 을 못 찾았다")
    dest = words.TOOLS_DIR / "whisper"
    with tempfile.TemporaryDirectory() as td:
        z = Path(td) / "whisper.zip"
        _download(url, z, "whisper.cpp")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dest)


def _whisper_model() -> None:
    _download(WHISPER_MODEL_URL, words.model_dir() / WHISPER_MODEL, "받아쓰기 모델(약 1.6GB)")


def _winget(pkg: str) -> list[str]:
    return ["winget", "install", "--id", pkg, "-e", "--source", "winget", "--silent",
            "--accept-package-agreements", "--accept-source-agreements"]


TOOLS: list[Tool] = [
    Tool("ffmpeg", "영상 자르기·합치기", _which("ffmpeg"), ["brew install ffmpeg"], ["winget install Gyan.FFmpeg"]),
    Tool("yt-dlp", "참고 영상 받기", _which("yt-dlp"), ["brew install yt-dlp"], ["winget install yt-dlp.yt-dlp"]),
    Tool("node", "화면 부품(자막·가격표) 렌더 준비", _which("node"), ["brew install node"],
         ["winget install OpenJS.NodeJS.LTS"]),
    Tool("hyperframes", "화면 부품 렌더", _which("hyperframes"), ["npm i -g hyperframes --allow-scripts=esbuild"], ["npm i -g hyperframes --allow-scripts=esbuild"]),
    Tool("gcloud", "구글 계정 연결(로그인 한 번)", _which("gcloud"), ["brew install --cask google-cloud-sdk"],
         ["winget install Google.CloudSDK"]),
    Tool("whisper", "받아쓰기(단어 시각)", _whisper_find, ["brew install whisper-cpp"],
         ["frogshorts setup 이 공식 whisper-bin-x64.zip 을 받아 ~/.frogshorts/tools/whisper 에 푼다"],
         auto=_whisper_win if WIN else None),
    Tool("whisper 모델", "받아쓰기 모델 파일", _model_find,
         [f"curl -L --create-dirs -o ~/.local/share/whisper-models/{WHISPER_MODEL} {WHISPER_MODEL_URL}"],
         [f"frogshorts setup 이 {WHISPER_MODEL} 을 {words.model_dir()} 에 받는다"], auto=_whisper_model),
]


def _cmd_list(line: str) -> list[str]:
    parts = line.split()
    if WIN and parts[0] == "winget":
        return _winget(parts[2])
    return parts


def ensure_brew(yes: bool) -> bool:
    """맥: brew 가 없으면 Homebrew 공식 설치(관리자 암호를 물을 수 있다)."""
    refresh_path()
    if shutil.which("brew"):
        return True
    print("   맥 프로그램 설치 도구 Homebrew 가 없다 — 공식 설치를 시작한다(맥 로그인 암호를 물어볼 수 있다).")
    if not yes and not ask("   Homebrew 를 설치할까요?"):
        print(f"   직접 설치: {BREW_INSTALL}")
        return False
    with urllib.request.urlopen(BREW_SCRIPT, timeout=60) as r:        # 공식 안내 명령과 같은 일
        subprocess.run(["/bin/bash", "-c", r.read().decode("utf-8")])
    refresh_path()
    brew = shutil.which("brew")
    if brew:
        prof = Path.home() / ".zprofile"
        line = f'eval "$({brew} shellenv)"'
        if line not in (prof.read_text() if prof.exists() else ""):
            with prof.open("a") as f:
                f.write(f"\n{line}\n")                   # 새 터미널에서도 brew 가 보이게(Homebrew 안내와 같은 줄)
    return bool(brew)


WINGET_FAMILY = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"
WINGET_SOURCE_MSIX = "https://cdn.winget.microsoft.com/cache/source.msix"


def _winget_source_ok() -> bool:
    """패키지 목록(winget 원본)을 실제로 읽을 수 있는지 — 갓 등록한 계정은 목록 묶음이 없어 0x8a15000f 가 난다."""
    try:
        r = subprocess.run([shutil.which("winget") or "winget", "show", "--id", "Gyan.FFmpeg", "-e",
                            "--source", "winget", "--accept-source-agreements"],
                           capture_output=True, timeout=180)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _winget_ok() -> bool:
    exe = shutil.which("winget")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--version"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def ensure_winget() -> bool:
    """윈도우: winget 이 안 보이거나 실행이 막히면 마이크로소프트 공식 복구(앱 설치 관리자 등록)를 한 번 한다."""
    refresh_path()
    if _winget_ok():
        return ensure_source()
    print("   winget(윈도우 프로그램 설치 도구)이 이 계정에 등록돼 있지 않다 — 등록을 시도한다…")
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Add-AppxPackage -RegisterByFamilyName -MainPackage {WINGET_FAMILY}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    apps = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
    if apps.is_dir() and str(apps) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = str(apps) + os.pathsep + os.environ.get("PATH", "")
    if not _winget_ok():
        return False
    return ensure_source()


def ensure_source() -> bool:
    if not _winget_source_ok():
        print("   winget 패키지 목록이 비어 있다 — 공식 목록 묶음(source.msix)을 설치한다…")
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Add-AppxPackage -Path {WINGET_SOURCE_MSIX}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _winget_source_ok():
            print("   (목록을 아직 못 읽는다 — 설치를 시도해 보고 안 되면 안내한다)")
    return True


def ask(q: str, default: bool = True) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return default
    a = input(f"{q} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not a else a in ("y", "yes", "네", "예", "ㅇ")


def install(tool: Tool) -> bool:
    """도구 하나 설치 → 다시 찾아서 결과."""
    if tool.auto:
        tool.auto()
    else:
        for line in (tool.win if WIN else tool.mac):
            if line.split()[0] == "winget" and not _winget_ok():
                raise FrogError("winget 이 없다 — 마이크로소프트 스토어에서 '앱 설치 관리자(App Installer)'를 설치·업데이트하자")
            code = run(_cmd_list(line))
            if code not in (0, -1978335189):           # winget '이미 설치됨' 코드도 성공으로 본다
                print(f"     (종료 코드 {code})")
    refresh_path()
    return bool(tool.find())


def os_name() -> str:
    return f"{platform.system()} {platform.release()} · {platform.machine()}"
