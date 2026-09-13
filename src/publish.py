"""docs/ 에 HTML 게시 (GitHub Pages) + 선택적으로 GitHub Contents API 업로드(로컬 실행 시)."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
from pathlib import Path

import requests

from .common import ROOT, log
from .render import render_index

API = "https://api.github.com"


def page_paths(run_date: dt.date, user_id: str | None) -> str:
    """docs/ 기준 상대 경로. 공용 페이지는 {date}/index.html, 유저 페이지는 {date}/{user}.html"""
    d = run_date.isoformat()
    return f"{d}/{user_id}.html" if user_id else f"{d}/index.html"


def write_pages(docs_dir: Path, run_date: dt.date, pages: dict[str, str], users: list[dict]) -> list[Path]:
    """pages: {상대경로: html}. 인덱스(latest.json, index.html)도 갱신."""
    written = []
    for rel, content in pages.items():
        p = docs_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(p)

    latest_path = docs_dir / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {"entries": []}
    entry = {"date": run_date.isoformat(),
             "users": [{"id": "all", "name": "공용", "path": page_paths(run_date, None)}]
                      + [{"id": u["id"], "name": u.get("name", u["id"]), "path": page_paths(run_date, u["id"])} for u in users]}
    latest["entries"] = [e for e in latest["entries"] if e["date"] != entry["date"]]
    latest["entries"].insert(0, entry)
    latest["entries"] = latest["entries"][:60]
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
    written.append(latest_path)

    idx = docs_dir / "index.html"
    idx.write_text(render_index(latest["entries"]), encoding="utf-8")
    written.append(idx)
    (docs_dir / ".nojekyll").touch()
    log.info("HTML %d개 파일 기록 → %s", len(pages), docs_dir)
    return written


# --------------------------------------------------------------------------- GitHub 업로드 (로컬 PC 실행용)

def github_upload(files: list[Path], repo: str, branch: str, message: str) -> None:
    """git 없이 Contents API 로 파일 업로드/갱신. GITHUB_TOKEN(PAT, repo 권한) 필요."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITHUB_TOKEN 이 없어 업로드할 수 없습니다")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28"})
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        url = f"{API}/repos/{repo}/contents/{rel}"
        sha = None
        r = s.get(url, params={"ref": branch}, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
        body = {"message": f"{message}: {rel}", "branch": branch,
                "content": base64.b64encode(p.read_bytes()).decode("ascii")}
        if sha:
            body["sha"] = sha
        r = s.put(url, json=body, timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"GitHub 업로드 실패 {rel}: {r.status_code} {r.text[:200]}")
        log.info("GitHub 업로드 %s", rel)
