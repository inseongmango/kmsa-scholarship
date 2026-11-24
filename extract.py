"""공고문 텍스트에서 신청기간, 지원대상, 지원금액, 주관기관을 뽑아내는 함수 모음."""
from __future__ import annotations

import datetime as dt
import json
import re
from email.utils import parsedate_to_datetime

import requests

# ---------------------------------------------------------------- 날짜 패턴
_FULL = (
    r"(?P<{p}y>(?:19|20)\d{{2}})\s*[.\-/년]\s*(?P<{p}m>1[0-2]|0?[1-9])\s*[.\-/월]\s*"
    r"(?P<{p}d>3[01]|[12]\d|0?[1-9])(?!\d)\s*(?:일|\.)?"
)
_PART = r"(?P<{p}m>1[0-2]|0?[1-9])\s*[.\-/월]\s*(?P<{p}d>3[01]|[12]\d|0?[1-9])(?!\d)\s*(?:일|\.)?"
_TAIL = r"(?:\s*\(\s*[월화수목금토일]?\s*\))?(?:\s*\d{1,2}\s*:\s*\d{2})?(?:\s*까지)?"
_SEP = r"\s*(?:부터)?\s*[~∼～〜\-–—]\s*"

FULL_RE = re.compile(_FULL.format(p="a"))
RANGE_FULL_RE = re.compile(_FULL.format(p="a") + _TAIL + r"\s*(?:[~∼～〜\-–—]|부터)?\s*" + _FULL.format(p="b"))
RANGE_PART_RE = re.compile(_FULL.format(p="a") + _TAIL + _SEP + _PART.format(p="b"))
UNTIL_FULL_RE = re.compile(r"(?:~|마감\s*[:：]?)\s*" + _FULL.format(p="a") + r"|" + _FULL.format(p="b") + _TAIL.replace(r"(?:\s*까지)?", r"\s*까지"))
UNTIL_PART_RE = re.compile(r"~\s*" + _PART.format(p="a") + r"|" + _PART.format(p="b") + r"(?:\s*\(\s*[월화수목금토일]?\s*\))?(?:\s*\d{1,2}\s*:\s*\d{2})?\s*까지")

KEY_STRONG = re.compile(r"(신청|접수|모집|제출|지원)\s*(기간|기한|일정|마감)|마감\s*일?")
KEY_WEAK = re.compile(r"기간|까지|마감")


def _mk(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d))
    except (TypeError, ValueError):
        return None


def parse_any_date(s: str | None) -> dt.date | None:
    """RSS pubDate, '2026-11-23 09:36:58.0', '2026.11.23' 같은 문자열을 날짜로."""
    if not s:
        return None
    s = s.strip()
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError, IndexError):
        pass
    m = FULL_RE.search(s)
    if m:
        return _mk(m["ay"], m["am"], m["ad"])
    m = re.search(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)", s)
    if m:
        return _mk(*m.groups())
    m = re.search(r"(?<!\d)(\d{2})[.\-](\d{2})[.\-](\d{2})(?!\d)", s)  # 26.11.23
    if m:
        return _mk("20" + m[1], m[2], m[3])
    return None


def _infer_year(month, day, ref: dt.date) -> dt.date | None:
    d = _mk(ref.year, month, day)
    if d and d < ref - dt.timedelta(days=60):
        d = _mk(ref.year + 1, month, day)
    return d


def _range_in(line: str):
    m = RANGE_FULL_RE.search(line)
    if m:
        a, b = _mk(m["ay"], m["am"], m["ad"]), _mk(m["by"], m["bm"], m["bd"])
        if a and b and a <= b and (b - a).days <= 400:
            return a, b
    m = RANGE_PART_RE.search(line)
    if m:
        a = _mk(m["ay"], m["am"], m["ad"])
        if a:
            b = _mk(a.year, m["bm"], m["bd"])
            if b and b < a:
                b = _mk(a.year + 1, m["bm"], m["bd"])
            if b:
                return a, b
    return None


def _until_in(line: str, ref: dt.date):
    m = UNTIL_FULL_RE.search(line)
    if m:
        p = "a" if m.group("ay") else "b"
        return _mk(m[p + "y"], m[p + "m"], m[p + "d"])
    m = UNTIL_PART_RE.search(line)
    if m:
        p = "a" if m.group("am") else "b"
        return _infer_year(m[p + "m"], m[p + "d"], ref)
    return None


def extract_period(text: str, ref: dt.date, title: str = "") -> tuple[dt.date | None, dt.date | None]:
    """신청 시작일과 마감일을 찾는다. 신청·접수·모집이 들어간 줄을 먼저 본다."""
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text or "") if ln.strip()]
    strong = [ln for ln in lines if KEY_STRONG.search(ln)]
    weak = [ln for ln in lines if KEY_WEAK.search(ln) and ln not in strong]
    for group in (strong, weak):
        for i, ln in enumerate(group):
            # 날짜가 다음 줄로 넘어간 경우를 위해 다음 줄까지 붙여 본다
            nxt = lines[lines.index(ln) + 1] if lines.index(ln) + 1 < len(lines) else ""
            r = _range_in(ln) or _range_in(ln + " " + nxt)
            if r:
                return r
        for ln in group:
            e = _until_in(ln, ref)
            if e:
                return None, e
    # 제목에 '(~7/31)', '[신청 마감: 2025.12.29(월)]' 같은 표기가 있는 경우
    if title:
        r = _range_in(title)
        if r:
            return r
        e = _until_in(title, ref)
        if e:
            return None, e
    return None, None


# ---------------------------------------------------------------- 기타 항목
TARGET_KEY = re.compile(r"(신청|지원|선발|모집)\s*(대상|자격)|자격\s*요건|대\s*상\s*[:：]")
AMOUNT_KEY = re.compile(r"(지원|장학|수혜|지급)\s*(금액|금|내용|내역|액)|금\s*액")
ORG_RE = re.compile(r"[\[(（【]\s*([^\]\)）】]{2,25}?(?:재단|장학회|장학재단|공단|협회|부|청|도|시|군|구|의료원|병원|그룹|주식회사|㈜)[^\]\)）】]{0,8})\s*[\])）】]")
ORG_INLINE_RE = re.compile(r"((?:\(재\)|재단법인|\(사\)|사단법인)?\s*[가-힣A-Za-z]{2,20}(?:장학재단|장학회|문화재단|복지재단|교육재단|재단))")

# 치의학·한의학·수의학은 '의학'에 걸리지 않도록 앞 글자를 막는다
MED_RE = re.compile(
    r"(?<![치한수협회논합동건결주])(?:의과\s*대학|의학과|의예과|의대생|의대|의학\s*전문\s*대학원|의학)|의전원|공중보건\s*장학|의생명|의약\s*계열|예비\s*의사|의\s*[/·・]\s*(?:약|치|한)"
)
# 의대생이 지원할 수 없는 다른 전공 전용 표현
OTHER_RE = re.compile(
    r"이공계|공학\s*계열|자연\s*계열|인문\s*계열|사회\s*계열|예체능|간호|약학(?:대학|과)?|약대|"
    r"치의학|치과대학|한의학|한의대|수의학|수의대|사범대|교육대학|공과대학|법학전문대학원|로스쿨|전문대학|특성화고"
)
# 특정 지역 주민·출신만 지원할 수 있는 장학금
REGION_RE = re.compile(
    r"[가-힣]{1,6}(?:시|군|구|도)\s*(?:에\s*)?(?:거주|출신|소재|연고)|거주\s*(?:자|학생|대학생|하는)|주민등록|관내|(?:도|시|군)내\s*(?:거주|출신)"
)
# 보훈·다자녀·교직원 자녀처럼 특정 집단만 지원할 수 있는 장학금
SPECIAL_RE = re.compile(r"유자녀|다자녀|교직원|다문화|탈북|북한이탈|북한배경|보훈|국가유공|순직|귀농|한부모|장애인|조손|자립준비|보호종료|농어업인\s*자녀|임직원\s*자녀")
# 특정 대학 재학생만 대상인 교내 장학금
UNIV_RE = re.compile(r"교내|본교|우리\s*대학|재학생\s*한정")


def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip(" :：-·•○◦□■※")
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_line(text: str, key: re.Pattern, n: int = 90, need: str | None = None) -> str:
    for ln in re.split(r"[\r\n]+", text or ""):
        if key.search(ln):
            # '1. 신청대상:' 처럼 머리말만 있는 줄이면 건너뛴다
            body = key.split(ln, maxsplit=1)[-1]
            body = re.sub(r"^[\s:：\-·•○◦□■※)]+", "", body)
            if len(body) >= 4 and (need is None or re.search(need, body)):
                return _short(body, n)
    return ""


def extract_org(title: str, text: str = "") -> str:
    m = ORG_RE.search(title)
    if m:
        return _short(m[1], 30)
    m = ORG_INLINE_RE.search(title) or ORG_INLINE_RE.search((text or "")[:1500])
    return _short(m[1], 30) if m else ""


def is_med_related(*texts: str) -> bool:
    return any(MED_RE.search(t or "") for t in texts)


def classify(title: str, target: str = "", text: str = "") -> str:
    """med: 의대생 대상이거나 명시적으로 포함 / open: 전공 무관 / other: 다른 전공 전용 / univ: 교내 장학금"""
    head = f"{title} {target}"
    if UNIV_RE.search(title):
        return "univ"
    if MED_RE.search(head):
        return "med"
    if OTHER_RE.search(head):
        return "other"
    if MED_RE.search((text or "")[:4000]):
        return "med"
    return "open"


def extract_all(title: str, text: str, ref: dt.date) -> dict:
    start, end = extract_period(text, ref, title)
    target = extract_line(text, TARGET_KEY, 90)
    scope = classify(title, target, text)
    return {
        "apply_start": start.isoformat() if start else "",
        "apply_end": end.isoformat() if end else "",
        "target": target,
        "amount": extract_line(text, AMOUNT_KEY, 70, need=r"\d[\d,.]*\s*(만\s*)?원|전액|등록금"),
        "org": extract_org(title, text),
        "scope": scope,
        "med": scope == "med",
        "region": bool(REGION_RE.search(f"{title} {target}")),
        "special": bool(SPECIAL_RE.search(f"{title} {target}")),
    }


# ---------------------------------------------------------------- 선택: LLM 보정
LLM_PROMPT = """다음은 장학금 공고문이다. 아래 JSON 형식으로만 답하라. 모르면 빈 문자열.
{{"org": 주관기관, "apply_start": "YYYY-MM-DD", "apply_end": "YYYY-MM-DD", "target": 지원대상 요약(60자 이내), "amount": 지원금액 요약(40자 이내), "scope": 아래 넷 중 하나}}
scope: "med" = 의대생(의예과·의학과·의전원) 전용이거나 지원 대상에 의대생이 명시됨 / "open" = 전공 제한 없이 어느 대학 학생이든 지원 가능 / "other" = 의대생은 지원할 수 없는 다른 전공·계열 전용 / "univ" = 특정 대학 재학생만 대상인 교내 장학금
게시일: {ref}
제목: {title}
본문:
{text}"""


def llm_extract(title: str, text: str, ref: dt.date, api_key: str, model: str) -> dict | None:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": model,
                "max_tokens": 400,
                "messages": [{"role": "user", "content": LLM_PROMPT.format(ref=ref, title=title, text=text[:6000])}],
            },
            timeout=60,
        )
        r.raise_for_status()
        out = "".join(b.get("text", "") for b in r.json().get("content", []))
        m = re.search(r"\{.*\}", out, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        res = {}
        for k in ("apply_start", "apply_end"):
            d = parse_any_date(str(data.get(k) or ""))
            res[k] = d.isoformat() if d else ""
        for k, n in (("org", 30), ("target", 90), ("amount", 70)):
            res[k] = _short(str(data.get(k) or ""), n)
        sc = str(data.get("scope") or "").strip().lower()
        if sc in ("med", "open", "other", "univ"):
            res["scope"] = sc
            res["med"] = sc == "med"
        return res
    except (requests.RequestException, ValueError, KeyError):
        return None
