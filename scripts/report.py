#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""report.py — 指标聚合与渲染（文本 / JSON / HTML 看板）

口径
----
命中率 = cache_read / input
    input 已包含 cache_read，故「未命中」= input - cache_read
    总量 = input + output + cache_write（缓存命中部分不重复累加）
一次任务 = 一个 trace_id 下的全部 API 调用（无 trace_id 时退化为 session 粒度）
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collect import DB_PATH, connect  # noqa: E402

RANGES = {
    "24h": ("最近 24 小时", 1),
    "7d": ("最近 7 天", 7),
    "30d": ("最近 30 天", 30),
    "all": ("全部", None),
}
LOW_HIT_THRESHOLD = 90.0      # 低于此值在面板中标红


def parse_range(token):
    if token and token.endswith("d") and token[:-1].isdigit():
        return f"最近 {token[:-1]} 天", int(token[:-1])
    name, days = RANGES.get(token or "all", RANGES["all"])
    return name, days


def load_tasks(db, days=None, workspace=None):
    sql = """
        SELECT COALESCE(NULLIF(trace_id,''), 'sess:'||session_id) AS tkey,
               MIN(ts) AS started, MAX(ts) AS ended,
               workspace,
               GROUP_CONCAT(DISTINCT model) AS models,
               COUNT(*) AS calls,
               SUM(input) AS input,
               SUM(output) AS output,
               SUM(cache_read) AS cache_read,
               SUM(cache_write) AS cache_write,
               SUM(CASE WHEN flags<>'' THEN 1 ELSE 0 END) AS flagged
        FROM requests
        WHERE 1=1
    """
    args = []
    if days:
        cutoff = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
        sql += " AND ts >= ?"
        args.append(cutoff)
    if workspace:
        sql += " AND workspace = ?"
        args.append(workspace)
    sql += " GROUP BY tkey ORDER BY started DESC"
    rows = db.execute(sql, args).fetchall()

    tasks = []
    for (tkey, started, ended, ws, models, calls, inp, outp, cr, cw, flagged) in rows:
        inp, outp, cr, cw = inp or 0, outp or 0, cr or 0, cw or 0
        hit = (cr / inp * 100) if inp else 0.0
        tasks.append({
            "id": tkey,
            "started": started or 0,
            "ended": ended or 0,
            "duration_s": round(((ended or 0) - (started or 0)) / 1000, 1),
            "workspace": ws,
            "models": models or "unknown",
            "calls": calls,
            "input": inp,
            "output": outp,
            "cache_read": cr,
            "cache_write": cw,
            "uncached": max(inp - cr, 0),
            "hit_rate": round(hit, 2),
            "flagged": flagged,
            "low_hit": hit < LOW_HIT_THRESHOLD,
        })
    return tasks


def summarize(tasks):
    if not tasks:
        return {"tasks": 0, "calls": 0, "input": 0, "output": 0, "cache_read": 0,
                "cache_write": 0, "uncached": 0, "hit_rate": 0.0, "low_hit": 0,
                "p50": 0.0, "p90": 0.0, "best": 0.0, "worst": 0.0}
    rates = sorted(t["hit_rate"] for t in tasks)
    n = len(rates)
    s = {k: sum(t[k] for t in tasks) for k in
         ("calls", "input", "output", "cache_read", "cache_write", "uncached")}
    s["tasks"] = n
    s["hit_rate"] = round(s["cache_read"] / s["input"] * 100, 2) if s["input"] else 0.0
    s["low_hit"] = sum(1 for t in tasks if t["low_hit"])
    s["p50"] = round(rates[n // 2], 2)
    s["p90"] = round(rates[min(int(n * 0.9), n - 1)], 2)
    s["best"] = round(rates[-1], 2)
    s["worst"] = round(rates[0], 2)
    return s


def by_dimension(tasks, key):
    agg = {}
    for t in tasks:
        for part in str(t[key]).split(","):
            part = part.strip() or "unknown"
            a = agg.setdefault(part, {"key": part, "tasks": 0, "calls": 0, "input": 0,
                                      "cache_read": 0, "output": 0})
            a["tasks"] += 1
            a["calls"] += t["calls"]
            a["input"] += t["input"]
            a["cache_read"] += t["cache_read"]
            a["output"] += t["output"]
    out = list(agg.values())
    for a in out:
        a["hit_rate"] = round(a["cache_read"] / a["input"] * 100, 2) if a["input"] else 0.0
    return sorted(out, key=lambda x: -x["input"])


def daily_series(db, days):
    cutoff = int((datetime.now() - timedelta(days=days)).timestamp() * 1000) if days else 0
    rows = db.execute(
        "SELECT ts, input, cache_read, output FROM requests WHERE ts >= ?", (cutoff,)).fetchall()
    buckets = {}
    for ts, inp, cr, outp in rows:
        if not ts:
            continue
        day = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
        b = buckets.setdefault(day, {"day": day, "input": 0, "cache_read": 0, "output": 0})
        b["input"] += inp or 0
        b["cache_read"] += cr or 0
        b["output"] += outp or 0
    series = sorted(buckets.values(), key=lambda x: x["day"])
    for b in series:
        b["hit_rate"] = round(b["cache_read"] / b["input"] * 100, 2) if b["input"] else 0.0
    return series


def fmt_num(n):
    if n >= 1e8:
        return f"{n/1e8:.2f}亿"
    if n >= 1e4:
        return f"{n/1e4:.1f}万"
    return f"{n:,}"


def render_text(tasks, summary, range_name, models, workspaces, anomalies):
    L = []
    L.append("=" * 66)
    L.append(f"WorkBuddy 缓存命中率报告 · {range_name}")
    L.append("=" * 66)
    L.append(f"任务数 {summary['tasks']}   请求数 {summary['calls']:,}   "
             f"整体命中率 {summary['hit_rate']:.2f}%")
    L.append(f"输入 {fmt_num(summary['input'])}   其中命中 {fmt_num(summary['cache_read'])}   "
             f"未命中 {fmt_num(summary['uncached'])}")
    L.append(f"输出 {fmt_num(summary['output'])}   任务命中率 P50 {summary['p50']}% · "
             f"P90 {summary['p90']}% · 区间 {summary['worst']}%~{summary['best']}%")
    if summary["low_hit"]:
        L.append(f"⚠ 有 {summary['low_hit']} 个任务命中率低于 {LOW_HIT_THRESHOLD:.0f}%")
    L.append("")
    L.append("【按模型】")
    L.append(f"{'模型':<26}{'任务':>6}{'请求':>7}{'命中率':>10}{'输入token':>14}")
    for m in models:
        L.append(f"{m['key'][:25]:<26}{m['tasks']:>6}{m['calls']:>7}"
                 f"{m['hit_rate']:>9.1f}%{fmt_num(m['input']):>14}")
    L.append("")
    L.append("【按工作空间 Top 8】")
    L.append(f"{'工作空间':<40}{'任务':>6}{'命中率':>10}{'输入token':>14}")
    for w in workspaces[:8]:
        name = w["key"] if len(w["key"]) <= 38 else w["key"][:35] + "..."
        L.append(f"{name:<40}{w['tasks']:>6}{w['hit_rate']:>9.1f}%{fmt_num(w['input']):>14}")
    L.append("")
    L.append("【命中率最低的任务 Top 5】")
    for t in sorted(tasks, key=lambda x: x["hit_rate"])[:5]:
        when = datetime.fromtimestamp(t["started"] / 1000).strftime("%m-%d %H:%M") if t["started"] else "-"
        L.append(f"  {when}  {t['hit_rate']:>6.1f}%  请求{t['calls']:>4}  "
                 f"输入{fmt_num(t['input']):>8}  {t['models'][:20]}")
    if anomalies:
        L.append("")
        L.append(f"【数据异常】{len(anomalies)} 条")
        kinds = {}
        for a in anomalies:
            kinds[a[0]] = kinds.get(a[0], 0) + 1
        for k, v in kinds.items():
            L.append(f"  {k}: {v}")
    L.append("")
    return "\n".join(L)


HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WorkBuddy 缓存命中率看板</title>
<style>
:root{--bg:#fafaf9;--card:#fff;--ink:#2c2c2a;--mute:#5f5e5a;--line:rgba(0,0,0,.12);
--accent:#534ab7;--good:#3b6d11;--warn:#a32d2d;--soft:#eeedfe}
@media(prefers-color-scheme:dark){:root{--bg:#1c1c1b;--card:#262625;--ink:#f0efec;--mute:#b4b2a9;
--line:rgba(255,255,255,.14);--accent:#afa9ec;--good:#97c459;--warn:#f09595;--soft:#2f2b52}}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:var(--bg);color:var(--ink);
font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
h1{font-size:16px;font-weight:500;margin:0 0 4px}
.sub{color:var(--mute);font-size:12px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:.5px solid var(--line);border-radius:10px;padding:14px 16px}
.card .lbl{color:var(--mute);font-size:12px;margin-bottom:6px}
.card .val{font-size:22px;font-weight:500;line-height:1.2}
.card .hint{color:var(--mute);font-size:11px;margin-top:4px}
.bar{height:6px;border-radius:3px;background:var(--soft);overflow:hidden;margin-top:8px}
.bar>i{display:block;height:100%;background:var(--accent)}
.bar.low>i{background:var(--warn)}
.sec{margin:20px 0 8px;font-size:14px;font-weight:500;
position:sticky;top:0;z-index:8;background:var(--bg);padding:8px 0}
table{width:100%;border-collapse:collapse;background:var(--card);
border:.5px solid var(--line);border-radius:10px;overflow:hidden;font-size:12px}
th,td{padding:8px 10px;text-align:left;border-bottom:.5px solid var(--line);white-space:nowrap}
th{color:var(--mute);font-weight:500;cursor:pointer;user-select:none;font-size:11px}
th:hover{color:var(--ink)}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.low td{background:rgba(163,45,45,.06)}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;
background:var(--soft);color:var(--accent)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:1px}
/* 关键：给表格一个限高的滚动框。若让表格自然撑开，横向滚动条会落到
   247 行之后、页面最底部，在预览面板里永远看不到——用户只能看到被裁掉的列。 */
.wrap{max-height:min(46vh,420px);overflow:auto;overscroll-behavior:contain;
border:.5px solid var(--line);border-radius:10px;background:var(--card);cursor:grab}
.wrap.grabbing{cursor:grabbing;user-select:none}
.wrap::-webkit-scrollbar{height:12px;width:12px}
.wrap::-webkit-scrollbar-track{background:var(--soft);border-radius:6px}
.wrap::-webkit-scrollbar-thumb{background:#b9b9b9;border-radius:6px;border:2px solid var(--soft)}
.wrap::-webkit-scrollbar-thumb:hover{background:#9a9a9a}
.wrap{scrollbar-width:thin;scrollbar-color:#b9b9b9 transparent}
/* 10 列在窄窗口会被压扁，强制最小宽度让容器产生横向溢出 */
#tbl{min-width:980px;border:none;border-radius:0;overflow:visible}
/* 表头吸顶：限高滚动时仍能看到列名 */
#tbl thead th{position:sticky;top:0;z-index:3;background:var(--card);
box-shadow:inset 0 -1px 0 var(--line)}
/* 横向滚动时首列吸附，便于对照是哪一个任务 */
#tbl th:first-child,#tbl td:first-child{position:sticky;left:0;z-index:2;
background:var(--card);box-shadow:1px 0 0 var(--line)}
#tbl thead th:first-child{z-index:4}
.hd{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.navbtn{background:var(--card);border:.5px solid var(--line);border-radius:6px;
padding:0 7px;height:20px;line-height:18px;font-size:12px;color:var(--mute);
cursor:pointer;user-select:none}
.navbtn:hover{color:var(--accent);border-color:var(--accent)}
.ctrl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.btn{background:var(--card);border:.5px solid var(--line);border-radius:8px;padding:5px 11px;
font-size:12px;color:var(--mute);cursor:pointer}
.btn.on{background:var(--soft);color:var(--accent);border-color:var(--accent)}
.legend{color:var(--mute);font-size:11px;margin-top:10px}
.hint-scroll{margin-left:8px;font-size:11px;color:var(--mute);border:1px dashed var(--line);
padding:1px 7px;border-radius:6px;white-space:nowrap}
</style></head><body>
<h1>WorkBuddy 缓存命中率看板</h1>
<div class="sub" id="sub"></div>
<div class="grid" id="cards"></div>
<div class="ctrl" id="ctrl"></div>
<div class="sec"><span class="hd">任务明细 <span class="legend" id="count"></span>
<span class="navbtn" id="tblL" title="向左">◀</span>
<span class="navbtn" id="tblR" title="向右">▶</span>
<span class="hint-scroll">拖动可左右查看</span></span></div>
<div class="wrap" id="wrap"><table id="tbl"><thead><tr>
<th data-k="started">开始时间</th><th data-k="workspace">工作空间</th><th data-k="models">模型</th>
<th data-k="calls" class="num">请求</th><th data-k="input" class="num">输入</th>
<th data-k="cache_read" class="num">命中</th><th data-k="uncached" class="num">未命中</th>
<th data-k="output" class="num">输出</th>
<th data-k="hit_rate" class="num">命中率</th><th data-k="duration_s" class="num">耗时(s)</th>
</tr></thead><tbody></tbody></table></div>
<div class="legend">口径：命中率 = 缓存命中 token / 输入 token；一次任务 = 一个 traceId 下的全部调用。
「整体命中率」为当前筛选范围内所有请求的汇总比值（默认全部历史），与「任务命中率 P50」（各任务命中率的中位数）不是同一统计量。
低于 __THRESHOLD__% 的任务标红。生成于 __GEN__。</div>
<script>
const DATA = __DATA__;
/* 工作空间显示名：只取目录名末段，不依赖任何本机命名习惯 */
function wsLabel(w){
  const b=String(w||'').replace(/\\/g,'/').split('/').filter(Boolean).pop()||'';
  return b.length>20?'…'+b.slice(-19):b;
}
let sortKey='started', asc=false, range='all', ws='all';
function fmt(n){return n>=1e8?(n/1e8).toFixed(2)+'亿':n>=1e4?(n/1e4).toFixed(1)+'万':n.toLocaleString()}
function ts(ms){if(!ms)return '-';const d=new Date(ms);const p=x=>String(x).padStart(2,'0');
return (d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes())}
function filtered(){
  const now=Date.now(); const limits={all:0,d1:864e5,d7:6048e5,d30:2592e6};
  return DATA.tasks.filter(t=>{
    if(limits[range] && (now-t.started)>limits[range]) return false;
    if(ws!=='all' && t.workspace!==ws) return false;
    return true;
  });
}
function sum(ts){
  const s={tasks:ts.length,calls:0,input:0,output:0,cache_read:0,uncached:0,low:0};
  ts.forEach(t=>{s.calls+=t.calls;s.input+=t.input;s.output+=t.output;
    s.cache_read+=t.cache_read;s.uncached+=t.uncached;if(t.low_hit)s.low++});
  s.hit_rate=s.input?+(s.cache_read/s.input*100).toFixed(2):0;
  const r=ts.map(t=>t.hit_rate).sort((a,b)=>a-b);
  s.p50=r.length?r[Math.floor(r.length/2)]:0;
  return s;
}
function cards(ts){
  const s=sum(ts);
  const items=[
    ['整体命中率',s.hit_rate.toFixed(2)+'%','命中 '+fmt(s.cache_read)+' / 输入 '+fmt(s.input),s.hit_rate],
    ['输入 token',fmt(s.input),'命中 '+fmt(s.cache_read)+' + 未命中 '+fmt(s.uncached),s.hit_rate],
    ['任务数',s.tasks,'共 '+s.calls.toLocaleString()+' 次请求',null],
    ['未命中 token',fmt(s.uncached),'实际参与计算的输入',null],
    ['输出 token',fmt(s.output),'输出总量，不参与命中率计算',null],
    ['任务命中率 P50',s.p50.toFixed(1)+'%','中位数',null],
    ['低命中任务',s.low,'低于 90% 的告警项',null]
  ];
  document.getElementById('cards').innerHTML=items.map(([l,v,h,b])=>`
   <div class="card"><div class="lbl">${l}</div><div class="val">${v}</div>
   ${b!==null?`<div class="bar ${b<90?'low':''}"><i style="width:${Math.min(b,100)}%"></i></div>`:''}
   <div class="hint">${h}</div></div>`).join('');
  document.getElementById('sub').textContent=
    `数据源 ~/.workbuddy/projects/**/*.jsonl · ${DATA.tasks.length} 个任务已入库 · 当前筛选 ${s.tasks} 个`;
}
function render(){
  const rows=filtered().sort((a,b)=>{
    let x=a[sortKey],y=b[sortKey];
    if(sortKey==='started'||sortKey==='duration_s'){x=+x;y=+y}
    return (x>y?1:x<y?-1:0)*(asc?1:-1);
  });
  document.getElementById('count').textContent=`（${rows.length} 条）`;
  document.querySelector('#tbl tbody').innerHTML=rows.map(t=>`
   <tr class="${t.low_hit?'low':''}">
    <td>${ts(t.started)}</td>
    <td>${wsLabel(t.workspace)}</td>
    <td>${t.models}</td>
    <td class="num">${t.calls}</td>
    <td class="num">${fmt(t.input)}</td>
    <td class="num">${fmt(t.cache_read)}</td>
    <td class="num">${fmt(t.uncached)}</td>
    <td class="num">${fmt(t.output)}</td>
    <td class="num"><span class="dot" style="background:${t.low_hit?'#a32d2d':'#3b6d11'}"></span>${t.hit_rate.toFixed(1)}%</td>
    <td class="num">${t.duration_s}</td>
   </tr>`).join('');
  cards(rows);
  if(window.__syncNav) window.__syncNav();   // 筛选后行数变化，重算按钮可用态
}
function buildCtrl(){
  const rng=[['all','全部'],['d30','30天'],['d7','7天'],['d1','24小时']];
  const wsList=[...new Set(DATA.tasks.map(t=>t.workspace))].sort();
  document.getElementById('ctrl').innerHTML=
    rng.map(([k,l])=>`<button class="btn ${k===range?'on':''}" data-r="${k}">${l}</button>`).join('')
    +`<select class="btn" id="wsel"><option value="all">全部工作空间</option>`+
    wsList.map(w=>`<option value="${w}">${wsLabel(w)}</option>`).join('')+`</select>`;
  document.querySelectorAll('[data-r]').forEach(b=>b.onclick=()=>{
    range=b.dataset.r;buildCtrl();render()});
  const sel=document.getElementById('wsel');sel.value=ws;
  sel.onchange=e=>{ws=e.target.value;render()};
}
document.querySelectorAll('#tbl th').forEach(th=>th.onclick=()=>{
  const k=th.dataset.k; if(sortKey===k)asc=!asc; else {sortKey=k;asc=false} render()});
buildCtrl();render();

/* 横向查看：三路冗余入口，任一可用即达成目的
   ① 限高滚动框自带的横向条（始终贴在框底，不会被长表格顶到页尾）
   ② 按住表格左右拖动
   ③ 标题行 ◀ ▶ 按钮，每次移动约 0.7 个可视宽度 */
const wrapEl=document.getElementById('wrap');
const stepX=()=>Math.max(240,Math.round(wrapEl.clientWidth*0.7));
document.getElementById('tblL').onclick=()=>wrapEl.scrollBy({left:-stepX(),behavior:'smooth'});
document.getElementById('tblR').onclick=()=>wrapEl.scrollBy({left:stepX(),behavior:'smooth'});
/* 无法横向滚动时按钮置灰，避免误导 */
function syncNav(){
  const room=wrapEl.scrollWidth-wrapEl.clientWidth;
  document.getElementById('tblL').style.opacity=wrapEl.scrollLeft>2?1:.35;
  document.getElementById('tblR').style.opacity=wrapEl.scrollLeft<room-2?1:.35;
}
wrapEl.addEventListener('scroll',syncNav,{passive:true});
window.__syncNav=syncNav;
let dg=false,sx0=0,sl0=0;
wrapEl.addEventListener('pointerdown',e=>{
  if(e.target.closest('th'))return;           // 表头留给排序
  dg=true;sx0=e.clientX;sl0=wrapEl.scrollLeft;
});
window.addEventListener('pointermove',e=>{
  if(!dg)return;
  const dx=e.clientX-sx0;
  if(Math.abs(dx)>3)wrapEl.classList.add('grabbing');
  wrapEl.scrollLeft=sl0-dx;
});
window.addEventListener('pointerup',()=>{
  if(!dg)return;
  dg=false;setTimeout(()=>wrapEl.classList.remove('grabbing'),0);
});
syncNav();
</script></body></html>"""


def render_html(tasks, summary, series, models, workspaces, range_name):
    payload = {
        "tasks": tasks,
        "summary": summary,
        "series": series,
        "models": models,
        "workspaces": workspaces,
        "range": range_name,
    }
    html = (HTML_TMPL
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
            .replace("__THRESHOLD__", str(int(LOW_HIT_THRESHOLD)))
            .replace("__GEN__", datetime.now().strftime("%Y-%m-%d %H:%M")))
    return html


def main():
    range_token = "all"
    workspace = None
    out = None
    fmt = "text"
    for i, a in enumerate(sys.argv[1:]):
        if a in ("--range", "-r"):
            range_token = sys.argv[i + 2]
        elif a in ("--workspace", "-w"):
            workspace = sys.argv[i + 2]
        elif a in ("--out", "-o"):
            out = sys.argv[i + 2]
        elif a in ("--json",):
            fmt = "json"
        elif a in ("--html",):
            fmt = "html"

    range_name, days = parse_range(range_token)
    db = connect()
    tasks = load_tasks(db, days, workspace)
    summary = summarize(tasks)
    models = by_dimension(tasks, "models")
    workspaces = by_dimension(tasks, "workspace")
    series = daily_series(db, days if days else 365)
    anomalies = db.execute(
        "SELECT kind, path, detail FROM anomalies ORDER BY id DESC LIMIT 50").fetchall()
    db.close()

    if fmt == "html":
        path = out or str(Path.cwd() / "wb-cache-dashboard.html")
        Path(path).write_text(
            render_html(tasks, summary, series, models, workspaces, range_name), encoding="utf-8")
        print(path)
        return

    if fmt == "json":
        print(json.dumps({"summary": summary, "tasks": tasks, "models": models,
                          "workspaces": workspaces, "series": series},
                         ensure_ascii=False, indent=2))
        return

    print(render_text(tasks, summary, range_name, models, workspaces, anomalies))


if __name__ == "__main__":
    main()
