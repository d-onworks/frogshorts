"""공통 도구 — 외부 명령 실행, 미디어 길이, 해시, YAML 읽기/쓰기, 경로 찾기."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

KST = ZoneInfo("Asia/Seoul")
PKG = Path(__file__).resolve().parent


class FrogError(RuntimeError):
    """사용자에게 그대로 보여 줄 실패(조용히 넘기면 안 되는 것)."""


def now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _extra_dirs() -> list[str]:
    """PATH 에 안 올라왔어도 설치 도구가 흔히 두는 곳(윈도우 npm 전역·winget 링크, 맥 Homebrew)."""
    import os
    if os.name == "nt":
        env = os.environ
        cands = [Path(env.get("APPDATA", "")) / "npm", Path(env.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links",
                 Path(env.get("ProgramFiles", r"C:\Program Files")) / "nodejs"]
    else:
        cands = [Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    return [str(d) for d in [*cands, Path.home() / ".local" / "bin"] if d.is_dir()]


def which(name: str) -> str | None:
    """실행 파일 찾기 — PATH 먼저, 없으면 설치 도구 기본 폴더(새 창에 PATH 가 아직 안 퍼진 경우)."""
    import os
    return shutil.which(name) or shutil.which(name, path=os.pathsep.join(_extra_dirs()))


def need(*tools: str) -> None:
    missing = [t for t in tools if not which(t)]
    if missing:
        raise FrogError(f"외부 도구가 없다: {', '.join(missing)} — 설치 후 다시 실행")


def run(cmd: list, quiet: bool = True, **kw) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    cmd[0] = which(cmd[0]) or cmd[0]
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-1500:]
        raise FrogError(f"명령 실패({cmd[0]}): {tail}")
    if not quiet and r.stdout:
        print(r.stdout)
    return r


def duration(path: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def has_audio(path: Path) -> bool:
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", path])
    return bool(r.stdout.strip())


def sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, Path):
            h.update(p.read_bytes())
        else:
            h.update(json.dumps(p, ensure_ascii=False, sort_keys=True).encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def dump_yaml(data, path: Path) -> None:
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=1000),
                          encoding="utf-8")


def write_json(data, path: Path) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def job_root(folder: Path) -> Path:
    """캐시를 공유하는 작업 루트(변형 폴더면 부모 쪽). '캐시/' 또는 '분석.md' 가 있는 가장 가까운 조상."""
    folder = Path(folder).resolve()
    for p in [folder, *folder.parents]:
        if (p / "분석.md").exists() or (p / "캐시").is_dir():
            return p
        if p.name == "work":
            break
    return folder


def say(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    print(f"⚠ {msg}", file=sys.stderr, flush=True)
