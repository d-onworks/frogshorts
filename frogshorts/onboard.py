"""`frogshorts setup` — 처음 한 번 따라 하면 끝나는 준비 마법사(초보용).

1) 필요한 프로그램 확인 → 없으면 자동 설치
2) 내 구글 계정 연결(ADC — 브라우저에서 로그인·허용 한 번)
3) 구글 클라우드 프로젝트 고르기 → frogshorts.toml 에 저장
4) Vertex AI 켜기(결제·무료 체험 크레딧 확인)
5) 시험 호출(글 한 줄, 1원 미만) → "연결되었습니다"
막히면 무엇을 하면 되는지 한 줄씩 알려 주고, 고친 뒤 같은 명령을 다시 실행하면 이어서 된다.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

from . import config, gauth, install
from .util import FrogError, which

FREE_TRIAL = "https://console.cloud.google.com/freetrial"
CRM = "https://cloudresourcemanager.googleapis.com/v1/projects"
SU = "https://serviceusage.googleapis.com/v1/projects/{p}/services/aiplatform.googleapis.com"


def _h(n: int, title: str) -> None:
    print(f"\n[{n}/5] {title}")


def _ok(msg: str) -> None:
    print(f"   ✓ {msg}")


def _bad(msg: str, *fix: str) -> None:
    print(f"   ✗ {msg}")
    for f in fix:
        print(f"     → {f}")


# ── 0. frogshorts 명령 ───────────────────────────────────────────────────────

def _tool_bin() -> Path:
    uv = which("uv")
    if uv:
        r = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip())
    return Path.home() / ".local" / "bin"


def step_command() -> bool:
    """`uv run frogshorts setup` 으로 시작했어도, 끝나면 새 창 어느 폴더에서든 `frogshorts` 로 부르게 등록한다.
    실패해도 멈추지 않는다(이 폴더에서 `uv run frogshorts …` 로 부르면 되니까)."""
    _h(0, "frogshorts 명령 등록(새 창 어느 폴더에서든 부를 수 있게)")
    exe = _tool_bin() / ("frogshorts.exe" if install.WIN else "frogshorts")
    if exe.exists():
        _ok(f"frogshorts 명령 — 있음 ({exe})")
        return True
    uv = which("uv")
    if not uv:
        _bad("uv 가 없어 명령 등록은 건너뛴다", "이 폴더에서 `uv run frogshorts …` 로 부르면 된다")
        return True
    rc = install.run([uv, "tool", "install", "--python", "3.12", "--editable", str(config.REPO), "--force", "--quiet"])
    if rc != 0:
        _bad("명령 등록 실패 — 그래도 계속한다", "이 폴더에서 `uv run frogshorts …` 로 부르면 된다")
        return True
    install.run([uv, "tool", "update-shell"], quiet=True)
    _ok(f"frogshorts 명령 등록됨 ({exe})")
    print("     → 지금 이 창에서는 `uv run frogshorts …`, **새 창부터는 그냥 `frogshorts …`**")
    return True


# ── 1. 프로그램 ──────────────────────────────────────────────────────────────

def step_tools(yes: bool) -> bool:
    _h(1, "필요한 프로그램 확인·설치")
    install.refresh_path()
    missing = [t for t in install.TOOLS if not t.find()]
    for t in install.TOOLS:
        if t not in missing:
            _ok(f"{t.key} — 있음")
    if not missing:
        return True
    print(f"   없는 것 {len(missing)}개: {', '.join(t.key for t in missing)}")
    if not install.WIN and any(not t.auto for t in missing) and not install.ensure_brew(yes):
        _bad("Homebrew 가 없어 자동 설치를 못 한다", f"터미널에 붙여 넣기: {install.BREW_INSTALL}",
             "끝나면 이 명령을 다시 실행")
        return False
    if install.WIN and any(not t.auto for t in missing) and not install.ensure_winget():
        _bad("winget 을 쓸 수 없어 자동 설치를 못 한다",
             "마이크로소프트 스토어에서 '앱 설치 관리자(App Installer)'를 설치·업데이트한 뒤 이 명령을 다시 실행")
        return False
    if not yes and not install.ask("   지금 자동으로 설치할까요? (몇 분 걸린다)"):
        _bad("설치를 건너뛰었다", "`frogshorts doctor` 가 하나씩 설치 명령을 보여 준다")
        return False
    still = []
    for t in missing:
        if t.key == "hyperframes" and not which("npm"):
            install.refresh_path()
        print(f"   · {t.key} 설치 중 — {t.why}")
        try:
            done = install.install(t)
        except (FrogError, OSError) as e:
            print(f"     {e}")
            done = False
        (_ok if done else lambda m: _bad(m, *(t.win if install.WIN else t.mac)))(
            f"{t.key} — {'설치됨' if done else '설치 실패'}")
        if not done:
            still.append(t)
    if still:
        print("\n   설치가 안 된 것이 있다. 위 → 줄대로 한 뒤, **새 터미널**을 열어 이 명령을 다시 실행하자.")
        return False
    return True


# ── 2. 구글 계정 ─────────────────────────────────────────────────────────────

def step_login(yes: bool) -> bool:
    _h(2, "내 구글 계정 연결")
    if gauth.mode() == "account":
        try:
            gauth.token()
            _ok(f"설정의 계정 {gauth.account()} 로 연결됨(gcloud 계정 지정 방식)")
            return True
        except FrogError as e:
            _bad(str(e), f"gcloud auth login {gauth.account()} --no-activate")
            return False
    if gauth.adc_info():
        try:
            gauth.token()
            _ok(f"연결됨 — {gauth.email() or '구글 계정'} (로그인 파일 {gauth.adc_path()})")
            return True
        except FrogError as e:
            print(f"   예전 로그인이 안 된다: {e}")
    exe = gauth.gcloud()
    if not exe:
        _bad("gcloud 가 없어 로그인 창을 못 연다", "1단계 설치를 먼저 끝내고 다시 실행")
        return False
    print("   이제 브라우저가 열린다. 영상 만들 때 쓸 **내 구글 계정**으로 로그인하고,")
    print("   'Google Auth Library' 가 접근하려 한다는 화면에서 **허용(계속)** 을 누르면 된다.")
    print("   (브라우저가 안 열리면 터미널에 나온 주소를 복사해 브라우저에 붙여 넣는다)")
    if not yes and not install.ask("   준비됐으면 Enter"):
        return False
    r = subprocess.run([exe, "auth", "application-default", "login"])
    gauth._cache.clear()
    if r.returncode != 0 or not gauth.adc_info():
        _bad("로그인이 끝나지 않았다", "브라우저에서 '허용'까지 눌렀는지 확인하고 이 명령을 다시 실행")
        return False
    try:
        gauth.token()
    except FrogError as e:
        _bad(str(e))
        return False
    _ok(f"연결되었다 — {gauth.email() or '구글 계정'}")
    return True


# ── 3. 프로젝트 ──────────────────────────────────────────────────────────────

def _projects() -> list[dict]:
    try:
        r = gauth.call(f"{CRM}?filter=lifecycleState:ACTIVE&pageSize=50", user_project=False)
    except urllib.error.HTTPError:
        return []
    return sorted(r.get("projects", []), key=lambda p: p.get("createTime", ""), reverse=True)


def save_project(pid: str) -> Path:
    """저장소의 frogshorts.toml 에 [vertex] project 를 적는다(없으면 예시를 복사해 만든다)."""
    f = config.REPO / "frogshorts.toml"
    if not f.exists():
        f.write_text((config.REPO / "frogshorts.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    s = f.read_text(encoding="utf-8")
    line = f'project = "{pid}"'
    m = re.search(r"(?ms)^\[vertex\]\s*$(.*?)(?=^\[|\Z)", s)
    if m and re.search(r"(?m)^project\s*=.*$", m.group(1)):
        body = re.sub(r"(?m)^project\s*=.*$", line, m.group(1), count=1)
        s = s[:m.start(1)] + body + s[m.end(1):]
    elif m:
        s = s[:m.end(0) - len(m.group(1))] + "\n" + line + s[m.end(0) - len(m.group(1)):]
    else:
        s += f"\n[vertex]\n{line}\n"
    f.write_text(s, encoding="utf-8")
    config.load.cache_clear()
    return f


PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


def step_project(yes: bool, given: str | None) -> str | None:
    _h(3, "구글 클라우드 프로젝트 고르기")
    if given and not PROJECT_ID.match(given):
        _bad(f"'{given}' 은 프로젝트 ID 모양이 아니다 — 영어 소문자·숫자·하이픈 6~30자(예: my-shop-123456)",
             "콘솔 위쪽 프로젝트 선택 창의 'ID' 칸 값을 그대로 복사한다(이름이 아니라 ID). 모르면 --project 없이 실행해 목록에서 고른다")
        return None
    cur = given or gauth.project(required=False)
    if cur and not given:
        _ok(f"이미 정해짐 — {cur}")
        return cur
    if not cur:
        ps = _projects()
        if not ps:
            _bad("이 구글 계정에 프로젝트가 없다(또는 목록을 못 읽었다)",
                 f"처음이면 무료 체험부터: {FREE_TRIAL}  (카드 등록이 필요하지만 직접 '업그레이드'하기 전엔 "
                 "결제되지 않는다. 시작하면 'My First Project' 가 생긴다)",
                 "다 됐으면 이 명령을 다시 실행. 이미 프로젝트 ID 를 알면: frogshorts setup --project <ID>")
            return None
        if len(ps) == 1 or yes or not sys.stdin.isatty():
            cur = ps[0]["projectId"]
            print(f"   프로젝트: {ps[0].get('name', '')} ({cur})")
            if len(ps) > 1:                  # 묻지 않고 첫 번째를 골랐다 — 다른 걸 쓰는 법을 같이 보여 준다
                print(f"   (프로젝트가 {len(ps)}개라 첫 번째로 했다: " + ", ".join(p["projectId"] for p in ps)
                      + " — 다른 걸 쓰려면 frogshorts setup --project <ID>)")
        else:
            for i, p in enumerate(ps, 1):
                print(f"   {i}. {p.get('name', '')}  ({p['projectId']})")
            while True:
                a = input("   어느 프로젝트로 할까요? 번호: ").strip()
                if a.isdigit() and 1 <= int(a) <= len(ps):
                    cur = ps[int(a) - 1]["projectId"]
                    break
    f = save_project(cur)
    _ok(f"{cur} — 저장함({f.name})")
    return cur


# ── 4. Vertex AI 켜기 ────────────────────────────────────────────────────────

def _billing_fix(pid: str) -> list[str]:
    return [f"결제 연결 화면: https://console.cloud.google.com/billing/linkedaccount?project={pid}",
            f"결제 계정이 없으면 무료 체험($300 크레딧)부터: {FREE_TRIAL}",
            "연결했으면 이 명령을 다시 실행"]


def step_enable(pid: str) -> bool:
    _h(4, "Vertex AI 켜기")
    url = SU.format(p=pid)
    try:
        if gauth.call(url).get("state") == "ENABLED":
            _ok("이미 켜져 있다")
            return True
        print("   켜는 중…(1분쯤)")
        op = gauth.call(url + ":enable", {})
        for _ in range(30):
            if op.get("done") or not op.get("name"):
                break
            time.sleep(4)
            op = gauth.call(f"https://serviceusage.googleapis.com/v1/{op['name']}")
        if op.get("error"):
            msg = str(op["error"].get("message", ""))
            if "billing" in msg.lower():
                _bad("이 프로젝트에 결제(또는 무료 체험 크레딧)가 연결되지 않았다", *_billing_fix(pid))
            else:
                _bad(f"켜기 실패: {msg[:200]}")
            return False
    except urllib.error.HTTPError as e:
        reason, msg = gauth.error_text(e)
        if "billing" in msg.lower() or reason == "BILLING_DISABLED":
            _bad("이 프로젝트에 결제(또는 무료 체험 크레딧)가 연결되지 않았다", *_billing_fix(pid))
        elif e.code in (401, 403):
            _bad(f"이 계정에 프로젝트 {pid} 권한이 없다 ({msg[:120]})",
                 "프로젝트 소유자 계정으로 연결했는지 확인 — 다른 프로젝트면: frogshorts setup --project <ID>")
        else:
            _bad(f"HTTP {e.code} {reason} {msg[:160]}")
        return False
    _ok("켰다")
    return True


# ── 5. 시험 호출 ─────────────────────────────────────────────────────────────

def step_test(pid: str) -> bool:
    _h(5, "시험 호출 — 글 한 줄(1원 미만)")
    from .providers import gemini
    model = str(config.get("gemini", "vision_model") or "gemini-3.5-flash")
    if gemini.via() != "vertex":
        print("   (설정 [gemini] via = apikey — 더빙·글씨 찾기는 API 키로 부른다)")
    print("   " + gemini.billing(model))
    body = {"contents": [{"role": "user", "parts": [{"text": "Reply with exactly: OK"}]}]}
    for attempt in range(4):
        try:
            r = gauth.call(gemini._vertex_url(model), body)
            text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"]).strip()
            _ok(f"Vertex AI 연결되었습니다 — 모델 응답 「{text[:20]}」 · 프로젝트 {pid}")
            return True
        except urllib.error.HTTPError as e:
            reason, msg = gauth.error_text(e)
            if (reason in ("SERVICE_DISABLED",) or e.code == 403 and "has not been used" in msg) and attempt < 3:
                print("   방금 켠 API 가 퍼지는 중 — 20초 뒤 다시")
                time.sleep(20)
                continue
            if "billing" in msg.lower() or reason == "BILLING_DISABLED":
                _bad("결제(또는 무료 체험 크레딧)가 연결되지 않았다", *_billing_fix(pid))
            elif e.code == 404:
                _bad(f"모델 {model} 을 못 찾았다 — 설정 [gemini] vision_model 확인 ({msg[:100]})")
            else:
                _bad(f"HTTP {e.code} {reason} {msg[:200]}")
            return False
        except (KeyError, IndexError, FrogError) as e:
            _bad(f"응답이 이상하다: {str(e)[:150]}")
            return False
    return False


DONE = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 준비 끝! 영상·더빙·글씨 찾기 모두 이 구글 프로젝트({pid})로 부른다.
 (새 구글 클라우드 계정은 무료 체험 크레딧 $300 — 클립 1개 약 $0.5~1)

 이제 이렇게 만든다:
  ① 참고할 쇼츠 분석     frogshorts analyze <유튜브 쇼츠 주소> -o work/내가게
  ② 내 가게 정보 넣기     briefs/예시-초록연못빵집.yaml 을 복사해 가게·상품·대사를 바꾸고
                          frogshorts plan work/내가게 --brief briefs/내가게.yaml
  ③ 계획·비용 보기        frogshorts make work/내가게          (돈 안 듦)
  ④ 영상 만들기
     · AI 로 생성         frogshorts make work/내가게 --go
     · 내가 찍은 영상     frogshorts adopt work/내가게 A=내영상.mp4   → 그다음 make --go
  완성본은 work/내가게/output/ 에 생긴다. 막히면: frogshorts doctor
  (frogshorts 명령이 안 잡히면 새 터미널을 열거나, 저장소 폴더에서 uv run frogshorts …)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


def main(yes: bool = False, project: str | None = None, skip_tools: bool = False) -> int:
    try:                                     # 클로드 같은 도우미가 돌릴 때도 한 줄씩 바로 보이게
        sys.stdout.reconfigure(errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass
    print(f"frogshorts 준비 마법사 — {install.os_name()}")
    print("처음 한 번만 하면 된다. 중간에 막히면 안내대로 고치고 같은 명령을 다시 실행하면 이어서 된다.")
    step_command()
    if not skip_tools and not step_tools(yes):
        return 1
    if not step_login(yes):
        return 1
    pid = step_project(yes, project)
    if not pid or not step_enable(pid) or not step_test(pid):
        return 1
    print(DONE.format(pid=pid))
    return 0
