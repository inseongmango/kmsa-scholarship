"""네트워크 없이 파싱·추출·리포트를 확인하는 테스트.  실행: python -m pytest tests"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crawler  # noqa: E402
import extract as ex  # noqa: E402
from report import grouped, write_csv, write_html  # noqa: E402

REF = dt.date(2026, 11, 24)

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title><![CDATA[2027년도 송원김영환장학재단 장학생 추천 안내(모집 연장)}]]></title>
<link>/bbs/sc/58/944667/artclView.do?layout=unknown</link><pubDate>2026-11-18 11:26:07.0</pubDate>
<author>a</author><description><![CDATA[<p>신청기간: 2026. 11. 18.(수) ~ 12. 5.(금) 17:00</p>]]></description></item>
<item><title><![CDATA[2027학년도 1학기 전공 신청 및 진입 확인 안내}]]></title>
<link>/bbs/sc/58/944702/artclView.do?layout=unknown</link><pubDate>2026-11-23 09:36:58.0</pubDate></item>
<item><title><![CDATA[2026-2 장학생 선발 결과 안내}]]></title>
<link>/bbs/sc/58/944000/artclView.do</link><pubDate>2026-11-20 09:00:00.0</pubDate></item>
</channel></rss>"""

DETAIL = """<html><body><div id="header">2026.01.01 ~ 2026.12.31 메뉴</div>
<div class="artclInfo"><dl><dt>작성일</dt><dd>2026.10.06</dd><dt>기간</dt><dd>2026.10.06 ~ 2026.10.26</dd></dl></div>
<div class="artclView"><p>1. 신청대상: 의생명과학분야 연구자가 되고자 하는 대학생</p>
<p>2. 지원내용: 학기당 300만원(연 600만원)</p><p>성적 평점이 3.7/4.5 이상</p></div></body></html>"""

GNU = """<table><tr><td class="td_subject"><a href="https://ex.org/bbs/board.php?bo_table=notice&wr_id=12">[보건장학회] 2026 학업지원 장학생 선발 공고 새글</a></td><td class="td_datetime">26.07.01</td></tr>
<tr><td><a href="https://ex.org/bbs/board.php?bo_table=notice&wr_id=11">정기총회 안내</a></td><td>26.06.30</td></tr></table>"""


def test_period_patterns():
    cases = {
        "기간 2026.10.06 ~ 2026.10.26": ("2026-10-06", "2026-10-26"),
        "3. 신청기간: 2026.10.06.(화)~2026.10.26.(월) 23:59": ("2026-10-06", "2026-10-26"),
        "1) 접수기간: 2026. 7. 10.() 2026. 7. 31.() 17:00까지": ("2026-07-10", "2026-07-31"),
        "1. 신청기간 : 2026. 8. 24(월) 09:00 ~ 2026. 9. 22(화) 18:00": ("2026-08-24", "2026-09-22"),
        "모집기간: 2026년 12월 20일 ~ 2027년 1월 9일": ("2026-12-20", "2027-01-09"),
        "신청기간: 2026. 12. 28.(월) ~ 1. 8.(금)": ("2026-12-28", "2027-01-08"),
        "신청 마감: 2026.12.29(월) 16:00까지": ("", "2026-12-29"),
        "서류 제출: 12월 5일(금)까지": ("", "2026-12-05"),
    }
    for text, (s, e) in cases.items():
        a, b = ex.extract_period(text, REF)
        assert (a.isoformat() if a else "", b.isoformat() if b else "") == (s, e), text


def test_period_ignores_grades_and_phone():
    a, b = ex.extract_period("성적 평점 3.7/4.5 이상\n문의 033-649-7443", REF)
    assert (a, b) == (None, None)


def test_title_deadline():
    a, b = ex.extract_period("", REF, "2026학년도 2학기 한경범장학회 장학금 신청 안내(~7/31)")
    assert b == dt.date(2027, 7, 31) or b == dt.date(2026, 7, 31)


def test_rss_parse_and_filter():
    items = crawler.parse_rss(RSS.encode(), "https://www.yonsei.ac.kr/bbs/sc/58/rssList.do?row=50")
    assert items[0]["title"] == "2027년도 송원김영환장학재단 장학생 추천 안내(모집 연장)"
    assert items[0]["url"].startswith("https://www.yonsei.ac.kr/bbs/sc/58/944667/artclView.do")
    assert items[0]["posted"] == dt.date(2026, 11, 18)
    inc, exc = ["장학"], ["결과"]
    kept = [i["title"] for i in items if crawler.title_ok(i["title"], inc, exc)]
    assert kept == ["2027년도 송원김영환장학재단 장학생 추천 안내(모집 연장)"]
    s, e = ex.extract_period(items[0]["desc"], items[0]["posted"])
    assert (s, e) == (dt.date(2026, 11, 18), dt.date(2026, 12, 5))


def test_detail_extract():
    text = crawler.page_text(DETAIL)
    found = ex.extract_all("2027년도 아산사회복지재단 의생명과학분야 대학 장학생 선발 안내", text, dt.date(2026, 10, 6))
    assert found["apply_start"] == "2026-10-06" and found["apply_end"] == "2026-10-26"
    assert "의생명과학" in found["target"]
    assert "300만원" in found["amount"]
    assert found["org"] == "아산사회복지재단"
    assert found["med"] is True


def test_gnuboard_list():
    items = crawler.parse_html_list(GNU, "https://ex.org/bbs/board.php?bo_table=notice", r"wr_id=\d+")
    assert items[0]["title"] == "[보건장학회] 2026 학업지원 장학생 선발 공고"
    assert items[0]["posted"] == dt.date(2026, 7, 1)
    assert ex.extract_org(items[0]["title"]) == "보건장학회"


class FakeResp:
    def __init__(self, body):
        self._b = body
        self.content = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return self._b if not isinstance(self._b, str) else json.loads(self._b)


class FakeFetcher:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, robots=True, **kw):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                if isinstance(v, Exception):
                    raise v
                return FakeResp(v)
        raise RuntimeError("404 " + url)


OAS = {"paths": {
    "/15028252/v1/uddi:old": {"get": {"summary": "한국장학재단_학자금지원정보(대학생)_20261014"}},
    "/15028252/v1/uddi:new": {"get": {"summary": "한국장학재단_학자금지원정보(대학생)_20261110"}},
}}
KOSAF = {"totalCount": 5, "data": [
    {"운영기관명": "보건복지부", "상품명": "공중보건장학금", "학과구분": "의학계열", "모집시작일": "2026-11-20", "모집종료일": "2026-12-10", "지원내역 상세내용": "학기당 1,020만원", "홈페이지 주소": "www.mohw.go.kr"},
    {"운영기관명": "OO시", "상품명": "OO시 인재장학금", "학과구분": "제한없음", "모집시작일": "2026-09-01", "모집종료일": "2026-09-30"},
    {"운영기관명": "OO재단", "운영기관구분": "민간장학재단", "상품명": "OO 장학금", "학과구분": "제한없음", "모집시작일": "2026-11-01", "모집종료일": "2026-11-30"},
    {"운영기관명": "OO대학교", "운영기관구분": "대학", "상품명": "OO대 성적우수장학금", "학과구분": "제한없음", "모집시작일": "2026-11-01", "모집종료일": "2026-12-30"},
    {"운영기관명": "XX재단", "운영기관구분": "민간장학재단", "상품명": "XX 이공계 장학금", "학과구분": "공학계열", "모집시작일": "2026-11-01", "모집종료일": "2026-12-30"},
]}


def test_full_run(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    routes = {
        "infuser.odcloud.kr": OAS,
        "api.odcloud.kr/api/15028252/v1/uddi:new": KOSAF,
        "empty.example": "<html><body><div id='app'>로딩 중...</div></body></html>",
        "wj/104/rssList.do": RSS.replace("/bbs/sc/58/944667", "/bbs/wj/104/555"),
        "rssList.do": RSS,
        "944667/artclView.do": DETAIL.replace("2026.10.06 ~ 2026.10.26", "2026.11.18 ~ 2026.12.05"),
    }
    fake = FakeFetcher(routes)
    monkeypatch.setattr(crawler, "Fetcher", lambda *a, **k: fake)
    cfg = {"settings": {"include_keywords": ["장학"], "exclude_keywords": ["결과"]}, "sources": [
        {"name": "한국장학재단", "type": "kosaf_api"},
        {"name": "연세대", "type": "rss", "url": "https://www.yonsei.ac.kr/bbs/sc/58/rssList.do?row=50"},
        {"name": "연세대 복제", "type": "rss", "url": "https://www.yonsei.ac.kr/bbs/wj/104/rssList.do?row=50"},
        {"name": "고장난 곳", "type": "rss", "url": "https://broken.example/rss"},
        {"name": "빈 게시판", "type": "html_list", "url": "https://empty.example/notice", "link_pattern": r"wr_id=\d+"},
        {"name": "네이버", "type": "naver_news", "queries": ["x"]},
    ]}
    state = {"items": {}, "sources": {}}
    crawler.run(cfg, state, REF)
    titles = sorted(i["title"] for i in state["items"].values())
    assert "OO시 인재장학금" not in titles            # 마감 지난 공공데이터 제외
    assert "공중보건장학금" in titles
    assert any("송원김영환" in t for t in titles)
    assert any("api.odcloud.kr/api/15028252/v1/uddi:new" in c for c in fake.calls)
    yon = next(i for i in state["items"].values() if "송원김영환" in i["title"])
    assert yon["apply_end"] == "2026-12-05" and yon["first_seen"] == "2026-11-24"
    assert state["sources"]["고장난 곳"]["status"] == "실패"
    assert state["sources"]["빈 게시판"]["status"] == "확인 필요"
    assert state["sources"]["네이버"]["status"] == "실패"  # 키 없음
    pub = next(i for i in state["items"].values() if i["title"] == "공중보건장학금")
    assert pub["med"] is True and pub["url"] == "http://www.mohw.go.kr"
    assert "OO대 성적우수장학금" not in titles     # 대학이 운영하는 교내 장학금 제외
    xx = next(i for i in state["items"].values() if "이공계" in i["title"])
    assert xx["scope"] == "other"
    assert next(i for i in state["items"].values() if i["title"] == "OO 장학금")["scope"] == "open"
    assert not any("이공계" in x["title"] for x in grouped(state))  # 다른 전공 전용은 숨김

    # 같은 글이 두 게시판에 있으면 한 줄로 묶인다
    g = grouped(state)
    song = [x for x in g if "송원김영환" in x["title"]]
    assert len(song) == 1 and len(song[0]["others"]) == 1

    # 두 번째 실행: 상세 페이지를 다시 열지 않고 first_seen 유지
    n_before = len(fake.calls)
    crawler.run(cfg, state, REF + dt.timedelta(days=1))
    assert not any("944667/artclView.do" in c for c in fake.calls[n_before:])
    assert yon["first_seen"] == "2026-11-24"

    write_html(state, tmp_path / "index.html")
    write_csv(state, tmp_path / "s.csv")
    h = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "송원김영환" in h and "__DATA__" not in h
    assert (tmp_path / "s.csv").read_text(encoding="utf-8-sig").startswith("구분,제목")


def test_classify():
    assert ex.classify("2026년 공중보건장학제도 시범사업 장학생 모집") == "med"
    assert ex.classify("(재)보건장학회 학업지원 장학생 선발", "의학과, 약학과, 치의학과, 간호학과 3·4학년") == "med"
    assert ex.classify("2026 대통령과학장학금(이공계) 신규 장학생 선발") == "other"
    assert ex.classify("OO치과대학 장학생 모집", "치의학과 재학생") == "other"
    assert ex.classify("한의학 장학금", "한의학과 재학생") == "other"
    assert ex.classify("2026-2 교내 성적우수장학금 신청 안내") == "univ"
    assert ex.classify("OO시 인재육성장학금 대학생 장학생 모집", "OO시 거주 대학생") == "open"
    assert ex.classify("장학생 모집", "협의대상 없음") == "open"


HFF = """<table><tr><td class="subject"><a href="/kor/foundation-news/notice.html?bmain=view&uid=31&search=%26page%3D1">2026년도 2학기 (재)보건장학회 학업지원 장학생 선발 공고</a></td><td>2026.06.30</td></tr>
<tr><td><a href="/kor/foundation-news/notice.html?bmain=view&uid=33&search=%26page%3D1">2026년도 연구지원 장학생 최종 선발자 명단</a></td><td>2026.07.30</td></tr></table>"""

DBF = """<ul class="board"><li><a href="javascript:void(0)" onclick="goView(77)"><span class="no">12</span><span class="tit">2027년 DB드림리더 장학생 모집</span><span class="date">2026.12.15</span></a></li>
<li><a href="#" onclick="goView(76)"><span class="tit">제122회 대학생 온라인 기업경영 체험스쿨 참가자 모집</span><span class="date">2026.10.19</span></a></li></ul>"""


def test_foundation_lists():
    base = "https://www.kr-hff.or.kr/kor/foundation-news/notice.html"
    items = crawler.parse_html_list(HFF, base, r"bmain=view&uid=\d+")
    assert items[0]["url"].startswith("https://www.kr-hff.or.kr/kor/foundation-news/notice.html?bmain=view&uid=31")
    assert crawler.canonical_url(items[0]["url"]).endswith("bmain=view&uid=31")
    assert items[0]["posted"] == dt.date(2026, 6, 30)
    assert not crawler.title_ok(items[1]["title"], ["장학"], ["명단"])

    # 자바스크립트 링크 게시판: 줄 글자에서 제목을 읽고 링크는 목록 주소
    db = crawler.parse_html_list(DBF, "https://www.dbfoundation.or.kr/news/notice/notice", None, ["장학", "드림리더"])
    assert len(db) == 1
    assert db[0]["title"] == "2027년 DB드림리더 장학생 모집"
    assert db[0]["posted"] == dt.date(2026, 12, 15)
    assert db[0]["url"] == "https://www.dbfoundation.or.kr/news/notice/notice" and db[0]["list_only"]
    # 패턴이 있어도 하나도 안 맞으면 같은 방식으로 넘어간다
    db2 = crawler.parse_html_list(DBF, "https://www.dbfoundation.or.kr/news/notice/notice", r"/view/\d+", ["장학"])
    assert [x["title"] for x in db2] == ["2027년 DB드림리더 장학생 모집"]
