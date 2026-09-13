"""다이제스트 JSON → 텍스트 / HTML / 카카오 200자 메시지."""
from __future__ import annotations

import datetime as dt
import html
import re

from .common import short_date

SITE_NAME = "짧은 뉴스"


def _interest_items(digest: dict, user_id: str | None) -> list[dict]:
    if not user_id:
        return []
    for e in digest.get("interest", []):
        if e["user_id"] == user_id:
            return e["items"]
    return []


def render_text(digest: dict, extras: dict, user: dict | None = None, page_url: str | None = None) -> str:
    """예시 파일과 같은 형식의 전문 텍스트 (텔레그램·HTML 본문 공용)."""
    lines = [digest["header"], ""]
    for it in digest["items"]:
        lines.append(f"■ ({it['category']}) {it['text']}")
        lines.append("")
    interest = _interest_items(digest, user["id"] if user else None)
    if interest:
        lines.append(f"[관심 뉴스 · {', '.join(user['keywords'])}]")
        for it in interest:
            lines.append(f"■ (관심·{it['keyword']}) {it['text']}")
            lines.append("")
    if page_url:
        lines += [f"[{SITE_NAME}]", page_url, ""]
    if extras.get("indicators"):
        lines.append("[주요 경제 지표]")
        for ind in extras["indicators"]:
            lines.append(f"  - {ind['name']} : {ind['value']}")
    return "\n".join(lines).rstrip() + "\n"


def category_counts(digest: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in digest["items"]:
        if it["category"] in ("톱뉴스", "날씨"):
            continue
        counts[it["category"]] = counts.get(it["category"], 0) + 1
    return counts


def _first_sentence(text: str) -> str:
    m = re.match(r"(.+?다\.)(\s|$)", text)
    return m.group(1) if m else text


def _fit(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def render_kakao_chunks(digest: dict, extras: dict, run_date: dt.date, user: dict | None,
                        max_chars: int = 200, max_messages: int = 18, include_indicators: bool = True) -> list[str]:
    """본문 전체를 카카오 말풍선(≤200자) 여러 개로 나눈다 — 음성 듣기용.
    1번: 날짜 헤더 + 톱뉴스, 이후 항목마다 1개, 관심 뉴스, 마지막에 경제지표(선택)."""
    items = list(digest["items"])
    top = next((it for it in items if it["category"] == "톱뉴스"), items[0])
    rest = [it for it in items if it is not top]
    header = f"{short_date(run_date)} {SITE_NAME}\n\n■ (톱뉴스) "
    chunks = [header + _fit(top["text"], max_chars - len(header))]
    for it in rest:
        prefix = f"■ ({it['category']}) "
        chunks.append(prefix + _fit(it["text"], max_chars - len(prefix)))
    for it in _interest_items(digest, user["id"] if user else None):
        prefix = f"■ (관심·{it['keyword']}) "
        chunks.append(prefix + _fit(it["text"], max_chars - len(prefix)))
    if include_indicators and extras.get("indicators"):
        ind = "[주요 경제 지표]\n" + "\n".join(f"{i['name']} {i['value']}" for i in extras["indicators"])
        if len(ind) <= max_chars:
            chunks.append(ind)
    if len(chunks) > max_messages:  # 일일 한도 보호 — 뒤쪽(지표·관심)부터 잘라냄
        chunks = chunks[:max_messages]
    return chunks


def render_kakao_text(digest: dict, run_date: dt.date, user: dict | None, max_chars: int = 200) -> str:
    """카카오 텍스트 템플릿(최대 200자)에 들어갈 요약. 톱뉴스 첫 문장을 최대한 살린다."""
    top = next((it for it in digest["items"] if it["category"] == "톱뉴스"), digest["items"][0])
    n_main = len([it for it in digest["items"] if it["category"] not in ("날씨",)])
    n_int = len(_interest_items(digest, user["id"] if user else None))
    tail = f"\n\n오늘 {n_main}건" + (f" + 관심 {n_int}건" if n_int else "") + " → 아래 버튼으로 전체 보기"
    head = f"{short_date(run_date)} {SITE_NAME}\n\n■ 톱뉴스 "
    budget = max_chars - len(head) - len(tail)
    sent = _first_sentence(top["text"])
    if len(sent) > budget:
        sent = sent[: max(0, budget - 1)].rstrip() + "…"
    return head + sent + tail


# --------------------------------------------------------------------------- HTML

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1a1a1a;--muted:#667085;--brand:#1E2A5A;--acc:#FEE500}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1b1f27;--fg:#e8eaf0;--muted:#98a2b3;--brand:#8ea2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.65 -apple-system,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}
header{background:var(--brand);color:#fff;padding:18px 16px}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;font-size:13px;opacity:.85}
main{max-width:720px;margin:0 auto;padding:16px}
article{background:var(--card);border-radius:12px;padding:16px 18px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.item{margin:0 0 14px}.item:last-child{margin-bottom:0}.cat{font-weight:700;color:var(--brand)}
.item.top .cat{background:var(--acc);color:#3C1E1E;padding:0 6px;border-radius:4px}
.item.interest .cat{color:#b54708}
h2{font-size:15px;color:var(--muted);margin:22px 0 8px;font-weight:600}
.src{font-size:12px;color:var(--muted);margin-top:4px}.src a{color:inherit}
table{width:100%;border-collapse:collapse;font-size:15px}td{padding:6px 4px;border-bottom:1px solid rgba(0,0,0,.06)}td:last-child{text-align:right;font-variant-numeric:tabular-nums}
footer{color:var(--muted);font-size:12px;text-align:center;padding:24px 16px}
nav a{color:var(--muted);font-size:13px;margin-right:12px}
"""


def _item_html(it: dict, articles_by_id: dict, cls: str, label: str) -> str:
    srcs, seen = [], set()
    for s in it.get("source_ids", []):
        a = articles_by_id.get(s)
        if a and a["outlet"] not in seen:  # 같은 매체 중복 표기 방지
            seen.add(a["outlet"])
            srcs.append(a)
    src_html = ""
    if srcs:
        links = " · ".join(f'<a href="{html.escape(a["url"])}" target="_blank" rel="noopener">{html.escape(a["outlet"])}</a>' for a in srcs[:2])
        src_html = f'<div class="src">출처: {links}</div>'
    return (f'<p class="item {cls}"><span class="cat">■ ({html.escape(label)})</span> '
            f'{html.escape(it["text"])}{src_html}</p>')


def render_html(digest: dict, extras: dict, crawl: dict, run_date: dt.date, user: dict | None,
                index_url: str = "index.html") -> str:
    articles_by_id = {a["id"]: a for c in crawl["clusters"] for a in c["articles"]}
    articles_by_id.update({a["id"]: a for a in crawl.get("weather", [])})
    title = f"{digest['header'].replace(f' {SITE_NAME}입니다.', '')} {SITE_NAME}"
    parts = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
             f"<title>{html.escape(title)}</title><style>{_CSS}</style></head><body>",
             f"<header><h1>📰 {html.escape(title)}</h1><p>오늘의 주요 뉴스를 두 문장씩 · 자동 생성 브리핑</p></header><main>"]
    parts.append(f"<nav><a href='{index_url}'>← 지난 브리핑</a></nav>")
    parts.append("<article>")
    for it in digest["items"]:
        cls = "top" if it["category"] == "톱뉴스" else ""
        parts.append(_item_html(it, articles_by_id, cls, it["category"]))
    parts.append("</article>")

    interest = _interest_items(digest, user["id"] if user else None)
    if user and user.get("keywords"):
        parts.append(f"<h2>관심 뉴스 · {html.escape(', '.join(user['keywords']))}</h2><article>")
        if interest:
            for it in interest:
                parts.append(_item_html(it, articles_by_id, "interest", f"관심·{it['keyword']}"))
        else:
            parts.append("<p class='item'><span class='cat'>—</span> 오늘은 관심 키워드에 해당하는 뉴스가 없었습니다.</p>")
        parts.append("</article>")

    if extras.get("indicators"):
        parts.append("<h2>주요 경제 지표</h2><article><table>")
        for ind in extras["indicators"]:
            parts.append(f"<tr><td>{html.escape(ind['name'])}</td><td>{html.escape(ind['value'])}</td></tr>")
        parts.append("</table></article>")
    gen = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts.append(f"</main><footer>연합뉴스·SBS·뉴시스·국민일보·경향신문·연합뉴스TV 공개 RSS를 바탕으로 Claude가 요약한 브리핑입니다. "
                 f"각 항목의 출처 링크에서 원문을 확인하세요. 생성 {gen}</footer></body></html>")
    return "".join(parts)


def render_index(entries: list[dict]) -> str:
    """docs/index.html — 날짜별 링크 목록. entries: [{date, users:[{id,name,path}]}] 최신순."""
    rows = []
    for e in entries:
        links = " · ".join(f"<a href='{html.escape(u['path'])}'>{html.escape(u['name'])}</a>" for u in e["users"])
        rows.append(f"<tr><td>{e['date']}</td><td>{links}</td></tr>")
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{SITE_NAME} 아카이브</title><style>{_CSS}</style></head><body>"
            f"<header><h1>📰 {SITE_NAME} 아카이브</h1><p>날짜를 눌러 브리핑을 확인하세요</p></header>"
            f"<main><article><table>{''.join(rows)}</table></article></main></body></html>")
