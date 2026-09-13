"""카카오 1회 인증 → 유저별 리프레시 토큰 저장.

사용법
  python scripts/kakao_auth.py --user kdu            # 브라우저가 열림 → 카카오 로그인 → 자동 저장
  python scripts/kakao_auth.py --user kdu --manual   # 다른 사람 PC 용: 링크만 출력, 받은 URL 의 code 를 붙여넣기
  python scripts/kakao_auth.py --user kdu --test     # 저장된 토큰으로 테스트 메시지 발송

사전 준비 (developers.kakao.com)
  1) 애플리케이션 추가 → [앱 키] REST API 키 → secrets/.env 의 KAKAO_REST_API_KEY
  2) [카카오 로그인] 활성화 ON, Redirect URI 에 http://localhost:5000/oauth 등록
  3) [카카오 로그인 > 동의항목] '카카오톡 메시지 전송(talk_message)' 선택 동의 설정
  4) (선택) [보안] Client Secret 발급 시 KAKAO_CLIENT_SECRET 도 .env 에 추가
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.common import load_env, setup_logging  # noqa: E402
from src.kakao import KakaoClient  # noqa: E402

REDIRECT_URI = "http://localhost:5000/oauth"


def wait_for_code(timeout: int = 300) -> str | None:
    result: dict[str, str] = {}
    done = threading.Event()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if "code" in q:
                result["code"] = q["code"][0]
                self.wfile.write("<h2>인증 완료. 이 창을 닫고 터미널로 돌아가세요.</h2>".encode("utf-8"))
                done.set()
            else:
                self.wfile.write(f"<h2>오류: {q}</h2>".encode("utf-8"))

        def log_message(self, *a):  # 콘솔 소음 억제
            pass

    srv = HTTPServer(("localhost", 5000), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    done.wait(timeout)
    srv.shutdown()
    return result.get("code")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="config/users.json 의 id")
    ap.add_argument("--manual", action="store_true", help="브라우저 자동 실행 없이 링크 출력 + code 수동 입력")
    ap.add_argument("--test", action="store_true", help="저장된 토큰으로 테스트 메시지 발송")
    ap.add_argument("--timeout", type=int, default=300, help="인증 대기 시간(초)")
    args = ap.parse_args()
    setup_logging()
    load_env()
    kakao = KakaoClient()

    if args.test:
        kakao.send_text(args.user, f"[테스트] 짧은 뉴스 카카오 연결 확인 ({args.user})", "https://hs3sf8ik.github.io/shortnews-kakao/", "확인")
        print("테스트 메시지를 보냈습니다. 카카오톡 '나와의 채팅'을 확인하세요.")
        return 0

    url = kakao.authorize_url(REDIRECT_URI, state=args.user)
    print("\n아래 링크에서 카카오 로그인 후 '카카오톡 메시지 전송'에 동의하세요:\n" + url + "\n")
    if args.manual:
        print("동의 후 브라우저가 이동하는 주소(http://localhost:5000/oauth?code=...)를 그대로 복사해 붙여넣으세요.")
        pasted = input("URL 또는 code: ").strip()
        code = parse_qs(urlparse(pasted).query).get("code", [pasted])[0]
    else:
        webbrowser.open(url)
        code = wait_for_code()
        if not code:
            print("5분 내에 인증이 완료되지 않았습니다. --manual 옵션으로 다시 시도하세요.")
            return 1
    tok = kakao.exchange_code(args.user, code, REDIRECT_URI)
    print(f"[{args.user}] 토큰 저장 완료 (scope={tok.get('scope')}). 리프레시 토큰은 2개월 유효, 매일 실행 시 자동 연장됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
