"""frogshorts 명령 — setup(처음 준비) → analyze → plan → make → variant (+ adopt, 장부, doctor 점검)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .util import FrogError


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="frogshorts", description="참고 쇼츠 URL → 우리 가게 버전 쇼츠")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="참고 영상 받기·받아쓰기·격자·분석.md")
    a.add_argument("url")
    a.add_argument("-o", "--out", help="작업 폴더(기본 work/<영상ID>)")

    p = sub.add_parser("plan", help="분석 틀 + 가게 브리프 → shorts.yaml 초안")
    p.add_argument("folder")
    p.add_argument("--brief", required=True)
    p.add_argument("--force", action="store_true", help="이미 있는 shorts.yaml 을 덮는다")
    p.add_argument("--mode", choices=["새대사", "원본재현"],
                   help="새대사(새 대사를 더빙해 그 시간 기준) · 원본재현(원본 구간·길이·소리 기준). 기본은 브리프의 모드:")

    m = sub.add_parser("make", help="클립 생성 → 더빙 → 정렬 → 부품 → 마감 (기본은 계획만)")
    m.add_argument("folder")
    m.add_argument("--go", action="store_true", help="실제로 돈 쓰는 생성까지 진행")
    m.add_argument("--dry-run", action="store_true", help="계획·예상 비용만(기본값과 같음)")
    m.add_argument("--max-clips", type=int, default=3, help="이번 실행에서 새로 생성할 클립 상한")

    v = sub.add_parser("variant", help="바꾼 항목만 다시 생성한 변형")
    v.add_argument("folder")
    v.add_argument("--name", required=True)
    v.add_argument("--set", action="append", default=[], metavar="키=값",
                   help="변수(상품=…) · 대사.N=… · 컷.ID.프롬프트=… · 컷.ID.길이=7 · 컷.ID.참조=원본@4-7")
    v.add_argument("--go", action="store_true")
    v.add_argument("--max-clips", type=int, default=3)
    v.add_argument("--keep", default="", metavar="A,B", help="원본 클립을 그대로 쓸 컷(재료가 바뀌어도)")

    d = sub.add_parser("adopt", help="이미 만든 클립을 현재 프롬프트의 캐시로 등록(출처 기록)")
    d.add_argument("folder")
    d.add_argument("pairs", nargs="+", metavar="컷ID=파일")

    sub.add_parser("ledger", help="Vertex 장부 요약")

    e = sub.add_parser("estimate", help="대사 길이 미리 재기(더빙 전, 돈 안 듦)")
    e.add_argument("folder", nargs="?", help="작업 폴더(shorts.yaml) — 없으면 --text")
    e.add_argument("--text", help="문장 하나")
    e.add_argument("--pace", default="보통", help="느림 · 보통 · 빠름 · 숫자(음절/초)")

    s = sub.add_parser("snapshot", help="완성본에서 앵커 시각 화면을 뽑아 보기(렌더 없음)")
    s.add_argument("folder")
    s.add_argument("--at", help="쉼표로 여러 개: 대사2:단추,대사4:차콜(끝),12.5")
    s.add_argument("--around", help="이 앵커 앞뒤를 촘촘히")
    s.add_argument("--span", type=float, default=1.0)
    s.add_argument("--step", type=float, default=0.125)
    sub.add_parser("doctor", help="설치·로그인·권한 한 번에 점검(없는 것은 맥/윈도우 설치 명령)")

    u = sub.add_parser("setup", help="처음 준비 — 프로그램 자동 설치 → 구글 계정 연결 → 프로젝트 → 시험 호출")
    u.add_argument("--yes", action="store_true", help="묻지 않고 기본값으로 진행(설치 동의 포함)")
    u.add_argument("--project", help="쓸 구글 클라우드 프로젝트 ID(안 주면 목록에서 고른다)")
    u.add_argument("--skip-tools", action="store_true", help="프로그램 설치 단계를 건너뛴다")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "analyze":
            from . import analyze
            analyze.main(args.url, args.out)
        elif args.cmd == "plan":
            from . import plan
            plan.plan(Path(args.folder), Path(args.brief), args.force, args.mode)
        elif args.cmd == "make":
            from . import make
            make.make(Path(args.folder), go=args.go and not args.dry_run, max_clips=args.max_clips)
        elif args.cmd == "variant":
            from . import variant
            variant.variant(Path(args.folder), args.name, args.set, go=args.go, max_clips=args.max_clips,
                            keep=[x.strip() for x in args.keep.split(",") if x.strip()])
        elif args.cmd == "adopt":
            from . import make
            make.adopt(Path(args.folder), args.pairs)
        elif args.cmd == "ledger":
            from .providers import video
            video.ledger_summary()
        elif args.cmd == "estimate":
            _estimate(args)
        elif args.cmd == "snapshot":
            from . import snapshot
            snapshot.snapshot(Path(args.folder), args.at, args.around, args.span, args.step)
        elif args.cmd == "setup":
            from . import onboard
            sys.exit(onboard.main(yes=args.yes, project=args.project, skip_tools=args.skip_tools))
        elif args.cmd == "doctor":
            from . import doctor
            sys.exit(doctor.main())
    except FrogError as e:
        sys.exit(f"✗ {e}")


def _estimate(args) -> None:
    from . import speech
    from .util import say
    if args.text:
        rate = speech.pace_of({"빠르기": args.pace})
        say(f"{speech.estimate(args.text, rate):.2f}초 · {speech.units(args.text)}음절 · {rate}음절/초 · 읽기 「{speech.spoken(args.text)}」")
        return
    if not args.folder:
        raise FrogError("작업 폴더 또는 --text 를 준다")
    from .providers import video
    from .spec import load
    from .util import job_root
    spec = load(Path(args.folder))
    lens = speech.plan_lengths(spec, job_root(Path(args.folder)) / "캐시")
    for n, v in lens.items():
        say(f"  대사{n} {v['초']:.2f}초 ({v['근거']})")
    tot = speech.total(spec, lens)
    need = speech.clips_needed(tot, video.MAX_SEC)
    say(f"  합계 약 {tot:.1f}초 · 클립 최소 {need}개(지금 {len(spec.cuts)}개) · 클립 비용 약 ${need * video.est_usd(None):.1f}")


if __name__ == "__main__":
    main()
