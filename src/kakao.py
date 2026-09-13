"""카카오톡 '나에게 보내기' API + 토큰 저장/갱신.

토큰 저장소
- 로컬:  secrets/kakao_tokens.json (평문, .gitignore)
- CI:    KAKAO_TOKENS_KEY 환경변수(Fernet 키)가 있으면 secrets/kakao_tokens.enc 를 암·복호화해 사용.
         GitHub Actions 가 갱신된 .enc 파일을 커밋해 리프레시 토큰 회전을 유지한다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time

import requests

from .common import SECRETS_DIR, log

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
PLAIN_PATH = SECRETS_DIR / "kakao_tokens.json"
ENC_PATH = SECRETS_DIR / "kakao_tokens.enc"


class TokenStore:
    def __init__(self):
        self.key = os.environ.get("KAKAO_TOKENS_KEY", "").strip()
        self.data: dict[str, dict] = self._load()

    # ---- 저장/로드
    def _fernet(self):
        from cryptography.fernet import Fernet  # 지연 import (로컬 평문 모드에선 불필요)
        return Fernet(self.key.encode())

    def _load(self) -> dict:
        if self.key and ENC_PATH.exists():
            return json.loads(self._fernet().decrypt(ENC_PATH.read_bytes()).decode("utf-8"))
        if PLAIN_PATH.exists():
            return json.loads(PLAIN_PATH.read_text(encoding="utf-8"))
        return {}

    def save(self) -> None:
        SECRETS_DIR.mkdir(exist_ok=True)
        raw = json.dumps(self.data, ensure_ascii=False, indent=2)
        if self.key:
            ENC_PATH.write_bytes(self._fernet().encrypt(raw.encode("utf-8")))
        else:
            PLAIN_PATH.write_text(raw, encoding="utf-8")

    # ---- 유저별 토큰
    def get(self, user_id: str) -> dict | None:
        return self.data.get(user_id)

    def put(self, user_id: str, tok: dict) -> None:
        cur = self.data.get(user_id, {})
        now = time.time()
        cur["access_token"] = tok["access_token"]
        cur["access_expires_at"] = now + int(tok.get("expires_in", 21600)) - 60
        if tok.get("refresh_token"):  # 갱신 응답에는 남은 기간이 1개월 미만일 때만 새 리프레시 토큰이 포함됨
            cur["refresh_token"] = tok["refresh_token"]
            cur["refresh_expires_at"] = now + int(tok.get("refresh_token_expires_in", 5184000))
        cur["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        self.data[user_id] = cur
        self.save()


class KakaoClient:
    def __init__(self, store: TokenStore | None = None):
        self.rest_key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
        self.client_secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
        if not self.rest_key:
            raise RuntimeError("KAKAO_REST_API_KEY 가 설정되지 않았습니다 (secrets/.env 또는 환경변수)")
        self.store = store or TokenStore()

    # ---- OAuth
    def authorize_url(self, redirect_uri: str, state: str = "") -> str:
        q = {"client_id": self.rest_key, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": "talk_message"}
        if state:
            q["state"] = state
        return AUTH_URL + "?" + requests.compat.urlencode(q)

    def exchange_code(self, user_id: str, code: str, redirect_uri: str) -> dict:
        data = {"grant_type": "authorization_code", "client_id": self.rest_key,
                "redirect_uri": redirect_uri, "code": code}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = requests.post(TOKEN_URL, data=data, timeout=15)
        tok = r.json()
        if "access_token" not in tok:
            raise RuntimeError(f"토큰 발급 실패: {tok}")
        self.store.put(user_id, tok)
        return tok

    def refresh(self, user_id: str) -> str:
        cur = self.store.get(user_id)
        if not cur or not cur.get("refresh_token"):
            raise RuntimeError(f"[{user_id}] 카카오 토큰이 없습니다. scripts/kakao_auth.py --user {user_id} 로 먼저 인증하세요")
        data = {"grant_type": "refresh_token", "client_id": self.rest_key, "refresh_token": cur["refresh_token"]}
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = requests.post(TOKEN_URL, data=data, timeout=15)
        tok = r.json()
        if "access_token" not in tok:
            raise RuntimeError(f"[{user_id}] 토큰 갱신 실패 (재인증 필요할 수 있음): {tok}")
        self.store.put(user_id, tok)
        log.info("[%s] 카카오 액세스 토큰 갱신%s", user_id, " (+리프레시 토큰 회전)" if tok.get("refresh_token") else "")
        return tok["access_token"]

    def access_token(self, user_id: str) -> str:
        cur = self.store.get(user_id)
        if cur and cur.get("access_token") and cur.get("access_expires_at", 0) > time.time():
            return cur["access_token"]
        return self.refresh(user_id)

    # ---- 메시지
    def send_text(self, user_id: str, text: str, url: str, button_title: str = "전체 뉴스 보기") -> dict:
        if len(text) > 200:
            raise ValueError(f"카카오 텍스트 템플릿은 200자 제한입니다 ({len(text)}자)")
        template = {
            "object_type": "text",
            "text": text,
            "link": {"web_url": url, "mobile_web_url": url},
            "button_title": button_title,
        }
        for attempt in (1, 2):
            token = self.access_token(user_id)
            r = requests.post(MEMO_URL, headers={"Authorization": f"Bearer {token}"},
                              data={"template_object": json.dumps(template, ensure_ascii=False)}, timeout=15)
            body = r.json() if r.content else {}
            if r.status_code == 200 and body.get("result_code") == 0:
                log.info("[%s] 카카오톡 발송 완료", user_id)
                return body
            if r.status_code == 401 and attempt == 1:
                log.info("[%s] 401 → 토큰 갱신 후 재시도", user_id)
                self.refresh(user_id)
                continue
            raise RuntimeError(f"[{user_id}] 카카오 발송 실패 {r.status_code}: {body}")
        return {}
