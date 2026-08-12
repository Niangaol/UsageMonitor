# -*- coding: utf-8 -*-
"""dashboard.py — 本地网页仪表盘（v3：三视图）。

- 仅监听 127.0.0.1（不做远程访问），默认端口 8765；
- 纯标准库（http.server），页面与图表内联，零外部依赖、离线可用；
- 数据全部来自本机日期文件夹，不产生任何新数据。

视图：
1. 今日概览：大数字卡片（总活跃/AI编程/社交/浏览器停留/会话数）+ 24 小时活跃分布
   + 14/30 天趋势 + 类别/应用分布 + AI 工具/联系人（鼠标悬停看详情）
2. 日报：选日期渲染当日 report.md（前端 mini-markdown，含表格/标题/列表/代码块）
3. 明细：会话明细与浏览器 URL 明细（均支持关键词过滤）

用法：
    python dashboard.py                # 启动，浏览器访问 http://127.0.0.1:8765
    python dashboard.py --port 9000    # 指定端口
    python dashboard.py --open         # 启动后自动打开浏览器
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import report  # noqa: E402
import version  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_DATA_ROOT = "D:\\电脑使用情况监控"

# API 日期参数白名单：防路径穿越（date=../../xxx 会拼进数据目录路径）
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_URL_MAX_ROWS = 200  # 浏览器明细最多回传条数


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>电脑使用情况监控 · 本地仪表盘</title>
<!-- 主题预置（防闪烁）：localStorage 手动选择优先，否则跟随系统 -->
<script>
try{
  var _t = localStorage.getItem('dashTheme');
  if(_t && _t!=='auto'){ document.documentElement.dataset.theme = _t; }
  else if(window.matchMedia('(prefers-color-scheme: light)').matches){ document.documentElement.dataset.theme = 'light'; }
}catch(e){}
</script>
<style>
  :root { color-scheme: dark; --bg:#0f1115; --card:#171a21; --border:#262b36; --fg:#e6e9ef;
          --dim:#8b93a3; --accent:#4f8cff; --green:#3fbf7f; --red:#e05c5c;
          --amber:#e8a33d; --purple:#a06bff; --pink:#e05c8c;
          --bar2:#7ab0ff; --grid:#232936; --axis:#5a6272; --axis2:#8b93a3;
          --cell-empty:#232936; --tt-bg:rgba(13,16,22,0.95); --tt-border:#3a4252; --tt-fg:#e6e9ef; }
  /* 浅色主题（data-theme=light）：跟随系统 / 手动切换 / 按时间自动 */
  :root[data-theme="light"] { color-scheme: light; --bg:#f2f4f8; --card:#ffffff; --border:#dfe3ea; --fg:#1f2733;
          --dim:#6b7280; --accent:#2563eb; --green:#16a34a; --red:#dc2626;
          --amber:#d97706; --purple:#7c3aed; --pink:#db2777;
          --bar2:#93c5fd; --grid:#dbe1ea; --axis:#9aa3b0; --axis2:#6b7280;
          --cell-empty:#e8ecf2; --tt-bg:rgba(255,255,255,0.97); --tt-border:#c9d2de; --tt-fg:#1f2733; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--fg); font-family:"Microsoft YaHei","Segoe UI",sans-serif; padding:24px; }
  h1 { font-size:20px; font-weight:600; }
  .sub { color:var(--dim); font-size:12px; margin-top:4px; }
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:16px; }
  .controls { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  select, button, input { background:var(--card); color:var(--fg); border:1px solid var(--border);
    border-radius:6px; padding:6px 10px; font-size:13px; cursor:pointer; outline:none; }
  button:hover, select:hover { border-color:var(--accent); }
  button.active { border-color:var(--accent); color:var(--accent); }
  input[type=text] { cursor:text; width:200px; }
  input::placeholder { color:#5a6272; }
  .tabs { display:flex; gap:4px; background:var(--card); border:1px solid var(--border); border-radius:8px; padding:3px; }
  .tab { border:none; background:transparent; border-radius:6px; padding:6px 16px; font-size:13px; }
  .tab:hover { border:none; background:#1d2533; }
  .tab.active { background:var(--accent); color:#fff; border:none; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:16px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
  .card .label { color:var(--dim); font-size:12px; display:flex; align-items:center; gap:6px; }
  .card .value { font-size:24px; font-weight:600; margin-top:6px; }
  .card .value.small { font-size:18px; }
  .card .extra { color:var(--dim); font-size:11px; margin-top:4px; }
  .dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
  @media (max-width:900px){ .grid{ grid-template-columns:1fr; } }
  .panel { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:12px; }
  .panel h2 { font-size:14px; font-weight:600; margin-bottom:12px; display:flex; align-items:center; justify-content:space-between; }
  .panel h2 .hint { color:var(--dim); font-size:11px; font-weight:400; }
  canvas { width:100%; height:200px; display:block; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); white-space:nowrap; }
  th { color:var(--dim); font-weight:500; position:sticky; top:0; background:var(--card); }
  tr:hover td { background:#1d222c; }
  :root[data-theme="light"] tr:hover td { background:#eef1f6; }
  .bar { height:14px; background:linear-gradient(90deg,var(--accent),var(--bar2)); border-radius:3px; }
  .tag { display:inline-block; background:#1d2533; border:1px solid var(--border); border-radius:4px;
         padding:1px 6px; font-size:11px; color:var(--dim); margin-left:6px; }
  .footer { color:var(--dim); font-size:11px; margin-top:16px; text-align:center; }
  .empty { color:var(--dim); padding:24px 0; text-align:center; }
  .scroll { max-height:340px; overflow-y:auto; }
  .pill { display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; border:1px solid var(--border); }
  .pill-video { color:var(--pink); border-color:var(--pink); }
  .pill-code { color:var(--green); border-color:var(--green); }
  .pill-learn { color:var(--amber); border-color:var(--amber); }
  .pill-other { color:var(--dim); }
  .url-cell { white-space:normal; word-break:break-all; max-width:380px; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .hidden { display:none; }
  /* 日报 markdown 渲染 */
  .md-body { font-size:13.5px; line-height:1.75; }
  .md-body h2 { font-size:15px; margin:18px 0 8px; color:var(--fg); border-bottom:1px solid var(--border); padding-bottom:6px; }
  .md-body h3 { font-size:14px; margin:14px 0 6px; }
  .md-body p { margin:6px 0; color:#c9cfda; }
  .md-body table { margin:8px 0 14px; border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .md-body th { background:#1d2533; }
  .md-body td, .md-body th { border-right:1px solid var(--border); }
  .md-body td:last-child, .md-body th:last-child { border-right:none; }
  .md-body code { background:#1d2533; border:1px solid var(--border); border-radius:4px; padding:1px 5px; font-size:12px; }
  .md-body pre { background:#0d1016; border:1px solid var(--border); border-radius:8px; padding:12px; overflow-x:auto; margin:8px 0; }  .md-body pre code { background:none; border:none; padding:0; }
  .md-body ul, .md-body ol { margin:6px 0 6px 20px; }
  .md-body li { margin:3px 0; }
  .md-body blockquote { border-left:3px solid var(--accent); padding-left:10px; color:var(--dim); margin:8px 0; }
  .legend { display:flex; gap:6px; align-items:center; font-size:11px; color:var(--dim); margin-top:8px; }
  .legend span { display:inline-flex; align-items:center; }
  .lg-cell { width:12px; height:12px; border-radius:3px; display:inline-block; margin:0 1px; }
</style>
</head>
<body>
<div class="topbar">
  <div>
    <h1>电脑使用情况监控</h1>
    <div class="sub">数据仅存本机 · 仅监听 127.0.0.1 · 不联网</div>
  </div>
  <div class="tabs">
    <button class="tab active" data-tab="overview" onclick="switchTab('overview')">今日概览</button>
    <button class="tab" data-tab="report" onclick="switchTab('report')">日报</button>
    <button class="tab" data-tab="detail" onclick="switchTab('detail')">明细</button>
    <button class="tab" data-tab="heatmap" onclick="switchTab('heatmap')">热力图</button>
  </div>
  <div class="controls">
    <select id="daySelect" onchange="loadDay(this.value)"></select>
    <button onclick="loadDay(todayStr())">今天</button>
    <button id="rangeBtn" onclick="toggleRange()">近 14 天</button>
    <button id="themeBtn" onclick="cycleTheme()" title="主题：自动跟随系统 / 浅色 / 深色">🌗 自动</button>
  </div>
</div>

<!-- ============ 今日概览 ============ -->
<section id="view-overview">
  <div class="cards">
    <div class="card"><div class="label"><span class="dot" style="background:var(--accent)"></span>总活跃时长</div><div class="value" id="cTotal">-</div><div class="extra" id="cTotalSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--purple)"></span>AI 编程时长</div><div class="value" id="cAi">-</div><div class="extra" id="cAiSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--amber)"></span>社交聊天</div><div class="value" id="cSocial">-</div><div class="extra" id="cSocialSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--pink)"></span>浏览器停留</div><div class="value" id="cBrowser">-</div><div class="extra" id="cBrowserSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--green)"></span>会话数</div><div class="value small" id="cCount">-</div><div class="extra" id="cCountSub"></div></div>
  </div>

  <div class="grid">
    <div class="panel"><h2>当日 24 小时活跃分布 <span class="hint">悬停看分钟数 · 跨小时已精确分摊</span></h2><canvas id="hourly" width="640" height="200"></canvas></div>
    <div class="panel"><h2>活跃趋势 <span class="hint" id="trendHint"></span></h2><canvas id="trend" width="640" height="200"></canvas></div>
  </div>

  <div class="grid">
    <div class="panel"><h2>当日类别分布 <span class="hint">含占比</span></h2><div id="catBars"></div></div>
    <div class="panel"><h2>当日应用 Top 15</h2><div id="appBars"></div></div>
  </div>

  <div class="grid">
    <div class="panel"><h2>AI 工具 / 联系人</h2><div id="misc"></div></div>
    <div class="panel"><h2>浏览器访问 <span class="hint">分类 / 域名停留 Top 5</span></h2><div id="urlSummary"></div></div>
  </div>
</section>

<!-- ============ 日报 ============ -->
<section id="view-report" class="hidden">
  <div class="panel">
    <h2><span>日报</span><span class="hint" id="reportHint">自动生成的 report.md · 与日期文件夹内文件一致</span></h2>
    <div id="reportMd" class="md-body"><div class="empty">加载中…</div></div>
  </div>
</section>

<!-- ============ 明细 ============ -->
<section id="view-detail" class="hidden">
  <div class="panel"><h2>会话明细 <span class="hint">按应用 / 标题过滤 · 最多 200 条</span></h2>
    <div style="margin-bottom:8px"><input type="text" id="sessFilter" placeholder="过滤应用名 / 窗口标题…" oninput="renderSessions()"></div>
    <div class="scroll"><table><thead><tr><th>开始</th><th>结束</th><th>秒数</th><th>应用</th><th>标题</th><th>类别</th><th>备注</th></tr></thead>
    <tbody id="sessBody"></tbody></table></div>
  </div>
  <div class="panel"><h2>浏览器访问明细 <span class="hint" id="urlHint"></span></h2>
    <div style="margin-bottom:8px"><input type="text" id="urlFilter" placeholder="过滤域名 / 标题关键词…" oninput="renderUrls()"></div>
    <div class="scroll"><table><thead><tr><th>时间</th><th>分类</th><th>域名</th><th>停留</th><th>页面</th></tr></thead>
    <tbody id="urlBody"></tbody></table></div>
  </div>
</section>

<!-- ============ 热力图 ============ -->
<section id="view-heatmap" class="hidden">
  <div class="cards">
    <div class="card"><div class="label"><span class="dot" style="background:var(--green)"></span>活跃天数</div><div class="value small" id="hmDays">-</div><div class="extra" id="hmDaysSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--amber)"></span>最长连续活跃</div><div class="value small" id="hmStreak">-</div><div class="extra">连续使用天数</div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--accent)"></span>日均活跃</div><div class="value small" id="hmAvg">-</div><div class="extra" id="hmAvgSub"></div></div>
    <div class="card"><div class="label"><span class="dot" style="background:var(--purple)"></span>近 7 天总时长</div><div class="value small" id="hmWeek">-</div><div class="extra" id="hmWeekSub"></div></div>
  </div>

  <div class="panel"><h2>每日活跃热力图 <span class="hint">GitHub 风格 · 最近 12 周 · 颜色越深越活跃 · 悬停看详情</span></h2>
    <canvas id="heatGit" width="900" height="190"></canvas>
    <div class="legend"><span>少</span><span id="lgGit0"></span><span id="lgGit1"></span><span id="lgGit2"></span><span id="lgGit3"></span><span id="lgGit4"></span><span>多</span></div>
  </div>

  <div class="panel"><h2>小时级活跃热力图 <span class="hint">X=日期 · Y=小时 · 颜色越深该时段用电脑越久</span>
    <button id="hRangeBtn" onclick="toggleHHeat()">近 14 天</button></h2>
    <canvas id="heatHour" width="900" height="460"></canvas>
    <div class="legend"><span>少</span><span id="lgHour0"></span><span id="lgHour1"></span><span id="lgHour2"></span><span id="lgHour3"></span><span id="lgHour4"></span><span>多</span></div>
  </div>
</section>

<div class="footer">本页面由 dashboard.py 提供 · 数据目录：<span id="dataRoot"></span></div>

<script>
const $ = id => document.getElementById(id);
let RANGE_DAYS = 14;
let CUR = { agg:null, hourly:null, urls:null, trend:null, date:null };
function todayStr(){ const d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function fmtMs(ms){ if(ms==null) return '-'; const s=Math.floor(ms/1000); const h=Math.floor(s/3600), m=Math.floor(s%3600/60), sec=s%60;
  let p=[]; if(h)p.push(h+' 小时'); if(m)p.push(m+' 分钟'); if(!h&&!m&&sec)p.push(sec+' 秒'); if(!p.length)p.push('0 秒'); return p.join(' '); }
function fmtMin(ms){ if(ms==null) return '-'; return (ms/60000).toFixed(1)+' 分钟'; }
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function barList(obj, fmt, maxN){
  const entries = Object.entries(obj||{}).sort((a,b)=>b[1]-a[1]).slice(0, maxN||99);
  if(!entries.length) return '<div class="empty">（无数据）</div>';
  const max = Math.max(...entries.map(e=>e[1]));
  return entries.map(([k,v])=>{
    const pct = max>0 ? Math.round(v/max*100) : 0;
    return '<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;font-size:12px">'
      + '<span style="overflow:hidden;text-overflow:ellipsis;max-width:65%">'+esc(k)+'</span>'
      + '<span style="color:var(--dim);flex-shrink:0">'+fmt(v)+'</span></div>'
      + '<div class="bar" style="width:'+Math.max(2,pct)+'%"></div></div>';
  }).join('');
}

// ---- Tab 切换 ----
function switchTab(tab){
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active', b.dataset.tab===tab));
  ['overview','report','detail','heatmap'].forEach(v=>{
    $('view-'+v).classList.toggle('hidden', v!==tab);
  });
  if(tab==='report') loadReport();
  if(tab==='detail') { renderSessions(); renderUrls(); }
  if(tab==='heatmap') loadHeatmap();
  history.replaceState(null,'','?view='+tab);
}

// ---- 主题（自动跟随系统 / 浅色 / 深色）----
const THEME_COLORS = {
  dark:  { bar:'#4f8cff', barHi:'#7ab0ff', grid:'#232936', axis:'#5a6272', axis2:'#8b93a3',
           heat:['#232936','#123d28','#1d6b3a','#2a9d52','#3fd47a'] },
  light: { bar:'#2563eb', barHi:'#60a5fa', grid:'#dbe1ea', axis:'#9aa3b0', axis2:'#6b7280',
           heat:['#e8ecf2','#d3eee0','#8ad3ab','#43ab76','#1e8e56'] },
};
function curTheme(){ return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'; }
function curColors(){ return THEME_COLORS[curTheme()]; }
function themeMode(){ try{ return localStorage.getItem('dashTheme') || 'auto'; }catch(e){ return 'auto'; } }
function applyTheme(){
  const mode = themeMode();
  const sysLight = window.matchMedia('(prefers-color-scheme: light)').matches;
  const theme = mode==='auto' ? (sysLight ? 'light' : 'dark') : mode;
  document.documentElement.dataset.theme = theme;
  $('themeBtn').textContent = mode==='auto' ? '🌗 自动' : (mode==='light' ? '☀️ 浅色' : '🌙 深色');
  redrawCharts();
}
function cycleTheme(){
  const order = ['auto','light','dark'];
  const next = order[(order.indexOf(themeMode())+1) % 3];
  try{ localStorage.setItem('dashTheme', next); }catch(e){}
  applyTheme();
}
function redrawCharts(){
  if(CUR.hourly && CUR.hourly.length){
    drawBars($('hourly'), (CUR.hourly||[]).map((v,i)=>({v,label:i})), i=>String(i).padStart(2,'0')+':00', fmtMin, it=>it.label+':00');
  }
  if(CUR.trend && CUR.trend.length){
    drawBars($('trend'), CUR.trend.map(d=>({v:d.total_ms,label:d.date})), i=>CUR.trend[i].date.slice(5), ms=>fmtMs(ms), it=>it.label);
  }
  if(HEAT_CACHE && HEAT_CACHE.length){ drawHeatGit($('heatGit')); drawHeatHour($('heatHour')); }
}
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', ()=>{ if(themeMode()==='auto') applyTheme(); });

// ---- canvas 图表（高分屏 2x + 悬停 tooltip）----
function setupCanvas(c){
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  c.width = w*dpr; c.height = h*dpr;
  const ctx = c.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
  return {ctx, w, h};
}
function drawBars(canvas, items, labels, fmt, tipKey){
  const C = curColors();
  const {ctx, w, h} = setupCanvas(canvas);
  const padL=38, padB=22, padT=8;
  const max = Math.max(1, ...items.map(i=>i.v));
  const bw = (w-padL-6)/items.length;
  const data = [];
  const paint = (hlIt)=>{
    ctx.clearRect(0,0,w,h);
    items.forEach((it,i)=>{
      const x = padL+i*bw, bh = Math.max(2, it.v/max*(h-padT-padB));
      const y = h-padB-bh;
      ctx.fillStyle = it.v>0 ? (hlIt===it ? C.barHi : C.bar) : C.grid;
      ctx.fillRect(x+1.5, y, Math.max(2,bw-3), bh);
    });
    ctx.strokeStyle = C.grid; ctx.fillStyle = C.axis; ctx.font='10px sans-serif';
    [0,0.5,1].forEach(f=>{
      const yy = h-padB - f*(h-padT-padB);
      ctx.beginPath(); ctx.moveTo(padL,yy); ctx.lineTo(w-2,yy); ctx.stroke();
      ctx.fillText(fmt(max*f), 2, yy+3);
    });
    ctx.fillStyle = C.axis2; ctx.textAlign = 'center';
    const step = Math.max(1, Math.ceil(items.length/12));
    items.forEach((it,i)=>{ if(i%step===0) ctx.fillText(labels(i), padL+i*bw+bw/2, h-8); });
  };
  items.forEach((it,i)=>{ data.push({x:padL+i*bw, w:bw, it}); });
  paint(null);
  canvas.onmousemove = e=>{
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX-r.left;
    let hit = null;
    for(const d of data){ if(mx>=d.x && mx<=d.x+d.w) { hit = d; break; } }
    paint(hit ? hit.it : null);
    if(hit){
      const tip = tipKey(hit.it);
      const tw = 170, th = 34;
      const hh = Math.max(2, hit.it.v/max*(h-padT-padB));
      const hy = h-padB-hh;
      let tx = hit.x+hit.w/2-tw/2; if(tx<padL-20) tx=padL-20; if(tx+tw>w-4) tx=w-4-tw;
      const ty = hy-th-10 < 0 ? hy+10 : hy-th-10;
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-bg').trim();
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-border').trim();
      ctx.beginPath(); if(ctx.roundRect) ctx.roundRect(tx,ty,tw,th,6); else ctx.rect(tx,ty,tw,th); ctx.fill(); ctx.stroke();
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-fg').trim();
      ctx.font = '12px "Microsoft YaHei",sans-serif'; ctx.textAlign='center';
      ctx.fillText(tip, tx+tw/2, ty+13);
      ctx.fillStyle = C.axis2; ctx.font = '10px "Microsoft YaHei",sans-serif';
      ctx.fillText(fmt(hit.it.v), tx+tw/2, ty+26);
    }
  };
  canvas.onmouseleave = ()=>paint(null);
}

// ---- mini-markdown 渲染（日报用，零依赖）----
function mdInline(s){
  return esc(s)
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}
function renderMarkdown(md){
  const lines = String(md||'').split('\\n');
  let html = '', i = 0, inCode = false, codeBuf = [];
  const flushTable = rows =>{
    if(!rows.length) return '';
    let out = '<table><thead><tr>';
    const head = rows[0].map(c=>'<th>'+mdInline(c.trim())+'</th>').join('');
    out += head + '</tr></thead><tbody>';
    for(let r=1;r<rows.length;r++){
      out += '<tr>' + rows[r].map(c=>'<td>'+mdInline(c.trim())+'</td>').join('') + '</tr>';
    }
    return out + '</tbody></table>';
  };
  while(i < lines.length){
    const line = lines[i];
    const t = line.trim();
    if(t.startsWith('```')){
      if(!inCode){ inCode = true; codeBuf = []; }
      else { html += '<pre><code>'+esc(codeBuf.join('\\n'))+'</code></pre>'; inCode = false; }
      i++; continue;
    }
    if(inCode){ codeBuf.push(line); i++; continue; }
    if(t.startsWith('#')){
      const lvl = t.match(/^#+/)[0].length;
      html += `<h${Math.min(lvl,3)}>${mdInline(t.replace(/^#+\s*/,''))}</h${Math.min(lvl,3)}>`; i++; continue;
    }
    if(t.startsWith('|')){
      const rows = [];
      while(i < lines.length && lines[i].trim().startsWith('|')){
        const cells = lines[i].trim().replace(/^\||\|$/g,'').split('|');
        if(!(rows.length===1 && cells.every(c=>/^:?-{2,}:?$/.test(c.trim())))) rows.push(cells);
        i++;
      }
      html += flushTable(rows); continue;
    }
    if(/^[-*]\s+/.test(t) || /^\d+\.\s+/.test(t)){
      let items = [];
      while(i < lines.length && (/^[-*]\s+/.test(lines[i].trim()) || /^\d+\.\s+/.test(lines[i].trim()))){
        items.push(lines[i].trim().replace(/^[-*]\s+|\d+\.\s+/,''));
        i++;
      }
      html += '<ul>' + items.map(x=>'<li>'+mdInline(x)+'</li>').join('') + '</ul>';
      continue;
    }
    if(t.startsWith('>')){
      html += '<blockquote>'+mdInline(t.replace(/^>\s?/,''))+'</blockquote>'; i++; continue;
    }
    if(t === ''){ i++; continue; }
    html += '<p>'+mdInline(line)+'</p>';
    i++;
  }
  return html || '<div class="empty">（无内容）</div>';
}

// ---- 热力图（GitHub Contribution Graph 风格）----
const HEAT_DAYS = 84;          // GitHub 风格：最近 12 周
const WEEK_CN = ['周一','周二','周三','周四','周五','周六','周日'];
let HOUR_DAYS = 14;
let HEAT_CACHE = null;

function heatLevel(v, thresholds){
  // thresholds: 递增数组，返回 0..4 档
  let lv = 0;
  for(let i=0;i<thresholds.length;i++){ if(v >= thresholds[i]) lv = i+1; }
  return lv;
}
function parseDate(s){ const p = s.split('-'); return new Date(+p[0], +p[1]-1, +p[2]); }
function dayKey(d){ return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function msToHm(ms){
  const s = Math.floor(ms/1000), h = Math.floor(s/3600), m = Math.floor(s%3600/60);
  if(h && m) return h+' 小时 '+m+' 分';
  if(h) return h+' 小时';
  return m+' 分钟';
}

function drawHeatGit(canvas){
  const days = HEAT_CACHE || [];
  const C = curColors();
  const heat = C.heat;
  const {ctx, w, h} = setupCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  if(!days.length) return;
  // 最近 HEAT_DAYS 天，按日期字典序（升序）
  const list = days.slice(-HEAT_DAYS);
  const today = parseDate(todayStr());
  const first = parseDate(list[0].date);
  // 起点：第一天的周一
  const start = new Date(first);
  start.setDate(start.getDate() - ((start.getDay()+6)%7));
  const totalCols = Math.ceil((today - start)/86400000/7) + 1;
  const cell = Math.min(16, Math.floor((w-90)/totalCols));
  const px = 6;  // 格子间距
  const ox = 40, oy = 16;
  const monthLabels = {};
  const cells = [];
  const byDate = {};
  list.forEach(d=>{ byDate[d.date] = d; });
  // 月份标签：每月第一个周列
  for(let i=0;i<totalCols;i++){
    const d = new Date(start); d.setDate(d.getDate() + i*7);
    const m = d.getMonth();
    if(m !== (new Date(d.getFullYear(), d.getMonth(), 0)).getMonth()){
      monthLabels[i] = (d.getMonth()+1)+'月';
    }
  }
  ctx.font = '11px sans-serif'; ctx.fillStyle = C.axis2; ctx.textAlign = 'left';
  Object.entries(monthLabels).forEach(([i,lab])=>{ ctx.fillText(lab, ox + i*(cell+px), oy-4); });
  // 星期标签
  ctx.textAlign = 'right';
  for(let r=0;r<7;r++){
    if(r%2===0) ctx.fillText(WEEK_CN[r], ox-5, oy + r*(cell+px) + cell*0.75);
  }
  // 格子
  for(let c=0;c<totalCols;c++){
    for(let r=0;r<7;r++){
      const d = new Date(start);
      d.setDate(d.getDate() + c*7 + r);
      const key = dayKey(d);
      const data = byDate[key];
      const v = data ? data.total_ms : 0;
      const lv = data ? heatLevel(data.total_ms, [30*60000, 60*60000, 3*3600000, 6*3600000]) : 0;
      const x = ox + c*(cell+px), y = oy + r*(cell+px);
      ctx.fillStyle = heat[lv];
      ctx.fillRect(x, y, cell, cell);
      cells.push({x, y, w:cell, h:cell, key, v});
    }
  }
  // tooltip
  canvas.onmousemove = e=>{
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX-rect.left) * w / rect.width, my = (e.clientY-rect.top) * h / rect.height;
    let hit = null;
    for(const c of cells){ if(mx>=c.x && mx<=c.x+c.w && my>=c.y && my<=c.y+c.h){ hit=c; break; } }
    if(hit && hit.key){
      const d = parseDate(hit.key);
      const tip = hit.key + ' ' + WEEK_CN[(d.getDay()+6)%7] + ' · ' + (hit.v>0 ? msToHm(hit.v) : '未使用');
      const tw = 190, th = 26;
      let tx = hit.x+hit.w/2-tw/2; tx = Math.max(2, Math.min(tx, w-tw-2));
      const ty = hit.y - th - 8 < 0 ? hit.y + hit.h + 8 : hit.y - th - 8;
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-bg').trim();
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-border').trim();
      ctx.beginPath(); if(ctx.roundRect) ctx.roundRect(tx,ty,tw,th,6); else ctx.rect(tx,ty,tw,th); ctx.fill(); ctx.stroke();
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-fg').trim();
      ctx.font = '12px "Microsoft YaHei",sans-serif'; ctx.textAlign='center';
      ctx.fillText(tip, tx+tw/2, ty+17);
    }
  };
  canvas.onmouseleave = ()=>drawHeatGit(canvas);
}

function drawHeatHour(canvas){
  const days = HEAT_CACHE || [];
  const C = curColors();
  const heat = C.heat;
  const {ctx, w, h} = setupCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  if(!days.length) return;
  const list = days.slice(-HOUR_DAYS);
  const cell = Math.min(18, Math.floor((w-50)/list.length));
  const px = 3;
  const rowH = Math.min(15, Math.floor((h-30)/24));
  const rowPx = 2;
  const ox = 38, oy = 8;
  const cells = [];
  ctx.font = '10px sans-serif'; ctx.fillStyle = C.axis; ctx.textAlign = 'right';
  // Y 轴小时标签（隔 3 小时）
  for(let hr=0;hr<24;hr+=3){
    ctx.fillText(String(hr).padStart(2,'0')+':00', ox-5, oy + hr*(rowH+rowPx) + rowH*0.75);
  }
  ctx.textAlign = 'center';
  const step = Math.max(1, Math.ceil(list.length/10));
  list.forEach((d, i)=>{
    if(i%step===0) ctx.fillText(d.date.slice(5), ox + i*(cell+px) + cell/2, h-6);
    for(let hr=0;hr<24;hr++){
      const ms = (d.hourly_ms||[])[hr] || 0;
      const lv = heatLevel(ms/60000, [1, 5, 15, 30]);  // 分钟阈值
      const x = ox + i*(cell+px), y = oy + hr*(rowH+rowPx);
      ctx.fillStyle = heat[lv];
      ctx.fillRect(x, y, cell, rowH);
      cells.push({x, y, w:cell, h:rowH, date:d.date, hr, ms});
    }
  });
  canvas.onmousemove = e=>{
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX-rect.left) * w / rect.width, my = (e.clientY-rect.top) * h / rect.height;
    let hit = null;
    for(const c of cells){ if(mx>=c.x && mx<=c.x+c.w && my>=c.y && my<=c.y+c.h){ hit=c; break; } }
    if(hit){
      const tip = hit.date + ' ' + String(hit.hr).padStart(2,'0') + ':00 · ' + (hit.ms>0 ? msToHm(hit.ms) : '未使用');
      const tw = 180, th = 26;
      let tx = hit.x+hit.w/2-tw/2; tx = Math.max(2, Math.min(tx, w-tw-2));
      const ty = hit.y - th - 8 < 0 ? hit.y + hit.h + 8 : hit.y - th - 8;
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-bg').trim();
      ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-border').trim();
      ctx.beginPath(); if(ctx.roundRect) ctx.roundRect(tx,ty,tw,th,6); else ctx.rect(tx,ty,tw,th); ctx.fill(); ctx.stroke();
      ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--tt-fg').trim();
      ctx.font = '12px "Microsoft YaHei",sans-serif'; ctx.textAlign='center';
      ctx.fillText(tip, tx+tw/2, ty+17);
    }
  };
  canvas.onmouseleave = ()=>drawHeatHour(canvas);
}

async function loadHeatmap(){
  if(HEAT_CACHE) { drawHeatGit($('heatGit')); drawHeatHour($('heatHour')); return; }
  const d = await api('/api/heatmap?days=84');
  HEAT_CACHE = d.days || [];
  const days = HEAT_CACHE;
  // 图例
  const heat = curColors().heat;
  for(let i=0;i<5;i++){
    $('lgGit'+i).innerHTML = '<span class="lg-cell" style="background:'+heat[i]+'"></span>';
    $('lgHour'+i).innerHTML = '<span class="lg-cell" style="background:'+heat[i]+'"></span>';
  }
  // 统计
  const active = days.filter(x=>x.total_ms>0);
  $('hmDays').textContent = active.length;
  $('hmDaysSub').textContent = '共 ' + days.length + ' 天';
  // 最长连续活跃
  let streak = 0, cur = 0;
  for(let i=0;i<days.length;i++){
    if(days[i].total_ms>0){ cur++; streak = Math.max(streak, cur); } else { cur = 0; }
  }
  $('hmStreak').textContent = streak + ' 天';
  // 日均
  const sum = active.reduce((s,x)=>s+x.total_ms, 0);
  $('hmAvg').textContent = msToHm(days.length ? sum/days.length : 0);
  $('hmAvgSub').textContent = '按 ' + days.length + ' 天平均（含未使用日）';
  // 近 7 天
  const wk = days.slice(-7).reduce((s,x)=>s+x.total_ms, 0);
  $('hmWeek').textContent = msToHm(wk);
  $('hmWeekSub').textContent = days.slice(-7).filter(x=>x.total_ms>0).length + ' 天有使用';
  drawHeatGit($('heatGit'));
  drawHeatHour($('heatHour'));
}
function toggleHHeat(){
  HOUR_DAYS = HOUR_DAYS===14 ? 30 : 14;
  $('hRangeBtn').textContent = '近 ' + HOUR_DAYS + ' 天';
  drawHeatHour($('heatHour'));
}

// ---- 数据加载 ----
async function api(url){ const r = await fetch(url); return r.json(); }
async function loadDay(date){
  CUR.date = date;
  const [d, h, u] = await Promise.all([
    api('/api/day?date='+date),
    api('/api/hourly?date='+date),
    api('/api/urls?date='+date),
  ]);
  CUR.agg = d.aggregate || {}; CUR.hourly = h.hourly_ms || []; CUR.urls = u || {};
  const a = CUR.agg;
  // 卡片
  $('cTotal').textContent = fmtMs(a.total_active_ms);
  const peak = a.hourly_ms ? a.hourly_ms.indexOf(Math.max(...a.hourly_ms)) : -1;
  $('cTotalSub').textContent = (peak>=0 ? '最忙时段 ' + String(peak).padStart(2,'0') + ':00' : '') + ' · ' + (a.session_count||0) + ' 会话';
  const ai = a.by_ai ? Object.values(a.by_ai).reduce((s,v)=>s+v,0) : 0;
  const total = a.total_active_ms||1;
  const social = (a.by_category||{})['社交聊天'] || 0;
  $('cAi').textContent = fmtMs(ai);
  $('cAiSub').textContent = total>0 ? (ai/total*100).toFixed(1)+'% 占比' : '';
  $('cSocial').textContent = fmtMs(social);
  $('cSocialSub').textContent = total>0 ? (social/total*100).toFixed(1)+'% 占比' : '';
  $('cBrowser').textContent = (u.total_duration_s!=null ? fmtMs(u.total_duration_s*1000) : '-');
  $('cBrowserSub').textContent = (u.count!=null && u.count>0) ? u.count+' 条访问 · '+Math.round((u.total_duration_s||0)/3600*10)/10+' 小时' : '';
  $('cCount').textContent = a.session_count ?? '-';
  const aiTop = Object.entries(a.by_ai||{}).sort((x,y)=>y[1]-x[1])[0];
  $('cCountSub').textContent = aiTop ? 'AI 主力: '+esc(aiTop[0]) : '';
  // 类别 + 应用
  const catTotal = Object.values(a.by_category||{}).reduce((s,v)=>s+v,0) || 1;
  $('catBars').innerHTML = Object.entries(a.by_category||{}).sort((x,y)=>y[1]-x[1])
    .map(([k,v])=>'<div style="margin-bottom:8px;display:flex;justify-content:space-between;font-size:12px">'
      + '<span>'+esc(k)+'</span><span style="color:var(--dim)">'+fmtMs(v)+' · '+(v/catTotal*100).toFixed(1)+'%</span></div>'
      + '<div class="bar" style="width:'+Math.max(2, v/catTotal*100)+'%"></div>').join('')
    || '<div class="empty">（无数据）</div>';
  $('appBars').innerHTML = barList(a.by_app, fmtMs, 15);
  let misc = '<div style="margin-bottom:10px"><b style="font-size:13px">AI 工具</b>'
    + barList(a.by_ai, fmtMs, 99) + '</div><div><b style="font-size:13px">联系人</b>'
    + barList(Object.fromEntries(Object.entries(a.by_contact||{}).flatMap(([app,cs])=>Object.entries(cs).map(([c,v])=>[app+'/'+c,v]))), fmtMs, 99) + '</div>';
  $('misc').innerHTML = misc;
  // 浏览器汇总（概览视图：分类/域名 Top）
  const us = u || {};
  let usHtml = '';
  const byCat = us.by_category_duration_s||{};
  if(Object.keys(byCat).length){
    usHtml += '<div style="margin-bottom:10px"><b style="font-size:13px">分类停留</b>' + barList(byCat, fmtMin, 99) + '</div>';
  }
  const byDom = us.by_domain_duration_s||{};
  if(Object.keys(byDom).length){
    usHtml += '<div><b style="font-size:13px">域名停留 Top 5</b>' + barList(byDom, fmtMin, 5) + '</div>';
  }
  $('urlSummary').innerHTML = usHtml || '<div class="empty">（当日无浏览器记录）</div>';
  // 24 小时图
  drawBars($('hourly'), (CUR.hourly||[]).map((v,i)=>({v, label:i})), i=>String(i).padStart(2,'0')+':00', fmtMin, it=>it.label+':00');
  renderSessions();
  renderUrls();
  if(!$('view-report').classList.contains('hidden')) loadReport();
}
function renderSessions(){
  const kw = ($('sessFilter').value||'').trim().toLowerCase();
  const rows = ((CUR.agg||{}).sessions||[]).filter(s=>!kw || (s.app||s.exe||s.title||'').toLowerCase().includes(kw))
    .slice(0,200).map(s=>{
      const note=[s.ai_tool?'AI:'+s.ai_tool:'', s.contact?'联系人:'+s.contact:'', s.browser_category||''].filter(Boolean).join(' ');
      return '<tr><td>'+esc(s.start)+'</td><td>'+esc(s.end)+'</td><td>'+Math.floor((s.duration_ms||0)/1000)+'</td><td>'
        +esc(s.app||s.exe||'')+'</td><td class="url-cell">'+esc(s.title||'')+'</td><td>'+esc(s.category||'')+'</td><td>'+esc(note)+'</td></tr>';
    }).join('');
  $('sessBody').innerHTML = rows || '<tr><td colspan="7" class="empty">（无匹配记录）</td></tr>';
}
function renderUrls(){
  const kw = ($('urlFilter').value||'').trim().toLowerCase();
  const rows = ((CUR.urls&&CUR.urls.visits)||[]).filter(v=>!kw || (v.domain+' '+(v.title||'')+' '+(v.url||'')).toLowerCase().includes(kw))
    .map(v=>{
      const pill = 'pill-'+({视频:'video',代码:'code',学习:'learn'}[v.category]||'other');
      const short = String(v.url||'').split('?')[0];
      const label = v.title && v.title!=='[已隐藏]'
        ? '<a href="'+esc(short)+'" target="_blank" rel="noopener">'+esc(String(v.title).slice(0,60))+'</a>'
        : '<span class="url-cell">'+esc(v.url==='[已隐藏]'?'[已隐藏]':short.slice(0,80))+'</span>';
      const dur = v.duration_s>=60 ? Math.round(v.duration_s/60)+' 分钟' : Math.round(v.duration_s)+' 秒';
      return '<tr><td>'+esc(String(v.time||'').slice(11))+'</td><td><span class="pill '+pill+'">'+esc(v.category||'-')+'</span></td>'
        +'<td>'+esc(v.domain||'-')+'</td><td>'+dur+'</td><td class="url-cell">'+label+'</td></tr>';
    }).join('');
  $('urlBody').innerHTML = rows || '<tr><td colspan="5" class="empty">（当日无浏览器记录）</td></tr>';
}
async function loadReport(){
  if(!CUR.date) return;
  const d = await api('/api/report?date='+CUR.date);
  $('reportHint').textContent = d.exists ? 'report.md · '+CUR.date : '该日暂无 report.md（数据不足或未生成）';
  $('reportMd').innerHTML = d.exists ? renderMarkdown(d.markdown) : '<div class="empty">该日暂无日报 —— 当天无数据或报告尚未生成<br>（monitor 会在跨天时自动生成；也可运行 report.py --day '+CUR.date+' --write）</div>';
}
async function loadTrend(){
  const days = await api('/api/days?n='+RANGE_DAYS);
  CUR.trend = days.days||[];
  $('trendHint').textContent = '近 '+RANGE_DAYS+' 天 · 悬停看详情';
  drawBars($('trend'), CUR.trend.map(d=>({v:d.total_ms, label:d.date})), i=>CUR.trend[i].date.slice(5),
    ms=>fmtMs(ms), it=>it.label);
}
function toggleRange(){
  RANGE_DAYS = RANGE_DAYS===14 ? 30 : 14;
  $('rangeBtn').textContent = '近 '+RANGE_DAYS+' 天';
  loadTrend();
}
async function init(){
  $('dataRoot').textContent = DATA_ROOT;
  applyTheme();
  const [all] = await Promise.all([api('/api/dates'), loadTrend()]);
  const sel=$('daySelect');
  sel.innerHTML = all.dates.map(d=>'<option value="'+d+'">'+d+'</option>').join('');
  const last = all.dates.length ? all.dates[all.dates.length-1] : todayStr();
  sel.value = last;
  const params = new URLSearchParams(location.search);
  switchTab(params.get('view') || 'overview');
  await loadDay(last);
}
init();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "UsageMonitorDashboard/3.0"

    def log_message(self, fmt, *args):  # 静默，减少刷屏
        pass

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _valid_date(self, query: dict) -> str | None:
        """校验并返回日期参数；非法返回 None。"""
        date = query.get("date", [""])[0]
        return date if _DAY_RE.fullmatch(date) else None

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        root = self.server.data_root

        if path == "/" or path == "/index.html":
            html = PAGE_TEMPLATE.replace("DATA_ROOT", json.dumps(root))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/dates":
            days = _available_days(root)
            self._send_json({"dates": days})
            return

        if path == "/api/days":
            n = max(1, min(90, int(query.get("n", ["14"])[0])))
            days = _available_days(root)[-n:]
            out = []
            for d in days:
                agg = report.aggregate(d, root)
                out.append({"date": d, "total_ms": agg["total_active_ms"], "count": agg["session_count"]})
            self._send_json({"days": out})
            return

        if path == "/api/day":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            self._send_json({"date": date, "aggregate": report.aggregate(date, root)})
            return

        if path == "/api/hourly":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            agg = report.aggregate(date, root)
            self._send_json({"date": date, "hourly_ms": agg.get("hourly_ms", [0] * 24)})
            return

        if path == "/api/urls":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            try:
                import browser_history  # noqa: PLC0415 —— 惰性导入
                config = browser_history.classifier.load_config()
                data = browser_history.collect(date, root, config)
                visits = data.get("visits", [])[:_URL_MAX_ROWS]
                self._send_json({
                    "date": date,
                    "count": data.get("count", 0),
                    "total_duration_s": data.get("total_duration_s", 0),
                    "by_category_duration_s": data.get("by_category_duration_s", {}),
                    "by_domain_duration_s": data.get("by_domain_duration_s", {}),
                    "visits": visits,
                })
            except Exception:  # noqa: BLE001 —— 浏览器数据失败不影响页面其他部分
                self._send_json({"date": date, "count": 0, "total_duration_s": 0,
                                 "by_category_duration_s": {}, "by_domain_duration_s": {}, "visits": []})
            return

        if path == "/api/report":
            date = self._valid_date(query)
            if not date:
                self._send_json({"error": "invalid date"}, 400)
                return
            md_path = os.path.join(root, date, "report.md")
            if os.path.isfile(md_path):
                try:
                    with open(md_path, "r", encoding="utf-8-sig") as fh:
                        self._send_json({"date": date, "exists": True, "markdown": fh.read()})
                        return
                except OSError:
                    pass
            self._send_json({"date": date, "exists": False, "markdown": ""})
            return

        if path == "/api/heatmap":
            # 热力图数据：最近 N 天（默认 84 = 12 周）的每日总活跃 + 24 小时分布
            try:
                n = max(7, min(90, int(query.get("days", ["84"])[0])))
            except ValueError:
                n = 84
            days = _available_days(root)[-n:]
            out = []
            for d in days:
                agg = report.aggregate(d, root)
                out.append({
                    "date": d,
                    "total_ms": agg["total_active_ms"],
                    "hourly_ms": agg.get("hourly_ms", [0] * 24),
                })
            self._send_json({"days": out})
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        self._send_json({"error": "method not allowed"}, 405)


def _available_days(data_root: str) -> list[str]:
    """列出数据根目录下所有 YYYY-MM-DD 文件夹（升序）。"""
    if not os.path.isdir(data_root):
        return []
    days = []
    for name in os.listdir(data_root):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            days.append(name)
    return sorted(days)


def create_server(data_root: str, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """创建仪表盘服务器（绑定 127.0.0.1）。"""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.data_root = data_root
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dashboard.py", description="本地网页仪表盘（仅 127.0.0.1）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {version.VERSION}")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"监听端口（默认 {DEFAULT_PORT}）")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--data-root", default=None, help="数据根目录（默认取 config.json）")
    args = parser.parse_args(argv)

    try:
        import classifier  # noqa: PLC0415
        data_root = args.data_root or (classifier.load_config().get("data_root") or DEFAULT_DATA_ROOT)
    except Exception:  # noqa: BLE001
        data_root = args.data_root or DEFAULT_DATA_ROOT

    server = create_server(data_root, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[dashboard] 数据目录: {data_root}")
    print(f"[dashboard] 仪表盘已启动: {url}  （Ctrl+C 退出）")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[dashboard] 已退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
