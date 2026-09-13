"""git 없이 프로젝트 파일을 GitHub 저장소에 올린다 (Contents API, 파일당 1커밋).

  python scripts/push_to_github.py --repo <GITHUB_ID>/shortnews-kakao [--branch main] [--dry-run]

필요: secrets/.env 의 GITHUB_TOKEN (Fine-grained PAT: 해당 repo 의 Contents: Read and write)
저장소는 미리 만들어 두어야 함 (Public, README 없이 빈 저장소도 OK — 첫 파일이 브랜치를 만든다).
.gitignore 규칙(secrets/, data/, __pycache__)은 여기서도 그대로 적용된다.
"""
from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.common import ROOT, load_env, setup_logging, log  # noqa: E402

API = "https://api.github.com"
# .github/workflows 는 PAT 에 'Workflows' 권한이 없으면 403 → Actions 방식으로 바꿀 때만 권한 추가 후 --with-workflows
SKIP_DIRS = {".git", "__pycache__", "data", ".pytest_cache", "logs", ".github"}
ALLOW_SECRETS = {"secrets/.env.example", "secrets/kakao_tokens.enc"}


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        parts = rel.split("/")
        if any(part in SKIP_DIRS for part in parts[:-1]) or p.suffix == ".pyc":
            continue
        if parts[0] == "secrets" and rel not in ALLOW_SECRETS:
            continue
        yield p, rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--with-workflows", action="store_true", help=".github/ 도 업로드 (PAT 에 Workflows 권한 필요)")
    args = ap.parse_args()
    if args.with_workflows:
        SKIP_DIRS.discard(".github")
    setup_logging()
    load_env()
    import os
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN 이 없습니다 (secrets/.env)")
        return 1
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    r = s.get(f"{API}/repos/{args.repo}", timeout=20)
    if r.status_code != 200:
        print(f"저장소 접근 실패 {r.status_code}: {r.text[:200]}")
        return 1

    files = list(iter_files())
    log.info("%d개 파일 업로드 대상", len(files))
    for p, rel in files:
        if args.dry_run:
            print(" ", rel)
            continue
        url = f"{API}/repos/{args.repo}/contents/{rel}"
        sha = None
        g = s.get(url, params={"ref": args.branch}, timeout=20)
        if g.status_code == 200:
            sha = g.json().get("sha")
            if base64.b64decode(g.json().get("content", "").encode()) == p.read_bytes():
                log.info("변경 없음  %s", rel)
                continue
        body = {"message": f"{'update' if sha else 'add'} {rel}", "branch": args.branch,
                "content": base64.b64encode(p.read_bytes()).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = s.put(url, json=body, timeout=30)
        if r.status_code not in (200, 201):
            print(f"실패 {rel}: {r.status_code} {r.text[:200]}")
            return 1
        log.info("업로드 완료 %s", rel)
    print("\n완료. 이제 GitHub 저장소 Settings → Pages → Source: Deploy from a branch, Branch: main / docs 로 설정하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
