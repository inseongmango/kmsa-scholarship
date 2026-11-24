"""수집 결과를 관리자용 HTML 한 장과 CSV로 내보낸다."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path


# 다른 전공 전용(other)과 특정 대학 교내 장학금(univ)은 보여주지 않는다
HIDDEN_SCOPES = ("other", "univ")


def _dedupe_key(title: str) -> str:
    t = re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", title)
    t = re.sub(r"\(\s*~?[^)]*(마감|연장|까지|~)[^)]*\)", "", t)
    return re.sub(r"[\W_]+", "", t).lower()


def grouped(state: dict) -> list[dict]:
    """여러 학교 게시판에 같은 공고가 올라온 경우 한 줄로 합친다."""
    groups: dict[str, dict] = {}
    for it in state.get("items", {}).values():
        if it.get("scope") in HIDDEN_SCOPES:
            continue
        k = _dedupe_key(it["title"]) or it["id"]
        g = groups.get(k)
        if g is None:
            g = groups[k] = dict(it, others=[])
        else:
            g["others"].append({"source": it["source"], "url": it["url"]})
            for f in ("org", "apply_start", "apply_end", "target", "amount"):
                if not g.get(f) and it.get(f):
                    g[f] = it[f]
            g["med"] = g.get("med") or it.get("med")
            g["first_seen"] = min(g["first_seen"], it["first_seen"])
    return sorted(groups.values(), key=lambda x: (x.get("apply_end") or "9999", x["first_seen"]))


def write_csv(state: dict, path: Path) -> None:
    cols = ["구분", "제목", "주관기관", "신청시작", "마감", "지원대상", "지원금액", "출처", "원문", "처음발견", "게시일"]
    with path.open("w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow(cols)
        for it in grouped(state):
            w.writerow(["의대 관련" if it.get("med") else "전공 무관", it["title"], it.get("org", ""), it.get("apply_start", ""),
                        it.get("apply_end", ""), it.get("target", ""), it.get("amount", ""), it["source"], it["url"],
                        it["first_seen"], it.get("posted", "")])


def write_html(state: dict, path: Path) -> None:
    data = {"generated": state.get("generated", ""), "items": grouped(state), "sources": state.get("sources", {})}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(TEMPLATE.replace("__DATA__", blob), encoding="utf-8")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>장학 공고 모아보기 · 의대협 관리자용</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@300;400;600&display=swap" rel="stylesheet">
<style>
/* IBM Carbon(getdesign.md/ibm) 기반: 흰 바탕, 진회색 글자, 파랑 하나만 강조, 각진 모서리, 그림자 없이 얇은 선 */
:root{
  --primary:#0f62fe;--primary-hover:#0050e6;--primary-press:#002d9c;--primary-soft:#edf5ff;
  --ink:#161616;--ink-muted:#525252;--ink-subtle:#8c8c8c;
  --canvas:#ffffff;--surface-1:#f4f4f4;--surface-2:#e0e0e0;--hairline:#e0e0e0;
  --inverse:#161616;--inverse-ink-muted:#c6c6c6;
  --red:#da1e28;--red-bg:#fff1f1;--red-ink:#a2191f;
  --yellow:#f1c21b;--yellow-bg:#fcf4d6;--yellow-ink:#684e00;
  --green:#24a148;--green-bg:#defbe6;--green-ink:#0e6027;
  --font:"IBM Plex Sans KR","IBM Plex Sans",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
*{box-sizing:border-box;border-radius:0}
body{margin:0;background:var(--canvas);color:var(--ink);font:400 14px/1.43 var(--font);letter-spacing:.16px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1312px;margin:0 auto}
.shell{background:var(--inverse);color:#fff;height:48px;display:flex;align-items:center;padding:0 24px;font-size:14px}
.shell b{font-weight:600}
.shell span{color:var(--inverse-ink-muted);margin-left:12px;padding-left:12px;border-left:1px solid #393939}
header{padding:40px 24px 32px;border-bottom:1px solid var(--hairline)}
.eyebrow{font-size:14px;color:var(--primary);margin-bottom:8px}
h1{font-size:42px;font-weight:300;line-height:1.2;margin:0 0 12px;letter-spacing:0}
.meta{color:var(--ink-muted);font-size:14px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:32px;border:1px solid var(--hairline)}
.stat{padding:16px;border-right:1px solid var(--hairline);background:var(--canvas)}
.stat:last-child{border-right:none}
.stat b{display:block;font-size:42px;font-weight:300;line-height:1.1;font-variant-numeric:tabular-nums}
.stat span{font-size:12px;color:var(--ink-muted);letter-spacing:.32px}
.stat.hl b{color:var(--red)}
.controls{position:sticky;top:0;z-index:5;background:var(--canvas);border-bottom:1px solid var(--hairline);padding:12px 24px}
.controls .wrap{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search],select{font:inherit;letter-spacing:.16px;height:40px;padding:0 16px;border:none;border-bottom:1px solid #8d8d8d;background:var(--surface-1);color:var(--ink);outline:none}
input[type=search]:focus,select:focus{outline:2px solid var(--primary);outline-offset:-2px}
input[type=search]{flex:1 1 260px;min-width:200px}
.chip{display:inline-flex;align-items:center;gap:8px;height:40px;padding:0 14px;border:1px solid var(--hairline);background:var(--canvas);cursor:pointer;font-size:14px;user-select:none}
.chip input{margin:0;accent-color:var(--primary)}
.chip:has(input:checked){border-color:var(--primary);background:var(--primary-soft);color:var(--primary)}
main{padding:24px 24px 64px}
table{width:100%;border-collapse:collapse;background:var(--canvas)}
#list{border:1px solid var(--hairline)}
th,td{padding:14px 16px;text-align:left;vertical-align:top;border-bottom:1px solid var(--hairline)}
th{font-size:14px;color:var(--ink);font-weight:600;background:var(--surface-2);white-space:nowrap;height:48px;vertical-align:middle}
#list tbody tr:hover td{background:#e8e8e8}
tr.done td{opacity:.45}
.title a{color:var(--ink);font-weight:600;text-decoration:none}
.title a:hover{color:var(--primary);text-decoration:underline}
.sub{color:var(--ink-muted);font-size:13px;margin-top:4px}
.badge{display:inline-block;font-size:12px;letter-spacing:.32px;line-height:16px;padding:4px 8px;white-space:nowrap;margin:0 4px 4px 0;border-radius:2px}
.b-new{background:var(--primary);color:#fff}
.b-med{background:var(--green-bg);color:var(--green-ink)}
.b-open{background:var(--surface-2);color:var(--ink)}
.b-urgent{background:var(--red-bg);color:var(--red-ink);box-shadow:inset 3px 0 0 var(--red)}
.b-soon{background:var(--yellow-bg);color:var(--yellow-ink);box-shadow:inset 3px 0 0 var(--yellow)}
.b-ok{background:var(--surface-1);color:var(--ink)}
.b-closed{background:var(--surface-1);color:var(--ink-subtle)}
.b-unknown{background:var(--canvas);border:1px dashed var(--ink-subtle);color:var(--ink-muted);padding:3px 7px}
.period{white-space:nowrap;font-variant-numeric:tabular-nums}
.src{font-size:13px;color:var(--ink-muted)}
.src a{color:var(--primary);text-decoration:none}
.src a:hover{text-decoration:underline}
button.copy{font:inherit;font-size:14px;letter-spacing:.16px;height:32px;padding:0 12px;border:1px solid var(--primary);background:var(--canvas);color:var(--primary);cursor:pointer;white-space:nowrap}
button.copy:hover{background:var(--primary-hover);border-color:var(--primary-hover);color:#fff}
button.copy:active{background:var(--primary-press)}
.done-box{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-muted);margin-top:8px;white-space:nowrap}
.done-box input{accent-color:var(--primary)}
.empty{padding:48px;text-align:center;color:var(--ink-muted)}
details{margin-top:32px;border:1px solid var(--hairline)}
summary{cursor:pointer;font-weight:600;padding:14px 16px;background:var(--surface-1)}
details[open] summary{border-bottom:1px solid var(--hairline)}
.health{width:100%}
.health th{background:var(--canvas);font-size:13px;height:40px}
.health td{font-size:13px;padding:10px 16px}
.st-ok{color:var(--green-ink);font-weight:600}.st-bad{color:var(--red-ink);font-weight:600}
.toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:var(--inverse);color:#fff;padding:14px 20px;font-size:14px;box-shadow:inset 3px 0 0 var(--green);opacity:0;transition:opacity .15s;pointer-events:none}
.toast.show{opacity:1}
@media (max-width:760px){
  .shell{padding:0 16px}
  .shell span{display:none}
  header,.controls,main{padding-left:16px;padding-right:16px}
  header{padding-top:28px;padding-bottom:24px}
  h1{font-size:30px}
  .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .stat:nth-child(2){border-right:none}
  .stat:nth-child(-n+2){border-bottom:1px solid var(--hairline)}
  .stat b{font-size:32px}
  .controls{position:static}
  .controls select{flex:1 1 40%}
  .chip,input[type=search],select{height:44px}
  #list,#list thead,#list tbody,#list tr,#list td{display:block}
  #list thead{display:none}
  #list{border:none}
  #list tr{border:1px solid var(--hairline);margin-bottom:12px;padding:8px 0}
  #list tbody tr:hover td{background:none}
  #list td{border:none;padding:6px 16px}
  #list td[data-l]::before{content:attr(data-l);display:inline-block;width:56px;color:var(--ink-muted);font-size:12px}
  details{overflow-x:auto}
  .health{min-width:600px}
  .health td:first-child{white-space:nowrap}
}
</style>
</head>
<body>
<div class="shell"><b>KMSA</b><span>대한의과대학·의학전문대학원학생협회 관리자</span></div>
<header><div class="wrap">
  <div class="eyebrow">장학금 게시판 등록용</div>
  <h1>장학 공고 모아보기</h1>
  <div class="meta">대학과 상관없이 의대생이 지원할 수 있는 장학금 · 마지막 수집 <span id="gen"></span> · 게시 전 원문과 대조해 주세요</div>
  <div class="stats" id="stats"></div>
</div></header>
<div class="controls"><div class="wrap">
  <input type="search" id="q" placeholder="장학금명, 기관, 대상 검색">
  <label class="chip"><input type="checkbox" id="fMed"> 의대 관련만</label>
  <label class="chip"><input type="checkbox" id="fOpen" checked> 마감 지난 공고 숨기기</label>
  <label class="chip"><input type="checkbox" id="fNew"> 새 공고만</label>
  <label class="chip"><input type="checkbox" id="fDone"> 게시 완료 숨기기</label>
  <select id="fSrc"><option value="">전체 출처</option></select>
  <select id="sort"><option value="end">마감 임박순</option><option value="new">최근 발견순</option></select>
</div></div>
<main><div class="wrap">
  <table id="list"><thead><tr><th style="width:150px">상태</th><th>장학금</th><th style="width:170px">신청기간</th><th style="width:170px">출처</th><th style="width:96px"></th></tr></thead>
  <tbody id="rows"></tbody></table>
  <details><summary>수집 현황</summary><table class="health"><thead><tr><th>출처</th><th>상태</th><th>건수</th><th>마지막 성공</th><th>메모</th></tr></thead><tbody id="health"></tbody></table></details>
</div></main>
<div class="toast" id="toast">게시용 문구를 복사했습니다</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kstToday = () => { const d = new Date(Date.now() + 9*3600e3); return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())); };
const TODAY = kstToday();
const toDate = s => s ? new Date(s + 'T00:00:00Z') : null;
const daysTo = s => { const d = toDate(s); return d ? Math.round((d - TODAY) / 864e5) : null; };
const fmt = s => s ? s.slice(2).replace(/-/g, '.') : '';
let done = {};
try { done = JSON.parse(localStorage.getItem('kmsa-done') || '{}'); } catch (e) {}
const saveDone = () => { try { localStorage.setItem('kmsa-done', JSON.stringify(done)); } catch (e) {} };

function status(it) {
  const n = daysTo(it.apply_end);
  if (n === null) return ['기간 확인 필요', 'b-unknown'];
  if (n < 0) return ['마감', 'b-closed'];
  if (n === 0) return ['오늘 마감', 'b-urgent'];
  if (n <= 3) return ['D-' + n, 'b-urgent'];
  if (n <= 7) return ['D-' + n, 'b-soon'];
  return ['D-' + n, 'b-ok'];
}
const isNew = it => (TODAY - toDate(it.first_seen)) / 864e5 <= 2;

function postText(it) {
  const lines = [(it.org ? '[' + it.org + '] ' : '') + it.title, ''];
  if (it.apply_start || it.apply_end) lines.push('신청기간: ' + (it.apply_start || '') + ' ~ ' + (it.apply_end || ''));
  if (it.target) lines.push('지원대상: ' + it.target);
  if (it.amount) lines.push('지원금액: ' + it.amount);
  lines.push('', '자세한 내용은 원문 공고를 확인해 주세요.', it.url);
  return lines.join('\n');
}

function render() {
  const q = $('q').value.trim().toLowerCase();
  let list = DATA.items.filter(it => {
    if ($('fMed').checked && !it.med) return false;
    if ($('fOpen').checked) { const n = daysTo(it.apply_end); if (n !== null && n < 0) return false; }
    if ($('fNew').checked && !isNew(it)) return false;
    if ($('fDone').checked && done[it.id]) return false;
    if ($('fSrc').value && it.source !== $('fSrc').value && !(it.others||[]).some(o => o.source === $('fSrc').value)) return false;
    if (q && ![it.title, it.org, it.target, it.amount, it.source].join(' ').toLowerCase().includes(q)) return false;
    return true;
  });
  if ($('sort').value === 'new') list = list.slice().sort((a, b) => (b.first_seen || '').localeCompare(a.first_seen || ''));
  $('rows').innerHTML = list.length ? list.map(it => {
    const [st, cls] = status(it);
    const others = (it.others || []);
    return `<tr class="${done[it.id] ? 'done' : ''}">
      <td data-l="상태">${isNew(it) ? '<span class="badge b-new">NEW</span>' : ''}<span class="badge ${cls}">${st}</span>${it.med ? '<span class="badge b-med">의대 관련</span>' : '<span class="badge b-open">전공 무관</span>'}</td>
      <td class="title"><a href="${esc(it.url)}" target="_blank" rel="noopener">${esc(it.title)}</a>
        ${it.org ? `<div class="sub">${esc(it.org)}</div>` : ''}
        ${it.target ? `<div class="sub">대상: ${esc(it.target)}</div>` : ''}
        ${it.amount ? `<div class="sub">금액: ${esc(it.amount)}</div>` : ''}</td>
      <td class="period" data-l="기간">${it.apply_start || it.apply_end ? esc(fmt(it.apply_start)) + ' ~ ' + esc(fmt(it.apply_end)) : '<span class="sub">원문 확인</span>'}</td>
      <td class="src" data-l="출처">${esc(it.source)}${others.length ? `<br>외 ${others.map(o => `<a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.source)}</a>`).join(', ')}` : ''}<br>발견 ${esc(fmt(it.first_seen))}</td>
      <td><button class="copy" data-id="${esc(it.id)}">게시용 복사</button>
        <label class="done-box"><input type="checkbox" data-done="${esc(it.id)}" ${done[it.id] ? 'checked' : ''}> 게시 완료</label></td>
    </tr>`;
  }).join('') : '<tr><td colspan="5" class="empty">조건에 맞는 공고가 없습니다</td></tr>';
}

function init() {
  $('gen').textContent = DATA.generated || '-';
  const items = DATA.items;
  const open = items.filter(it => { const n = daysTo(it.apply_end); return n === null || n >= 0; });
  const soon = open.filter(it => { const n = daysTo(it.apply_end); return n !== null && n <= 7; });
  $('stats').innerHTML = [
    ['진행 중·확인 필요', open.length], ['7일 안에 마감', soon.length],
    ['새로 발견', items.filter(isNew).length], ['의대 관련', open.filter(it => it.med).length]
  ].map(([l, n], i) => `<div class="stat${i === 1 && n > 0 ? ' hl' : ''}"><b>${n}</b><span>${l}</span></div>`).join('');
  const srcs = [...new Set(items.flatMap(it => [it.source, ...(it.others || []).map(o => o.source)]))].sort();
  $('fSrc').innerHTML += srcs.map(s => `<option>${esc(s)}</option>`).join('');
  $('health').innerHTML = Object.entries(DATA.sources).map(([n, h]) =>
    `<tr><td>${esc(n)}</td><td class="${h.status === '정상' ? 'st-ok' : 'st-bad'}">${esc(h.status || '-')}</td><td>${h.count ?? '-'}</td><td>${esc(h.last_ok || '-')}</td><td>${esc(h.error || '')}</td></tr>`).join('');
  ['q', 'fMed', 'fOpen', 'fNew', 'fDone', 'fSrc', 'sort'].forEach(id => $(id).addEventListener('input', render));
  $('rows').addEventListener('click', async e => {
    const b = e.target.closest('button.copy');
    if (!b) return;
    const it = DATA.items.find(x => x.id === b.dataset.id);
    const text = postText(it);
    try { await navigator.clipboard.writeText(text); }
    catch (err) { const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
    $('toast').classList.add('show'); setTimeout(() => $('toast').classList.remove('show'), 1600);
  });
  $('rows').addEventListener('change', e => {
    const id = e.target.dataset.done;
    if (!id) return;
    if (e.target.checked) done[id] = 1; else delete done[id];
    saveDone(); render();
  });
  render();
}
init();
</script>
</body>
</html>
"""
