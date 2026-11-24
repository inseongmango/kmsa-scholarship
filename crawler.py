#!/usr/bin/env python3
"""의대협 장학 공고 수집기.

sources.yaml에 적힌 곳에서 장학 공고를 모아 docs/index.html(관리자용 한눈에 보기 페이지)과
docs/scholarships.csv를 만든다. 이전 실행 결과는 data/state.json에 쌓여서
처음 발견한 날짜(NEW 표시)와 이미 읽은 상세 정보가 유지된다.

사용법:  python crawler.py            # 수집 + 페이지 생성
         python crawler.py --report   # 수집 없이 페이지만 다시 생성
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib import robotparser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
import yaml
from bs4 import BeautifulSoup

import extract as ex
from report import write_csv, write_html

ROOT = Path(__file__).resolve().parent
KST = dt.timezone(dt.timedelta(hours=9))
log = logging.getLogger("kmsa")


def today_kst() -> dt.date:
    return dt.datetime.now(KST).date()


# ====================================================================== 네트워크
class RobotsBlocked(Exception):
    pass


class Fetcher:
    """robots.txt를 지키고, 같은 사이트에는 간격을 두고 요청한다."""

    def __init__(self, user_agent: str, delay: float = 2.0, timeout: int = 25):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5"})
        self.ua = user_agent
        self.delay = delay
        self.timeout = timeout
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._last: dict[str, float] = {}

    def _wait(self, host: str):
        gap = time.time() - self._last.get(host, 0)
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last[host] = time.time()

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        rp = self._robots.get(base)
        if rp is None:
            rp = robotparser.RobotFileParser()
            try:
                self._wait(p.netloc)
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                if r.status_code in (401, 403):
                    rp.disallow_all = True
                elif r.status_code >= 400:
                    rp.parse([])
                else:
                    rp.parse(r.text.splitlines())
            except requests.RequestException:
                rp.parse([])
            self._robots[base] = rp
        return rp.can_fetch(self.ua, url)

    def get(self, url: str, *, robots: bool = True, **kw) -> requests.Response:
        if robots and not self.allowed(url):
            raise RobotsBlocked(url)
        self._wait(urlparse(url).netloc)
        r = self.s.get(url, timeout=self.timeout, **kw)
        r.raise_for_status()
        if r.encoding is None or r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding
        return r


# ====================================================================== 유틸
def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def canonical_url(url: str) -> str:
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in ("layout", "page", "sca", "sfl", "stx", "sop", "spt", "search", "f_page", "pageindex")]
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", urlencode(q), ""))


def item_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def title_ok(title: str, inc: list[str], exc: list[str]) -> bool:
    return any(k in title for k in inc) and not any(k in title for k in exc)


def page_text(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "form", "iframe"]):
        t.decompose()
    for sel in ("#gnb", "#lnb", ".gnb", ".lnb", "#header", "#footer", ".sitemap", ".quick"):
        for t in soup.select(sel):
            t.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)[:20000]


# ====================================================================== 소스별 수집
def parse_rss(content: bytes, base_url: str) -> list[dict]:
    soup = BeautifulSoup(content, "xml")
    out = []
    for it in soup.find_all(["item", "entry"]):
        title = clean(it.title.get_text() if it.title else "")
        if title.endswith("}") and "{" not in title:  # K2Web RSS 제목 끝에 붙는 '}' 제거
            title = title[:-1].strip()
        link = ""
        if it.link:
            link = it.link.get("href") or it.link.get_text()
        link = urljoin(base_url, clean(link))
        date_tag = it.find(["pubDate", "published", "updated", "date"])
        desc_tag = it.find(["description", "summary", "content"])
        desc = page_text(desc_tag.get_text()) if desc_tag else ""
        if title and link:
            out.append({"title": title, "url": link, "posted": ex.parse_any_date(date_tag.get_text() if date_tag else ""), "desc": desc})
    return out


POSTED_RE = re.compile(r"(?<!\d)((?:20)?\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2})(?!\d)")


def parse_html_list(text: str, base_url: str, link_pattern: str | None = None, keywords: list[str] | None = None) -> list[dict]:
    """게시판 목록에서 글 제목·링크·게시일을 뽑는다.

    link_pattern 이 주어지면 그 패턴에 맞는 링크를 글로 본다. 링크가 자바스크립트로 처리돼
    하나도 찾지 못하면(또는 패턴이 없으면) 표의 각 줄(tr/li) 글자에서 제목을 읽고,
    링크는 그 줄 안의 실제 주소나 게시판 목록 주소로 대신한다.
    """
    soup = BeautifulSoup(text, "lxml")
    out, seen = [], set()
    if link_pattern:
        pat = re.compile(link_pattern)
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not pat.search(href):
                continue
            url = urljoin(base_url, href)
            title = clean(a.get("title") or a.get_text(" "))
            title = re.sub(r"(\s+(새글|NEW|new|N|첨부파일|파일|댓글\s*\d+)|\s*\[\d+\])+\s*$", "", title)
            if len(title) < 4 or url in seen:
                continue
            seen.add(url)
            row = a.find_parent(["tr", "li", "dl"]) or a.parent
            m = POSTED_RE.search(row.get_text(" ") if row else "")
            posted = ex.parse_any_date(m.group(1)) if m else None
            out.append({"title": title, "url": url, "posted": posted, "desc": ""})
        if out:
            return out

    # 줄 단위 읽기 (링크를 찾지 못한 경우)
    kws = keywords or ["장학"]
    for row in soup.find_all(["tr", "li"]):
        if row.find(["tr", "li"]):  # 바깥 껍데기 줄은 건너뛴다
            continue
        raw = clean(row.get_text(" "))
        if not any(k in raw for k in kws) or not (6 <= len(raw) <= 200):
            continue
        m = POSTED_RE.search(raw)
        posted = ex.parse_any_date(m.group(1)) if m else None
        title = POSTED_RE.sub(" ", raw)
        title = re.sub(r"^\s*(\d+|공지|NEW|N)\s+", "", title)
        title = re.sub(r"\s+(조회수?\s*)?\d+\s*$", "", clean(title))
        if title in seen:
            continue
        seen.add(title)
        url = base_url
        for a in row.find_all("a", href=True):
            h = a["href"].strip()
            if h and not h.startswith(("#", "javascript", "mailto")):
                url = urljoin(base_url, h)
                break
        out.append({"title": title, "url": url, "posted": posted, "desc": "", "list_only": url == base_url})
    return out


def fetch_kosaf(f: Fetcher, src: dict, key: str, today: dt.date) -> list[dict]:
    """공공데이터포털 '한국장학재단_학자금지원정보(대학생)'. 매달 새 파일로 바뀌므로 가장 최신 경로를 찾아 쓴다."""
    oas = f.get("https://infuser.odcloud.kr/oas/docs?namespace=15028252/v1", robots=False).json()
    best, best_date = None, ""
    for path, spec in oas.get("paths", {}).items():
        summary = json.dumps(spec, ensure_ascii=False)
        dates = re.findall(r"20\d{6}", summary)
        d = max(dates) if dates else ""
        if d >= best_date:
            best, best_date = path, d
    if not best:
        raise RuntimeError("API 경로를 찾지 못함")
    rows, page = [], 1
    while True:
        r = f.get(
            "https://api.odcloud.kr/api" + best,
            robots=False,
            params={"page": page, "perPage": 500, "returnType": "JSON", "serviceKey": key},
        ).json()
        data = r.get("data") or []
        rows += data
        if not data or len(rows) >= int(r.get("totalCount") or 0) or page > 20:
            break
        page += 1

    out = []
    for row in rows:
        g = lambda k: clean(str(row.get(k) or ""))  # noqa: E731
        start, end = ex.parse_any_date(g("모집시작일")), ex.parse_any_date(g("모집종료일"))
        if end and end < today:
            continue
        if not end and (not start or start < today - dt.timedelta(days=90)):
            continue
        name, org = g("상품명"), g("운영기관명")
        url = g("홈페이지 주소")
        if url and not url.startswith("http"):
            url = "http://" + url
        # 특정 대학이 자기 학생에게 주는 교내 장학금은 제외
        if re.search(r"대학|학교", g("운영기관구분")) or ex.UNIV_RE.search(name):
            continue
        target = " / ".join(x for x in (g("대학구분"), g("학년구분"), g("학과구분")) if x and x not in ("-", "해당없음"))
        quals = " ".join((g("학과구분"), g("특정자격 상세내용"), g("자격제한 상세내용")))
        if ex.is_med_related(name, quals):
            scope = "med"
        elif re.search(r"전문대", g("대학구분")) and not re.search(r"4년|일반|제한|전체|무관", g("대학구분")):
            scope = "other"
        elif re.search(r"특정", g("학과구분")) or ex.OTHER_RE.search(g("학과구분")):
            scope = "other"
        else:
            scope = "open"
        out.append({
            "id": item_id("kosaf", org, name, g("모집시작일")),
            "title": name,
            "url": url or "https://www.data.go.kr/data/15028252/fileData.do",
            "posted": start,
            "org": org,
            "apply_start": start.isoformat() if start else "",
            "apply_end": end.isoformat() if end else "",
            "target": ex._short(target, 90),
            "amount": ex._short(g("지원내역 상세내용"), 70),
            "scope": scope,
            "med": scope == "med",
            "detail_done": True,
        })
    return out


def fetch_naver(f: Fetcher, src: dict, cid: str, secret: str) -> list[dict]:
    out, seen = [], set()
    for q in src.get("queries", []):
        r = f.get(
            "https://openapi.naver.com/v1/search/news.json",
            robots=False,
            params={"query": q, "display": min(int(src.get("display", 50)), 100), "sort": "date"},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret},
        ).json()
        for it in r.get("items", []):
            url = it.get("originallink") or it.get("link")
            if not url or url in seen:
                continue
            seen.add(url)
            strip = lambda s: clean(re.sub(r"<[^>]+>", "", s or ""))  # noqa: E731
            out.append({"title": strip(it.get("title")), "url": url, "posted": ex.parse_any_date(it.get("pubDate")), "desc": strip(it.get("description"))})
    return out


# ====================================================================== 메인
def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"items": {}, "sources": {}}


def run(cfg: dict, state: dict, today: dt.date) -> None:
    st = cfg.get("settings", {})
    inc, exc = st.get("include_keywords", ["장학"]), st.get("exclude_keywords", [])
    max_age = dt.timedelta(days=int(st.get("max_post_age_days", 120)))
    detail_budget = int(st.get("max_detail_fetch_per_run", 80))
    llm_key, llm_model = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("LLM_MODEL", "claude-haiku-5-5")
    llm_budget = int(st.get("max_llm_calls_per_run", 40)) if llm_key else 0

    f = Fetcher(st.get("user_agent", "KMSA-ScholarshipBot/1.0"), float(st.get("request_delay_seconds", 2)))
    items: dict = state["items"]
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    for src in cfg.get("sources", []):
        if not src.get("enabled", True):
            continue
        name, typ = src["name"], src["type"]
        src_inc = src.get("include_keywords", inc)
        src_exc = list(exc) + list(src.get("exclude_keywords", []))
        health = state["sources"].setdefault(name, {})
        health.update({"type": typ, "last_run": now})
        try:
            if typ == "kosaf_api":
                key = os.environ.get("DATA_GO_KR_KEY")
                if not key:
                    raise RuntimeError("DATA_GO_KR_KEY가 설정되지 않아 건너뜀")
                raw = fetch_kosaf(f, src, key, today)
            elif typ == "naver_news":
                cid, sec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
                if not (cid and sec):
                    raise RuntimeError("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없어 건너뜀")
                raw = fetch_naver(f, src, cid, sec)
            elif typ == "rss":
                raw = parse_rss(f.get(src["url"]).content, src["url"])
            elif typ == "html_list":
                raw = parse_html_list(f.get(src["url"]).text, src["url"], src.get("link_pattern"), src_inc)
            else:
                raise RuntimeError(f"알 수 없는 type: {typ}")
        except RobotsBlocked:
            health.update({"status": "robots 차단", "error": "사이트가 수집을 허용하지 않음(robots.txt)", "count": 0})
            log.warning("[%s] robots.txt 차단", name)
            continue
        except Exception as e:  # noqa: BLE001 - 한 곳이 실패해도 나머지는 계속
            health.update({"status": "실패", "error": str(e)[:200], "count": 0})
            log.warning("[%s] 실패: %s", name, e)
            continue

        kept = 0
        for r in raw:
            if typ != "kosaf_api" and not title_ok(r["title"], src_inc, src_exc):
                continue
            posted = r.get("posted")
            if posted and typ != "kosaf_api" and posted < today - max_age:
                continue
            iid = r.get("id") or (item_id(name, r["title"]) if r.get("list_only") else item_id(canonical_url(r["url"])))
            it = items.get(iid)
            if it is None:
                it = items[iid] = {
                    "id": iid, "title": r["title"], "url": r["url"], "source": name, "source_type": typ,
                    "posted": posted.isoformat() if posted else "", "first_seen": today.isoformat(),
                    "org": "", "apply_start": "", "apply_end": "", "target": "", "amount": "", "med": False, "scope": "",
                    "detail_done": False, "llm_done": False,
                }
            it["last_seen"] = today.isoformat()
            it["title"] = r["title"]
            for k in ("org", "apply_start", "apply_end", "target", "amount", "med", "scope", "detail_done"):
                if k in r:
                    it[k] = r[k]
            kept += 1

            # 상세 페이지에서 기간·대상·금액 뽑기 (처음 한 번만)
            if not it.get("detail_done"):
                ref = posted or today
                text = r.get("desc", "")
                if typ in ("rss", "html_list") and src.get("fetch_detail", True) and not r.get("list_only") and detail_budget > 0:
                    detail_budget -= 1
                    try:
                        text = page_text(f.get(r["url"]).text) or text
                        it["detail_done"] = True
                    except RobotsBlocked:
                        it["detail_done"] = True
                    except Exception as e:  # noqa: BLE001
                        log.info("상세 페이지 실패 %s: %s", r["url"], e)
                else:
                    it["detail_done"] = True
                found = ex.extract_all(r["title"], text, ref)
                for k, v in found.items():
                    if v and not it.get(k):
                        it[k] = v
                if llm_budget > 0 and text and not it.get("llm_done"):
                    llm_budget -= 1
                    res = ex.llm_extract(r["title"], text, ref, llm_key, llm_model)
                    if res:
                        it["llm_done"] = True
                        for k, v in res.items():
                            if v or k == "med":
                                it[k] = v  # LLM 판단(scope)이 규칙보다 우선
        if typ in ("rss", "html_list") and not raw:
            health.update({"status": "확인 필요", "error": "게시글을 하나도 읽지 못함. 사이트 구조가 바뀌었거나 자바스크립트로만 열리는 게시판일 수 있음", "count": 0})
            log.warning("[%s] 게시글 0건 - 구조 확인 필요", name)
            continue
        health.update({"status": "정상", "error": "", "count": kept, "last_ok": now})
        log.info("[%s] %d건", name, kept)

    # 오래된 공고 정리
    keep_closed = dt.timedelta(days=int(st.get("keep_closed_days", 60)))
    for iid in list(items):
        it = items[iid]
        end = ex.parse_any_date(it.get("apply_end"))
        last = ex.parse_any_date(it.get("last_seen")) or today
        if (end and end < today - keep_closed) or (not end and last < today - max_age):
            del items[iid]
    state["generated"] = now


def main():
    ap = argparse.ArgumentParser(description="의대협 장학 공고 수집기")
    ap.add_argument("--config", default=str(ROOT / "sources.yaml"))
    ap.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    ap.add_argument("--out", default=str(ROOT / "docs"))
    ap.add_argument("--report", action="store_true", help="수집 없이 페이지만 다시 만들기")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    state_path = Path(a.state)
    state = load_state(state_path)
    if not a.report:
        run(cfg, state, today_kst())
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_html(state, out / "index.html")
    write_csv(state, out / "scholarships.csv")
    log.info("완료: 공고 %d건 → %s", len(state["items"]), out / "index.html")


if __name__ == "__main__":
    sys.exit(main())
