"""매일 실행 진입점.

python -m src.main                 # 수집 → 요약 → HTML → 카카오 발송
python -m src.main --dry-run       # 발송만 생략 (HTML 은 생성)
python -m src.main --use-cache     # 오늘자 data/ 캐시가 있으면 수집·요약 생략 (렌더/발송 테스트)
python -m src.main --skip-llm      # 수집만 하고 종료 (크롤링 점검)
python -m src.main --date 2026-09-13
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

import requests

from . import crawl as crawl_mod
from .common import DATA_DIR, ROOT, load_env, load_json, load_settings, load_users, log, now_kst, save_json, setup_logging
from .kakao import KakaoClient
from .publish import github_upload, page_paths, write_pages
from .render import render_html, render_kakao_text, render_text
from .summarize import summarize


def send_telegram(token: str, chat_id: str, text: str) -> None:
    # 텔레그램은 4096자 제한 → 항목 경계에서 분할
    chunks, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3900:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    for ch in chunks:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": ch, "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--dry-run", action="store_true", help="카카오/텔레그램 발송 생략")
    ap.add_argument("--use-cache", action="store_true", help="data/{date}/ 의 crawl.json·digest.json 재사용")
    ap.add_argument("--skip-llm", action="store_true", help="수집 후 종료")
    ap.add_argument("--user", help="특정 유저 id 에게만 발송")
    ap.add_argument("--backend", choices=["api", "claude-cli"], help="settings.llm.backend 덮어쓰기")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    load_env()
    settings = load_settings()
    if args.backend:
        settings["llm"]["backend"] = args.backend
    users = load_users()
    run_date = dt.date.fromisoformat(args.date) if args.date else now_kst().date()
    day_dir = DATA_DIR / run_date.isoformat()
    log.info("=== 짧은 뉴스 %s / 유저 %d명 ===", run_date, len(users))

    # 1) 수집
    crawl_path = day_dir / "crawl.json"
    if args.use_cache and crawl_path.exists():
        crawl = load_json(crawl_path)
        log.info("수집 캐시 사용 (%s)", crawl_path)
    else:
        crawl = crawl_mod.crawl(settings, run_date)
        save_json(crawl_path, crawl)
    if args.skip_llm:
        log.info("--skip-llm: 수집 결과 %s", crawl["stats"])
        return 0

    # 2) 요약
    digest_path = day_dir / "digest.json"
    if args.use_cache and digest_path.exists():
        digest = load_json(digest_path)
        log.info("요약 캐시 사용 (%s)", digest_path)
    else:
        digest = summarize(crawl, users, settings, run_date)
        save_json(digest_path, digest)

    # 3) 렌더 → docs/
    pub = settings["publish"]
    docs_dir = ROOT / pub.get("docs_dir", "docs")
    base_url = pub["page_base_url"].rstrip("/")
    pages = {page_paths(run_date, None): render_html(digest, crawl["extras"], crawl, run_date, None, "../index.html")}
    for u in users:
        pages[page_paths(run_date, u["id"])] = render_html(digest, crawl["extras"], crawl, run_date, u, "../index.html")
    files = write_pages(docs_dir, run_date, pages, users)
    full_text = render_text(digest, crawl["extras"], None, f"{base_url}/{page_paths(run_date, None)}")
    (day_dir / "digest.txt").write_text(full_text, encoding="utf-8")
    print("\n" + full_text)

    failures = 0
    if pub.get("github_upload") and not args.dry_run:
        try:
            github_upload(files, pub["github_repo"], pub.get("github_branch", "main"), f"digest {run_date}")
        except Exception as e:
            failures += 1
            log.error("GitHub Pages 업로드 실패 (카카오 링크가 열리지 않을 수 있음): %s", e)

    # 4) 발송
    if args.dry_run:
        log.info("--dry-run: 발송·업로드 생략")
        for u in users:
            print(f"\n[카카오 미리보기 · {u['id']}]\n{render_kakao_text(digest, run_date, u, settings['kakao']['max_text_chars'])}")
        return 0
    if settings["kakao"].get("enabled", True):
        kakao = KakaoClient()
        for u in users:
            if not u.get("kakao", True) or (args.user and u["id"] != args.user):
                continue
            url = f"{base_url}/{page_paths(run_date, u['id'])}"
            text = render_kakao_text(digest, run_date, u, settings["kakao"]["max_text_chars"])
            try:
                kakao.send_text(u["id"], text, url, settings["kakao"].get("button_title", "전체 뉴스 보기"))
            except Exception as e:
                failures += 1
                log.error("%s", e)

    tg = settings.get("telegram", {})
    if tg.get("enabled"):
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        for chat_id in tg.get("chat_ids", []):
            try:
                send_telegram(token, str(chat_id), full_text)
                log.info("텔레그램 발송 완료 %s", chat_id)
            except Exception as e:
                failures += 1
                log.error("텔레그램 발송 실패 %s: %s", chat_id, e)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
