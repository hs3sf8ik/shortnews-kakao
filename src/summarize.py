"""Claude 2단계 호출: ① 사안 선별(제목·리드만) → ② 선별 기사 본문으로 숏뉴스 문체 작성."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess

import anthropic

from .common import PROMPTS_DIR, header_date, log

CATEGORIES = ["정치", "경제", "사회", "국제", "스포츠"]

# 1M 토큰당 USD (입력, 출력) — 비용 추정 로그용
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "top": {"type": "string", "description": "톱뉴스로 쓸 cluster_id"},
        "main": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                },
                "required": ["cluster_id", "category"],
                "additionalProperties": False,
            },
        },
        "interest": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "cluster_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["user_id", "cluster_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["top", "main", "interest"],
    "additionalProperties": False,
}

WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "header": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": ["톱뉴스", "날씨"] + CATEGORIES},
                    "text": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["category", "text", "source_ids"],
                "additionalProperties": False,
            },
        },
        "interest": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "user_id": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "keyword": {"type": "string"},
                                "text": {"type": "string"},
                                "source_ids": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["keyword", "text", "source_ids"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["user_id", "items"],
                "additionalProperties": False,
            },
        },
        "quote": {"type": "string"},
    },
    "required": ["header", "items", "interest", "quote"],
    "additionalProperties": False,
}

SELECT_SYSTEM = """당신은 한국 일간 뉴스 브리핑 「간추린 숏뉴스」의 데스크입니다.
입력된 사안 목록(제목·리드·보도 매체 수)만 보고, 오늘 브리핑에 실을 사안을 고릅니다.

선별 규칙
- 총 21~23개를 고르고 카테고리를 붙입니다: 정치 5~8, 경제 4~6, 사회 5~8, 국제 1~6, 스포츠 0~1 (기록성 이슈만).
- 그중 파급력이 가장 큰 하나를 top 으로 지정합니다 (main 에도 포함).
- 우선순위: ① 국정·정부 인사·국회 ② 시장 지표·경제 정책 ③ 사법·사건·사고·재난 ④ 한국에 영향이 큰 국제 정세.
- 여러 매체가 함께 보도한 사안(매체 수가 많은 것)을 우선하되, 단독 보도라도 중대하면 포함합니다.
- 같은 사안이 여러 클러스터로 쪼개져 있으면 하나만 고릅니다. 연예·가십·단순 홍보·지역 단신·기획/해설·인터뷰는 제외합니다.
- 뉴욕증시·국제유가처럼 국내 시장과 직결된 해외 경제 뉴스는 '국제' 카테고리로 분류합니다.

관심 뉴스
- 유저별 관심 키워드와 후보 사안이 주어지면, main 에 포함되지 않은 후보 중 키워드와 실질적으로 관련된 것을 최대 2개 고릅니다. 관련성이 낮으면 0개로 둡니다.

반드시 입력에 있는 cluster_id 만 사용합니다."""


def _load_style_prompt() -> str:
    return (PROMPTS_DIR / "shortnews_style.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- 입력 포맷

def _fmt_select_input(crawl: dict, users: list[dict], settings: dict) -> str:
    lines = [f"[사안 목록] 기준일 {crawl['run_date']} / 총 {len(crawl['clusters'])}개",
             "형식: cluster_id | 분류 | 매체(기사수) | 제목 | 리드", ""]
    for c in crawl["clusters"]:
        a0 = c["articles"][0]
        lead = (a0["lead"] or a0["body"][:160]).strip()
        lead = lead[:120]
        outlets = ",".join(c["outlets"])
        lines.append(f"{c['id']} | {c['section'] or '?'} | {outlets}({len(c['articles'])}) | {a0['title']} | {lead}")
    lines.append("")
    lines.append("[유저 관심 키워드와 후보 사안]")
    n_cand = settings.get("interest_candidates_per_user", 6)
    for u in users:
        cands = keyword_candidates(crawl, u["keywords"], n_cand)
        if not u["keywords"]:
            continue
        ids = ", ".join(f"{cid}({kw})" for cid, kw in cands) or "(후보 없음)"
        lines.append(f"user_id={u['id']} 키워드={u['keywords']} 후보={ids}")
    return "\n".join(lines)


def keyword_candidates(crawl: dict, keywords: list[str], limit: int) -> list[tuple[str, str]]:
    """제목·리드·본문에 키워드(공백 무시)가 포함된 클러스터 → (cluster_id, keyword)."""
    out: list[tuple[str, str]] = []
    if not keywords:
        return out
    kws = [(k, re.sub(r"\s+", "", k).lower()) for k in keywords]
    for c in crawl["clusters"]:
        hay = " ".join(f"{a['title']} {a['lead']} {a['body']}" for a in c["articles"])
        hay_n = re.sub(r"\s+", "", hay).lower()
        for k, kn in kws:
            if kn and kn in hay_n:
                out.append((c["id"], k))
                break
        if len(out) >= limit:
            break
    return out


def _fmt_write_input(crawl: dict, sel: dict, users: list[dict], run_date: dt.date, settings: dict) -> str:
    by_id = {c["id"]: c for c in crawl["clusters"]}
    max_body = settings.get("max_body_chars", 1500)
    main_ids = [m["cluster_id"] for m in sel["main"]]
    cat_of = {m["cluster_id"]: m["category"] for m in sel["main"]}
    if sel["top"] in cat_of:
        cat_of[sel["top"]] = "톱뉴스"
    order = {"톱뉴스": 0, "정치": 1, "경제": 2, "사회": 3, "국제": 4, "스포츠": 5}
    main_ids.sort(key=lambda cid: order.get(cat_of.get(cid, ""), 9))

    def fmt_cluster(cid: str, label: str) -> list[str]:
        c = by_id.get(cid)
        if not c:
            return []
        ls = [f"### {cid} [{label}] {c['articles'][0]['title']}"]
        for i, a in enumerate(c["articles"][:2]):
            body = a["body"] or a["lead"]
            body = body[: max_body if i == 0 else 350]
            ls.append(f"- 기사ID {a['id']} / {a['outlet']} / {a['published'][:16]} / {a['title']}")
            ls.append(f"  {body}")
        ls.append("")
        return ls

    lines = [f"[작성 기준일] {header_date(run_date)} (헤더: '{header_date(run_date)} 간추린 숏뉴스입니다.')", "",
             "[본문 사안] 아래 순서대로 각 1개 항목씩 작성 (톱뉴스 1 + 나머지). 카테고리 라벨은 대괄호 안 값을 그대로 사용.", ""]
    for cid in main_ids:
        lines += fmt_cluster(cid, cat_of.get(cid, "정치"))

    lines += ["[날씨 기사] 이 중 최신 정보로 (날씨) 항목 1개 작성. 기사가 없으면 날씨 항목은 생략.", ""]
    for a in crawl.get("weather", []):
        lines.append(f"- 기사ID {a['id']} / {a['outlet']} / {a['published'][:16]} / {a['title']}")
        lines.append(f"  {(a['body'] or a['lead'])[:900]}")
    lines.append("")

    lines += ["[관심 뉴스] 유저별로 아래 후보 중 최대 2개를 같은 문체로 작성. 본문 사안과 중복되는 내용은 제외. 후보가 없거나 관련성이 낮으면 items 를 빈 배열로.", ""]
    for entry in sel.get("interest", []):
        u = next((x for x in users if x["id"] == entry["user_id"]), None)
        if not u:
            continue
        lines.append(f"## user_id={u['id']} 키워드={u['keywords']}")
        for cid in entry["cluster_ids"][:3]:
            kw = next((k for c, k in keyword_candidates(crawl, u["keywords"], 50) if c == cid), u["keywords"][0])
            lines += fmt_cluster(cid, f"관심·{kw}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- LLM 호출

class LLM:
    def __init__(self, cfg: dict):
        self.backend = cfg.get("backend", "api")
        self.model = cfg.get("model", "claude-opus-5")
        self.max_tokens = int(cfg.get("max_tokens", 12000))
        self.usage = {"input": 0, "output": 0}
        self.client = anthropic.Anthropic() if self.backend == "api" else None

    def call_json(self, system: str, user: str, schema: dict, effort: str) -> dict:
        if self.backend == "api":
            return self._call_api(system, user, schema, effort)
        return self._call_cli(system, user, schema)

    def _call_api(self, system: str, user: str, schema: dict, effort: str) -> dict:
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            raise RuntimeError(f"모델이 응답을 거부했습니다: {getattr(msg, 'stop_details', None)}")
        if msg.stop_reason == "max_tokens":
            raise RuntimeError("max_tokens 초과 — settings.llm.max_tokens 를 늘리세요")
        self.usage["input"] += msg.usage.input_tokens
        self.usage["output"] += msg.usage.output_tokens
        log.info("LLM %s effort=%s in=%d out=%d", self.model, effort, msg.usage.input_tokens, msg.usage.output_tokens)
        text = next(b.text for b in msg.content if b.type == "text")
        return json.loads(text)

    def _call_cli(self, system: str, user: str, schema: dict) -> dict:
        """API 키가 없을 때: 이 PC 의 Claude Code 구독(claude -p)으로 실행. GitHub Actions 에서는 사용 불가."""
        prompt = (f"{system}\n\n---\n\n{user}\n\n---\n아래 JSON 스키마에 맞는 JSON 본문만 출력하세요. 코드펜스·설명·도구 사용 금지.\n"
                  f"{json.dumps(schema, ensure_ascii=False)}")
        model_alias = "opus" if "opus" in self.model else ("haiku" if "haiku" in self.model else "sonnet")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}  # 중첩 세션 경고 방지
        res = subprocess.run(
            ["claude", "-p", "--model", model_alias, "--output-format", "text"],
            input=prompt, capture_output=True, text=True, encoding="utf-8", shell=True, timeout=900, env=env,
        )
        if res.returncode != 0:
            raise RuntimeError(f"claude CLI 실패: {res.stderr[:500]}")
        out = res.stdout.strip()
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            raise RuntimeError(f"claude CLI 출력에서 JSON 을 찾지 못했습니다: {out[:300]}")
        return json.loads(m.group(0))

    def cost_usd(self) -> float:
        pin, pout = PRICES.get(self.model, (5.0, 25.0))
        return self.usage["input"] / 1e6 * pin + self.usage["output"] / 1e6 * pout


# --------------------------------------------------------------------------- 파이프라인

def _validate_selection(sel: dict, crawl: dict) -> dict:
    ids = {c["id"] for c in crawl["clusters"]}
    main = [m for m in sel["main"] if m["cluster_id"] in ids]
    seen = set()
    dedup = []
    for m in main:
        if m["cluster_id"] not in seen:
            seen.add(m["cluster_id"])
            dedup.append(m)
    sel["main"] = dedup
    if sel["top"] not in seen and dedup:
        sel["top"] = dedup[0]["cluster_id"]
    main_ids = {m["cluster_id"] for m in dedup}
    for e in sel.get("interest", []):
        e["cluster_ids"] = [c for c in e["cluster_ids"] if c in ids and c not in main_ids][:2]
    return sel


def _validate_digest(digest: dict, crawl: dict) -> dict:
    valid_ids = {a["id"] for c in crawl["clusters"] for a in c["articles"]} | {a["id"] for a in crawl.get("weather", [])}
    for it in digest["items"]:
        it["text"] = it["text"].strip()
        it["source_ids"] = [s for s in it["source_ids"] if s in valid_ids]
    for e in digest.get("interest", []):
        e["items"] = e["items"][:2]
        for it in e["items"]:
            it["text"] = it["text"].strip()
            it["source_ids"] = [s for s in it["source_ids"] if s in valid_ids]
    # 날씨는 마지막, 톱뉴스는 처음으로 정렬 보정
    order = {"톱뉴스": 0, "정치": 1, "경제": 2, "사회": 3, "국제": 4, "스포츠": 5, "날씨": 6}
    digest["items"].sort(key=lambda it: order.get(it["category"], 3))
    return digest


MAX_ITEM_CHARS = 180

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"idx": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["idx", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

REPAIR_SYSTEM = """아래 뉴스 항목들을 같은 합쇼체·정확히 두 문장으로 유지하면서 공백 포함 150자 이내로 압축하세요.
사실·수치·인명은 바꾸거나 추가하지 말고, 수식어와 부연 절만 줄입니다. idx 는 그대로 돌려줍니다."""


def _repair_lengths(llm: "LLM", digest: dict) -> None:
    """180자를 넘는 항목만 골라 한 번의 저비용 호출로 압축한다."""
    targets = [(i, it) for i, it in enumerate(digest["items"]) if len(it["text"]) > MAX_ITEM_CHARS]
    for e in digest.get("interest", []):
        for j, it in enumerate(e["items"]):
            if len(it["text"]) > MAX_ITEM_CHARS:
                targets.append((1000 + len(targets), it))
    if not targets:
        return
    log.info("길이 초과 항목 %d개 압축", len(targets))
    user = "\n\n".join(f"idx={k}\n{it['text']}" for k, (_, it) in enumerate(targets))
    try:
        res = llm.call_json(REPAIR_SYSTEM, user, REPAIR_SCHEMA, effort="low")
    except Exception as e:  # 압축 실패는 치명적이지 않음
        log.warning("압축 호출 실패, 원문 유지: %s", e)
        return
    for r in res.get("items", []):
        k = r.get("idx")
        if isinstance(k, int) and 0 <= k < len(targets) and 40 < len(r["text"]) <= 200:
            targets[k][1]["text"] = r["text"].strip()


def summarize(crawl: dict, users: list[dict], settings: dict, run_date: dt.date) -> dict:
    llm = LLM(settings["llm"])

    # ① 선별 — 제목·리드만 보여 토큰 절약
    sel_in = _fmt_select_input(crawl, users, settings)
    log.info("1단계 선별 입력 %d자", len(sel_in))
    sel = llm.call_json(SELECT_SYSTEM, sel_in, SELECT_SCHEMA, effort="low")
    sel = _validate_selection(sel, crawl)
    log.info("선별 결과: 본문 %d개, 톱=%s, 관심=%s", len(sel["main"]), sel["top"],
             {e["user_id"]: e["cluster_ids"] for e in sel["interest"]})

    # ② 작성 — 선별된 사안만 본문 포함
    style = _load_style_prompt()
    write_in = _fmt_write_input(crawl, sel, users, run_date, settings)
    log.info("2단계 작성 입력 %d자", len(write_in))
    digest = llm.call_json(style, write_in, WRITE_SCHEMA, effort="medium")
    digest = _validate_digest(digest, crawl)
    _repair_lengths(llm, digest)
    lens = [len(it["text"]) for it in digest["items"]]
    log.info("항목 %d개, 길이 중앙값 %d자, 최대 %d자", len(lens), sorted(lens)[len(lens) // 2], max(lens))
    digest["header"] = f"{header_date(run_date)} 간추린 숏뉴스입니다."
    digest["selection"] = sel
    digest["usage"] = {**llm.usage, "model": llm.model, "est_cost_usd": round(llm.cost_usd(), 4)}
    log.info("LLM 사용량 in=%d out=%d ≈ $%.3f", llm.usage["input"], llm.usage["output"], llm.cost_usd())
    return digest
