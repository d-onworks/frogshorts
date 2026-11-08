# frogshorts

참고할 쇼츠 영상 주소 하나와 내 가게 정보(브리프)를 넣으면, 같은 짜임새의 **9:16 완성 쇼츠**를 만든다.
영상은 AI(구글 Vertex AI Omni)로 만들고, 더빙은 제미나이 TTS, 자막·가격표·랭킹 같은 화면 부품은 자동으로 얹는다.
상품이나 대사만 바꾼 **변형**은 바뀐 부분만 다시 만들어 싸게 뽑는다.

- 맥·윈도우 둘 다 된다.
- 영상·더빙·글씨 찾기를 **모두 내 구글 클라우드 프로젝트 하나(Vertex AI)** 로 부른다 — 무료 체험 크레딧 하나로 전부.
- 돈이 드는 생성은 `--go` 를 붙였을 때만 한다. 기본은 **계획과 예상 비용만** 보여 준다.

---

## 1. 시작 (처음 한 번 — 명령 몇 줄)

**준비물**: 구글 계정 하나. 구글 클라우드가 처음이면 [무료 체험](https://console.cloud.google.com/freetrial)을 먼저 시작한다
(카드 등록이 필요하지만 직접 '업그레이드'하기 전엔 결제되지 않는다. 크레딧 $300 · 90일).

> 내려받은 `.bat`·스크립트 파일을 실행하지 않는다. 공식 패키지 관리자(윈도우 winget · 맥 Homebrew)로 git·uv 를 깔고,
> 저장소를 받아 파이썬 명령으로 시작한다.

**클로드(VS Code·데스크톱 앱)에게 시켜도 된다** — 빈 폴더를 열고 이렇게 말한다.
1. `https://github.com/d-onworks/frogshorts 깃 클론해줘`
2. `frogshorts 폴더에서 uv run frogshorts setup 실행해줘` (uv 가 없으면 설치부터 해 달라고 한다)
3. 브라우저에 구글 로그인 창이 뜨면 **내 구글 계정으로 직접 로그인 → 허용**. 비밀번호·키는 클로드에게 알려 주지 않는다.

직접 하려면 아래처럼 한다.

**윈도우** — 시작 메뉴에서 "터미널"(또는 PowerShell)을 연다.
```powershell
winget install -e --id Git.Git --source winget --accept-package-agreements --accept-source-agreements
winget install -e --id astral-sh.uv --source winget --accept-package-agreements --accept-source-agreements
```
→ **터미널을 닫고 새로 연 뒤**
```powershell
git clone https://github.com/d-onworks/frogshorts.git
cd frogshorts
uv run frogshorts setup
```

**맥** — "터미널" 앱을 연다(Homebrew 가 없으면 [brew.sh](https://brew.sh) 의 한 줄을 먼저).
```bash
brew install git uv
git clone https://github.com/d-onworks/frogshorts.git
cd frogshorts
uv run frogshorts setup
```

`uv run` 이 파이썬 3.12 와 필요한 것을 알아서 준비한 뒤 **준비 마법사**를 띄운다. 화면 안내대로 따라 한다.

| 단계 | 하는 일 | 내가 할 것 |
|---|---|---|
| 0 | `frogshorts` 명령 등록(새 창 어느 폴더에서든 부르게) | 없음 |
| 1 | ffmpeg · yt-dlp · node · hyperframes · gcloud · 받아쓰기(whisper) 확인 → 없으면 **자동 설치** | "설치할까요?"에 Enter (윈도우는 관리자 허용 창이 뜨면 '예') |
| 2 | 내 구글 계정 연결(ADC) | 브라우저가 열리면 **내 구글 계정으로 로그인 → 허용** |
| 3 | 구글 클라우드 프로젝트 고르기 → `frogshorts.toml` 에 자동 저장 | 여러 개면 번호 고르기 |
| 4 | Vertex AI 켜기 · 결제(무료 크레딧) 연결 확인 | 결제가 안 돼 있으면 안내된 주소에서 연결 |
| 5 | 시험 호출(글 한 줄, 1원 미만) | 없음 — **"Vertex AI 연결되었습니다"** 가 나오면 끝 |

막히면 ✗ 줄 아래 → 안내대로 고치고 **같은 명령(`uv run frogshorts setup`)을 다시 실행**하면 된 곳은 건너뛰고 이어서 한다.
끝나면 **새 터미널**에서 그냥 `frogshorts …` 로 부른다. 언제든 점검: `frogshorts doctor`.

<details><summary>직접 하고 싶다면(마법사 없이)</summary>

`uv tool install --python 3.12 --editable .` → `frogshorts doctor` 가 보여 주는 설치 명령을 하나씩 →
`gcloud auth application-default login` → `frogshorts setup --skip-tools`.
더빙·글씨 찾기를 AI Studio API 키로 부르고 싶으면 `frogshorts.toml` 의 `[gemini] via = "apikey"` + `.env` 에 `GEMINI_API_KEY=…`.
</details>

---

## 2. 첫 영상까지 (예시: 가상 가게 "초록연못 빵집")

```bash
# ① 참고 영상 분석 — 받기 → 단어 단위 받아쓰기 → 단어가 적힌 프레임 격자 → 분석.md · 틀.yaml
frogshorts analyze https://www.youtube.com/shorts/<영상ID> -o work/빵집

# ② 분석.md 의 '해석' 칸과 틀.yaml(화면 띠·카메라 문단)을 격자 그림을 보며 채운다.
#    (참고 영상의 초 단위 타이밍을 베끼지 않는다 — 옮기는 건 짜임새·리듬·말과 그림의 관계)

# ③ 내 가게 정보 → 작업파일(shorts.yaml)
frogshorts plan work/빵집 --brief briefs/예시-초록연못빵집.yaml

# ④ 계획과 예상 비용만 보기(돈 안 듦)
frogshorts make work/빵집

# ⑤ 실제로 만들기 — 클립 상한 기본 3개(--max-clips 로 조정)
frogshorts make work/빵집 --go
```
완성본은 `work/빵집/output/*.mp4`(1080×1920, -14 LUFS), 검수 자료는 `work/빵집/검수/` 에 생긴다.

내 가게로 할 때는 `briefs/예시-초록연못빵집.yaml` 을 복사해 가게 이름·상품·대사·장면만 바꾼다.

### 변형 뽑기
```bash
frogshorts variant work/빵집 --name 바게트 --set 상품="바게트" --set 대사.2="겉은 바삭하고 속은 쫄깃해요."
#   → 컷마다 "그대로 / 다시 만듦(이유)"와 돈 드는 것만 먼저 보여 준다
frogshorts variant work/빵집 --name 바게트 --go
frogshorts variant work/빵집 --name 바게트 --keep A --go     # 컷 A 는 원본 클립을 그대로(돈 0)
```

### 그 밖의 명령
| 명령 | 하는 일 |
|---|---|
| `frogshorts estimate work/빵집` | 더빙 전에 대사 길이·필요 클립 수·비용을 어림 |
| `frogshorts estimate --text "문장"` | 문장 하나 길이(숫자는 읽는 소리로: 4,500원 → 사천오백원) |
| `frogshorts snapshot work/빵집 --at "대사2:결이,대사4:일찍"` | 완성본에서 그 말이 나올 때 화면만 뽑아 보기 |
| `frogshorts snapshot work/빵집 --around "대사2:결이"` | 그 말 앞뒤 1초를 촘촘히(컷 넘김 확인) |
| `frogshorts adopt work/빵집 A=내클립.mp4` | 이미 만든 클립을 컷 A 로 등록(출처 기록) |
| `frogshorts ledger` | 영상 생성 장부(건수·추정 비용·최근 실패 이유) |
| `frogshorts doctor` | 설치·로그인·권한 점검 |

---

## 3. 작업파일(shorts.yaml) 핵심

- **`대사:` 가 말의 유일한 원본.** 자막은 대사 글 그대로, 더빙은 `발음:` 표를 적용한 소리로 읽는다.
  `|` 를 넣은 자리에서 자막을 끊는다. 숫자는 한글로 적으면 받아쓰기와 더 잘 맞는다.
- **화면은 "몇 초"가 아니라 "대사의 어느 단어"에 붙는다(앵커).**
  | 앵커 | 뜻 |
  |---|---|
  | `대사3` | 3번 대사 첫 단어 시작 |
  | `대사3:차콜` · `대사3:차콜#2` | '차콜'이 들어간 첫(두 번째) 단어 시작 |
  | `대사3:차콜(끝)` · `대사3:끝` | 그 단어가 끝나는 때 · 3번 대사 마지막 단어 끝 |
  | `대사3:차콜 -0.2초` · `+5f` | 그만큼 앞당기거나 늦춤(f = 프레임) |
  대사를 고치면 그 줄만 다시 더빙 → 단어 시각을 다시 맞춤 → 화면이 따라온다.
  붙어 있던 단어가 대사에서 사라지면 **조용히 넘기지 않고 멈춘다.**
  받아쓰기에서 못 찾아 시각을 '추정'한 단어에 앵커가 걸리면 경고한다.
- **컷**: `길이: 3~10 | 자동`(자동 = 더빙 길이에 맞춰 필요한 만큼만 생성) · `참조: A@0`(앞 컷 첫 화면) ·
  `참조: [원본@4.2, 원본@4-7]`(참고 영상의 정지 화면·3초 동작 영상) · `흐림:`(손으로 적는 글씨 가림 영역) ·
  `고정:`(재료가 바뀌어도 이 클립 그대로).
- **부품**: `배너` · `자막`(노래방식, 실제 글꼴 너비로 2줄 이내) · `배지` · `엔딩` · `AI표시` · `랭킹` · `댓글`.
  `댓글` 은 **실제 댓글만** — `출처:` 에 댓글 링크를 적고, 시연용이면 `출처: 예시`(화면에 "예시 댓글" 표시가 붙는다).
- **마감**: `해상도: 720p | 1080p` · `글씨흐림: 자동 | 끔` · `자막강조: 현재 | 누적` · `LUFS: -14` 등.
- **모드**: `새대사`(기본, 새 더빙 기준) · `원본재현`(`frogshorts plan … --mode 원본재현`, 원본 구간·길이·소리 기준.
  원본 소리·구성은 그 영상 제작자의 것이라 **연습·강의용** — 올리려면 권리부터 확인한다).

---

## 4. 돈과 정직성

- 전부 내 Vertex 프로젝트(무료 체험 크레딧이 있으면 거기서 먼저 차감)로 과금된다.
  영상 클립: 초당 약 $0.1(10초 클립 약 $1, 720p 는 더 싸다), 계획 출력에 예상 비용이 나온다.
  생성할 때마다 "어느 계정·어느 프로젝트에서 과금되는지" 한 줄을 먼저 보여 주고, 장부에 적는다.
- 같은 재료(프롬프트·참조·길이·해상도)로 만든 클립은 다시 돈을 쓰지 않는다. 이미 있는 클립 파일은 덮지 않는다.
- 더빙(제미나이 3.8 TTS)·글씨 찾기(제미나이 비전): 같은 프로젝트 — 문장당·화면당 몇 원 수준.
- AI 로 만든 화면을 실제 매장 촬영처럼 속이지 않는다(AI표시 부품). 가짜 후기·가짜 통계·없는 할인율은 쓰지 않는다.
  사람 얼굴·목소리가 실제 손님이면 올리기 전에 허락을 받는다.

## 5. 막혔을 때
- `frogshorts doctor` 결과의 ✗ 줄부터 해결한다.
- 생성이 `safety` 로 막히면: 무엇에 걸렸는지는 알려 주지 않는다. 강제·위험해 보이는 동작 묘사를 부드럽게 고쳐 그 컷만
  다시 `--go` — 앞 컷은 캐시로 재사용된다. 실패 이유는 `frogshorts ledger` 에 남는다.
- 구글 연결이 끊겼다("로그인이 만료됐다"): `frogshorts setup` 을 다시 — 2단계에서 다시 로그인한다.
- 개발·시험: `uv venv .venv -p 3.12` → `uv pip install --python .venv -e ".[dev]"` → `.venv/bin/python -m pytest -q`
  (돈 드는 호출은 가짜로 바꿔 시험한다).

## 6. 함께 들어 있는 것
- 글꼴 Pretendard(SIL OFL 1.1), 애니메이션 라이브러리 GSAP 3.15(무료 라이선스) 사본 — `frogshorts/parts/`.
