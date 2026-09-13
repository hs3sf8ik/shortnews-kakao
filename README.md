# 짧은 뉴스 → 카카오톡 자동 브리핑

매일 07:30(KST) 주요 언론 RSS를 수집하고, Claude가 짧은 뉴스 형식 문체(항목당 두 문장·합쇼체)로 22~24개 항목을 작성해
GitHub Pages에 전문을 게시한 뒤, 유저별 카카오톡('나에게 보내기')으로 요약 + [전체 보기] 버튼을 발송합니다.
유저마다 관심 키워드를 두면 관련 뉴스를 최대 2개 추가합니다.

```
RSS 16개 피드 ─▶ 사안 클러스터링 ─▶ ① Claude 선별(제목·리드만) ─▶ ② Claude 작성(선별 본문만)
                                                                     │
      shortnews.co.kr 경제지표 ────────────────────────────────────────┤
                                                                     ▼
                                 docs/{date}/{user}.html (GitHub Pages) ─▶ 카카오톡 200자 요약 + 링크
```

## 폴더

| 경로 | 역할 |
|---|---|
| `prompts/short_news_style.md` | **문체 프롬프트** (예시 파일 분석 결과 반영, 2단계 작성 시 시스템 프롬프트) |
| `config/settings.json` | 피드 목록, 토큰 절약 한도, 모델, 페이지 URL, 발송 옵션 |
| `config/users.json` | 유저 목록 — `id`, `name`, `keywords`(관심 키워드), `kakao` |
| `src/crawl.py` | RSS 수집 → 본문 추출(trafilatura) → 제목 유사도 클러스터링 → 경제지표 수집 |
| `src/summarize.py` | Claude 2단계 호출(선별 → 작성) + 길이 초과 항목 압축, JSON 스키마 강제 |
| `src/render.py` | 전문 텍스트 / HTML / 카카오 200자 메시지 |
| `src/kakao.py` | 카카오 OAuth 토큰 저장·갱신(평문 또는 Fernet 암호화), 나에게 보내기 |
| `src/publish.py` | `docs/` 게시, (로컬 실행 시) GitHub Contents API 업로드 |
| `src/main.py` | 진입점 `python -m src.main` |
| `scripts/kakao_auth.py` | 유저별 카카오 1회 인증 |
| `scripts/push_to_github.py` | git 없이 저장소에 파일 업로드 |
| `scripts/run_daily.ps1` | Windows 작업 스케줄러 래퍼(PC에서 돌릴 때) |
| `.github/workflows/daily.yml` | GitHub Actions — 매일 22:30 UTC(07:30 KST) 실행 후 docs·토큰 커밋 |

## 실행 방식 (현재 설정: PC 방식)

| | PC 방식 (현재) | GitHub Actions 방식 |
|---|---|---|
| LLM | 이 PC 의 Claude Code 구독 (`llm.backend: claude-cli`, 추가 비용 0) | Claude API 크레딧 (`llm.backend: api`, 월 $5~13) |
| 스케줄 | 작업 스케줄러 `shortnews-kakao` 07:30 (`scripts/register_task.ps1`) | `.github/workflows/daily.yml` 22:30 UTC |
| 전문 페이지 | `publish.github_upload: true` → GitHub Contents API 로 docs/ 업로드 (`GITHUB_TOKEN`) | 워크플로가 docs/ 커밋 |
| 카카오 토큰 | `secrets/kakao_tokens.json` (평문, 로컬) | `secrets/kakao_tokens.enc` (Fernet 암호화) |
| 조건 | 07:30 에 PC 켜져 있고 로그인 상태 (놓치면 켜진 직후 실행) | 없음 |

## 1회 설정

### A. Claude API 키 (GitHub Actions 방식만 필요 — PC 방식은 건너뜀)
1. https://console.anthropic.com → API Keys → 키 생성
2. `secrets/.env.example` 을 `secrets/.env` 로 복사하고 `ANTHROPIC_API_KEY=` 채움

### B. 카카오디벨로퍼스 앱 (약 5분)
1. https://developers.kakao.com → 내 애플리케이션 → **애플리케이션 추가하기** (앱 이름·회사명 자유)
2. 앱 설정 → **앱 키** → `REST API 키` 복사 → `.env` 의 `KAKAO_REST_API_KEY=`
3. 제품 설정 → **카카오 로그인** → 활성화 ON → **Redirect URI** 에 `http://localhost:5000/oauth` 등록
4. 카카오 로그인 → **동의항목** → "카카오톡 메시지 전송(talk_message)" → **선택 동의** 로 설정
   (목록에 없으면 앱 설정 → 플랫폼/비즈니스 → *비즈 앱 전환* 후 표시됨. 개인 개발자도 전환 가능)
5. (선택) 카카오 로그인 → 보안 → Client Secret 발급 시 `.env` 의 `KAKAO_CLIENT_SECRET=`
6. 인증:
   ```
   python scripts/kakao_auth.py --user kdu          # 브라우저 → 카카오 로그인 → 동의
   python scripts/kakao_auth.py --user kdu --test   # '나와의 채팅'에 테스트 메시지
   ```
   다른 사람을 추가할 때는 `config/users.json` 에 항목을 넣고, 그 사람이 **자기 카카오 계정으로** 같은 인증을 한 번 합니다
   (`--manual` 옵션: 링크를 보내주고 로그인 후 이동한 URL 을 받아 붙여넣기). 앱이 비즈 앱이 아니면 팀원으로 등록된 계정만 인증 가능합니다.

### C. GitHub Pages (전문 페이지 호스팅 — 두 방식 모두 필요)
1. GitHub 에서 **Public** 저장소 `shortnews-kakao` 생성 (Pages 무료는 Public 필요)
2. Fine-grained PAT 발급 (Settings → Developer settings → Personal access tokens → Fine-grained,
   Repository access: 이 저장소만, Permissions: **Contents: Read and write**) → `.env` 의 `GITHUB_TOKEN=`
3. `config/settings.json` 의 `publish.page_base_url` 을 `https://<ID>.github.io/shortnews-kakao`,
   `publish.github_repo` 를 `<ID>/shortnews-kakao` 로 수정
4. 파일 올리기: `python scripts/push_to_github.py --repo <ID>/shortnews-kakao` (git 이 있으면 `git push` 로도 가능)
5. 저장소 **Settings → Pages** → Source: *Deploy from a branch*, Branch: `main` / `/docs`
   → PC 방식은 여기까지. 매일 실행 시 `docs/{date}/*.html` 이 자동 업로드되고 1~2분 뒤 페이지에 반영됩니다.

### D. GitHub Actions 방식으로 바꿀 때만
5. 토큰 암호화 키 생성 후 로컬 토큰을 암호화 파일로 변환:
   ```
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"   # → KAKAO_TOKENS_KEY
   # .env 에 KAKAO_TOKENS_KEY 추가 후:
   python -c "from src.common import load_env; load_env(); from src.kakao import TokenStore; import json; s=TokenStore(); s.data=json.load(open('secrets/kakao_tokens.json',encoding='utf-8')); s.save(); print('secrets/kakao_tokens.enc 생성')"
   ```
   `secrets/kakao_tokens.enc` 를 저장소에 올립니다 (암호화되어 있어 공개 저장소에 있어도 안전).
6. 저장소 **Settings → Secrets and variables → Actions** 에 등록:
   `ANTHROPIC_API_KEY`, `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`(없으면 빈 값), `KAKAO_TOKENS_KEY`
7. **Actions** 탭 → `daily-shortnews` → *Run workflow* (dry_run 체크) 로 1회 테스트 → 이후 매일 07:30 자동 실행
8. `settings.json` 에서 `llm.backend: "api"`, `publish.github_upload: false` 로 바꾸고 작업 스케줄러 작업은 비활성화

### E. 작업 스케줄러 (PC 방식)
```
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1            # 07:30 등록/갱신 (이미 등록됨)
powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Time 08:00 # 시간 변경
Start-ScheduledTask -TaskName shortnews-kakao                                 # 지금 즉시 1회 실행
Get-Content logs\run-<날짜>.log -Tail 40                                       # 실행 로그
```
작업은 로그인한 상태에서만 실행됩니다(화면 잠금은 OK). 07:30 에 PC 가 꺼져 있었다면 다음 부팅·로그인 직후 실행됩니다.

## 실행

```
python -m src.main                    # 전체 실행 (수집→요약→게시→발송)
python -m src.main --dry-run          # 발송만 생략, 카카오 미리보기 출력
python -m src.main --skip-llm         # 크롤링 점검
python -m src.main --use-cache        # 오늘 data/ 캐시 재사용 (렌더/발송만 다시)
python -m src.main --backend claude-cli   # API 키 없이 이 PC 의 Claude Code 구독으로 (Actions 불가)
```

결과: `data/{date}/crawl.json`(수집), `digest.json`(요약+사용 토큰), `digest.txt`(전문), `docs/{date}/*.html`

## 토큰·비용

- 1단계 선별 입력 약 2만 자(제목·리드만), 2단계 작성 입력 약 3.5만 자(선별 24건 본문) → 하루 약 4~5만 입력 토큰 + 6천 출력 토큰
- `claude-opus-5` 기준 하루 약 $0.4~0.5, `claude-sonnet-5` 로 바꾸면 약 $0.15~0.2 (`settings.llm.model`)
- 줄이려면 `max_clusters_for_selection`(130), `max_body_chars`(1200) 를 낮추면 됩니다

## 카카오 발송 방식 (`settings.kakao.mode`)

| mode | 동작 | 용도 |
|---|---|---|
| `text` (현재) | 본문 전체를 말풍선 여러 개로: 1번 = 날짜+톱뉴스, 이후 항목마다 1개, 관심 뉴스, 마지막 경제지표 | 카톡에서 바로 읽기·**음성 듣기** |
| `link` | 200자 요약 1건 + [전체 뉴스 보기] 버튼 → GitHub Pages 전문 | 하루 1건만 받고 싶을 때 |

카카오 API 제약: 텍스트 템플릿 **200자/건**, 발신·수신 쌍당 **하루 20건**(-536). 그래서 `text` 모드는 항목 수를
`digest.counts`(기본 톱1+정치4+경제4+사회3+국제2+날씨1 = 15) + 관심 2 + 지표 1 = 최대 18건으로 맞추고, 항목당 170자 상한을
프롬프트·압축 단계에서 강제합니다. 한도 초과 시 남은 건은 중단하고 로그에 남깁니다. 명언 블록은 넣지 않습니다.
링크 버튼이 필요하면 `kakao.link_button: true` — 이때 링크 도메인은 카카오 앱 [제품 링크 관리]에 등록돼 있어야 버튼이 표시됩니다.
리프레시 토큰은 2개월 유효, 매일 실행 시 자동 회전(`secrets/kakao_tokens.json`).

## 휴면(보안 정리) / 재가동

**휴면 — 남는 민감 정보를 없애는 순서**
1. 예약 작업 중지: `Disable-ScheduledTask shortnews-kakao` (완전 삭제는 `Unregister-ScheduledTask shortnews-kakao -Confirm:$false`)
2. GitHub PAT 삭제: https://github.com/settings/personal-access-tokens → `shortnews` 토큰 Delete
3. 카카오 연결 해제: 카카오계정 → 연결된 서비스 관리 → 앱 → 연결 끊기 (리프레시 토큰 무효화). 앱 자체를 지우려면 카카오디벨로퍼스 → 앱 설정 → 앱 삭제
4. 로컬 비밀 파일 삭제: `secrets\.env`, `secrets\kakao_tokens.json` (템플릿 `.env.example` 은 유지)
5. (선택) `data\`, `logs\` 삭제 — 뉴스 캐시·실행 로그, 민감 정보 없음
- GitHub 저장소·Pages 는 비밀이 없어 그대로 둬도 됨 (`.gitignore` 로 secrets/·data/ 제외, 업로드 스크립트도 동일 규칙)

**재가동**
1. `secrets\.env.example` → `secrets\.env` 복사 후 `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET`, 새 `GITHUB_TOKEN` 입력 (카카오 앱을 지웠다면 README B 절대로 재생성 + [제품 링크 관리]에 `https://hs3sf8ik.github.io` 등록)
2. `python scripts\kakao_auth.py --user kdu` (브라우저 로그인 1회) → `--test` 로 확인
3. `python -m src.main --dry-run` 으로 미리보기 → `Enable-ScheduledTask shortnews-kakao`

## 관심 키워드

`config/users.json` 의 `keywords` 는 제목·리드·본문 **부분 일치**(공백 무시)로 후보를 찾고, Claude 가 관련성 높은 것 최대 2개를 고릅니다.
좁은 고유명사(예: `금융범죄수사대`)만 두면 매칭이 드물 수 있으니 `금융범죄`, `금수대`, `보이스피싱` 처럼 변형·연관어를 함께 넣는 것을 권합니다.
