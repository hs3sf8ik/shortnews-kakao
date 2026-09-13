"""RSS 수집 → 본문 추출 → 사안(클러스터) 묶기 → 숏뉴스 경제지표 수집."""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import hashlib
import html
import re
import time
from dataclasses import asdict, dataclass, field

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from .common import KST, log

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
SECTIONS = ["정치", "경제", "사회", "국제", "스포츠"]


@dataclass
class Article:
    id: str
    outlet: str
    section: str | None
    title: str
    url: str
    published: str          # ISO 8601 (KST)
    lead: str               # RSS description (태그 제거)
    body: str = ""          # 본문 (추출 후)

    @property
    def published_dt(self) -> dt.datetime:
        return dt.datetime.fromisoformat(self.published)


@dataclass
class Cluster:
    id: str
    section: str | None
    articles: list[Article]
    outlets: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def title(self) -> str:
        return self.articles[0].title

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- 텍스트 정리

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_BRACKET_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|【[^】]*】|<[^>]*>")
_PUNCT_RE = re.compile(r"[\"'“”‘’…·,.!?~\-–—:;/\\|&*+=#@%^$`_(){}\[\]<>「」『』]")


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(_TAG_RE.sub(" ", s))
    return _WS_RE.sub(" ", s).strip()


# 기사 앞머리 바이라인·사진 캡션·AI 요약 안내문 등 토큰만 잡아먹는 상용구 제거
_BOILERPLATE_RES = [
    re.compile(r"[\(\[［（][^\)\]］）]{1,25}=\s*(연합뉴스|뉴시스|뉴스1|연합뉴스TV)[\)\]］）]\s*[가-힣]{2,4}\s*(특파원|기자|선임기자|논설위원)?\s*=\s*"),
    re.compile(r"[\[\(][^\]\)]{0,60}(제공|재판매 및 DB 금지|자료사진|사진=|연합뉴스 자료)[^\]\)]{0,40}[\]\)]"),
    re.compile(r"전체 내용을 이해하기 위해서는 기사 본문과 함께 읽어야 합니다\.?"),
    re.compile(r"\[[^\]]{0,30}(뉴시스Pic|포토|영상|그래픽)[^\]]{0,10}\]"),
    re.compile(r"(무단\s*전재|재배포\s*금지|AI\s*학습\s*(및\s*)?활용\s*금지)[^.]*\.?"),
    re.compile(r"[가-힣]{2,4}\s*기자\s*[\w.\-]+@[\w.\-]+"),
]


def strip_boilerplate(s: str) -> str:
    for p in _BOILERPLATE_RES:
        s = p.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def normalize_title(t: str) -> str:
    t = _BRACKET_RE.sub(" ", t)
    t = _PUNCT_RE.sub(" ", t)
    return _WS_RE.sub("", t).lower()


def bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def article_id(url: str) -> str:
    return "a" + hashlib.md5(url.encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- RSS 수집

def _entry_datetime(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                return dt.datetime(*tp[:6], tzinfo=dt.timezone.utc).astimezone(KST)
            except Exception:
                pass
    return None


def fetch_feed(feed: dict, window_start: dt.datetime, exclude_res: list[re.Pattern],
               session: requests.Session) -> list[Article]:
    try:
        r = session.get(feed["url"], timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.warning("피드 실패 %s %s: %s", feed["outlet"], feed["url"], e)
        return []
    parsed = feedparser.parse(r.content)
    out: list[Article] = []
    for e in parsed.entries:
        title = clean_text(e.get("title"))
        link = (e.get("link") or "").strip()
        if not title or not link:
            continue
        if any(p.search(title) for p in exclude_res):
            continue
        pub = _entry_datetime(e)
        if pub is None:
            pub = dt.datetime.now(KST)
        if pub < window_start:
            continue
        desc = strip_boilerplate(clean_text(e.get("summary") or e.get("description") or ""))
        # 일부 피드는 description 에 본문 전체가 들어 있음(뉴시스·국민일보)
        body = desc if len(desc) > 600 else ""
        out.append(Article(
            id=article_id(link), outlet=feed["outlet"], section=feed.get("section"),
            title=title, url=link, published=pub.isoformat(), lead=desc[:300], body=body,
        ))
    log.info("피드 %-6s %-4s %3d건 (%s)", feed["outlet"], feed.get("section") or "전체", len(out), feed["url"][:60])
    return out


def fetch_all_feeds(settings: dict, window_start: dt.datetime) -> list[Article]:
    exclude_res = [re.compile(p) for p in settings.get("exclude_title_patterns", [])]
    session = requests.Session()
    session.headers["User-Agent"] = UA
    arts: list[Article] = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(lambda f: fetch_feed(f, window_start, exclude_res, session), settings["feeds"]):
            arts.extend(res)
    # URL 중복 제거
    seen: set[str] = set()
    uniq = []
    for a in arts:
        if a.id in seen:
            continue
        seen.add(a.id)
        uniq.append(a)
    log.info("수집 기사 %d건 (중복 제거 후)", len(uniq))
    return uniq


# --------------------------------------------------------------------------- 클러스터링

def cluster_articles(articles: list[Article], threshold: float = 0.42) -> list[Cluster]:
    """제목 문자 바이그램 자카드 유사도로 같은 사안을 묶는다 (탐욕적)."""
    items = [(a, bigrams(normalize_title(a.title))) for a in articles]
    clusters: list[list[tuple[Article, set]]] = []
    reps: list[set] = []
    for a, bg in items:
        best, best_sim = -1, 0.0
        for i, rep in enumerate(reps):
            sim = jaccard(bg, rep)
            if sim > best_sim:
                best, best_sim = i, sim
        if best >= 0 and best_sim >= threshold:
            clusters[best].append((a, bg))
            reps[best] = reps[best] | bg if len(reps[best]) < 400 else reps[best]
        else:
            clusters.append([(a, bg)])
            reps.append(set(bg))

    now = dt.datetime.now(KST)
    out: list[Cluster] = []
    for i, members in enumerate(clusters):
        arts = [m[0] for m in members]
        # 본문 길이 → 리드 길이 → 최신 순으로 대표 기사 정렬
        arts.sort(key=lambda a: (len(a.body), len(a.lead), a.published), reverse=True)
        outlets = sorted({a.outlet for a in arts})
        secs = [a.section for a in arts if a.section]
        section = max(set(secs), key=secs.count) if secs else None
        newest = max(a.published_dt for a in arts)
        age_h = max(0.0, (now - newest).total_seconds() / 3600)
        score = len(outlets) * 2.0 + min(len(arts), 6) * 0.5 + max(0.0, 2.0 - age_h / 12)
        out.append(Cluster(id=f"c{i:03d}", section=section, articles=arts, outlets=outlets, score=score))
    out.sort(key=lambda c: c.score, reverse=True)
    for i, c in enumerate(out):
        c.id = f"c{i:03d}"
    log.info("사안 클러스터 %d개 (복수 매체 %d개)", len(out), sum(1 for c in out if len(c.outlets) > 1))
    return out


# --------------------------------------------------------------------------- 본문 추출

def extract_body(url: str, session: requests.Session, max_chars: int) -> str:
    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.debug("본문 요청 실패 %s: %s", url, e)
        return ""
    text = trafilatura.extract(r.text, include_comments=False, include_tables=False,
                               favor_precision=True, url=url) or ""
    if len(text) < 200:  # 추출 실패 시 og:description 으로 대체
        try:
            soup = BeautifulSoup(r.text, "html.parser")
            og = soup.find("meta", property="og:description")
            if og and og.get("content"):
                text = clean_text(og["content"])
        except Exception:
            pass
    text = strip_boilerplate(text)
    return text[:max_chars]


def fill_bodies(clusters: list[Cluster], per_cluster: int, workers: int, max_chars: int) -> None:
    session = requests.Session()
    session.headers["User-Agent"] = UA
    targets: list[Article] = []
    for c in clusters:
        for a in c.articles[:per_cluster]:
            if not a.body:
                targets.append(a)
    log.info("본문 추출 대상 %d건", len(targets))
    t0 = time.time()

    def work(a: Article):
        a.body = extract_body(a.url, session, max_chars)
        return a

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, targets))
    ok = sum(1 for a in targets if len(a.body) >= 200)
    log.info("본문 추출 완료 %d/%d건, %.1fs", ok, len(targets), time.time() - t0)
    for c in clusters:
        c.articles.sort(key=lambda a: (len(a.body), len(a.lead)), reverse=True)


# --------------------------------------------------------------------------- 숏뉴스 경제지표

_INDICATOR_RE = re.compile(r"^\s*-\s*(.+?)\s*:\s*(.+?)\s*$", re.M)


def fetch_shortnews_extras(url_tpl: str, date: dt.date) -> dict:
    """shortnews.co.kr 날짜 페이지에서 [주요 경제 지표] 블록만 가져온다 (수치는 사실 정보)."""
    url = url_tpl.format(date=date.isoformat())
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.warning("숏뉴스 지표 페이지 실패: %s", e)
        return {"indicators": [], "source_url": url}
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")
    m = re.search(r"\[주요 경제 지표\](.*?)(?:\n\s*\n\s*\n|\Z)", text, re.S)
    indicators = []
    if m:
        for name, val in _INDICATOR_RE.findall(m.group(1)):
            indicators.append({"name": name.strip(), "value": val.strip()})
    log.info("경제지표 %d개 수집 (%s)", len(indicators), url)
    return {"indicators": indicators, "source_url": url}


# --------------------------------------------------------------------------- 진입점

def is_weather(a: Article) -> bool:
    t = a.title
    if "세계의 날씨" in t or "주간날씨" in t or "주간 날씨" in t:
        return False
    return "날씨" in t or t.startswith("[기상") or "기상특보" in t


def crawl(settings: dict, run_date: dt.date) -> dict:
    now = dt.datetime.now(KST)
    window_start = now - dt.timedelta(hours=settings.get("window_hours", 26))
    articles = fetch_all_feeds(settings, window_start)

    weather = [a for a in articles if is_weather(a)]
    weather.sort(key=lambda a: a.published, reverse=True)
    articles = [a for a in articles if not is_weather(a)]

    clusters = cluster_articles(articles)
    top = clusters[: settings.get("max_clusters_for_selection", 140)]
    fill_bodies(top, settings.get("max_articles_per_cluster", 2),
                settings.get("fetch_workers", 12), settings.get("max_body_chars", 1500))
    # 날씨 기사 본문 (최신 2건)
    weather = weather[:2]
    if weather:
        fill_bodies([Cluster(id="w", section="날씨", articles=weather)], 2,
                    settings.get("fetch_workers", 12), settings.get("max_body_chars", 1500))

    extras = fetch_shortnews_extras(settings["shortnews_indicators_url"], run_date)
    return {
        "run_date": run_date.isoformat(),
        "window_start": window_start.isoformat(),
        "clusters": [c.to_dict() for c in top],
        "weather": [asdict(a) for a in weather],
        "extras": extras,
        "stats": {"articles": len(articles), "clusters": len(clusters), "kept": len(top)},
    }
