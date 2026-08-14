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
import paths  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_DATA_ROOT = paths.default_data_root()

# API 日期参数白名单：防路径穿越（date=../../xxx 会拼进数据目录路径）
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_URL_MAX_ROWS = 200  # 浏览器明细最多回传条数


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>UsageMonitor · 概览</title>
<style>
  :root{
    --bg:#101318; --surface:#14171e; --surface-2:#1a1e27; --surface-3:#20252f;
    --border:rgba(255,255,255,.07); --border-strong:rgba(255,255,255,.13);
    --text:#e8e6e1; --dim:#9aa0ab; --faint:#6b7280;
    --accent:#e0a53c; --accent-soft:rgba(224,165,60,.13);
    --danger:#e0533d; --warn:#d9a441; --ok:#7fb069;
    --mono:ui-monospace,"Cascadia Code",Consolas,"Courier New",monospace;
    --radius:8px; --sidebar-w:216px;
    --ease:cubic-bezier(.22,.61,.36,1);
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html,body{height:100%}
  body{background:var(--bg);color:var(--text);
    font-family:ui-sans-serif,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    font-size:13px;line-height:1.55;-webkit-font-smoothing:antialiased}
  ::selection{background:var(--accent-soft)}
  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:#2a303c;border-radius:5px;border:2px solid var(--bg)}
  ::-webkit-scrollbar-thumb:hover{background:#39414f}
  ::-webkit-scrollbar-track{background:transparent}
  button,input,select{font:inherit;color:inherit}
  .app{display:flex;min-height:100vh}

  /* ---------- 侧边栏 ---------- */
  .sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--surface);
    border-right:1px solid var(--border);display:flex;flex-direction:column;
    position:fixed;top:0;bottom:0;left:0;z-index:50}
  .brand{display:flex;align-items:center;gap:10px;padding:18px 16px 14px;
    border-bottom:1px solid var(--border)}
  .brand svg{flex-shrink:0}
  .brand b{display:block;font-size:14px;letter-spacing:.2px}
  .brand span{display:block;font-size:11px;color:var(--faint);margin-top:1px}
  .nav{padding:10px 8px;flex:1;overflow-y:auto}
  .nav-item{display:flex;align-items:center;gap:10px;padding:8px 10px;margin:2px 0;
    border-radius:6px;color:var(--dim);text-decoration:none;position:relative;
    transition:background .18s var(--ease),color .18s var(--ease)}
  .nav-item svg{flex-shrink:0;opacity:.85}
  .nav-item:hover{background:var(--surface-2);color:var(--text)}
  .nav-item.active{color:var(--text);background:var(--accent-soft)}
  .nav-item.active::before{content:"";position:absolute;left:-8px;top:20%;bottom:20%;width:3px;
    border-radius:2px;background:var(--accent)}
  .side-foot{padding:14px 16px;border-top:1px solid var(--border);font-size:11px;color:var(--faint)}
  .side-foot .root{font-family:var(--mono);font-size:10px;word-break:break-all;color:var(--dim);
    margin-bottom:6px}
  .backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:40;opacity:0;pointer-events:none;
    transition:opacity .2s var(--ease)}
  .hamburger{display:none;position:fixed;top:12px;left:12px;z-index:60;width:36px;height:36px;
    border:1px solid var(--border);border-radius:6px;background:var(--surface);cursor:pointer;
    align-items:center;justify-content:center}

  /* ---------- 主内容 ---------- */
  .content{flex:1;margin-left:var(--sidebar-w);padding:26px 30px 60px;max-width:1240px}
  .page-head{display:flex;align-items:center;justify-content:space-between;gap:14px;
    flex-wrap:wrap;margin-bottom:22px}
  .page-head h1{font-size:19px;font-weight:600;letter-spacing:.1px}
  .page-head .sub{font-size:12px;color:var(--faint);margin-top:3px}
  .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  select,input[type=text],button.btn{background:var(--surface);border:1px solid var(--border);
    border-radius:6px;padding:6px 10px;font-size:12.5px;color:var(--text);outline:none;
    transition:border-color .18s var(--ease),background .18s var(--ease)}
  select:focus,input[type=text]:focus,button.btn:focus-visible{border-color:var(--accent)}
  button.btn{cursor:pointer}
  button.btn:hover{background:var(--surface-2);border-color:var(--border-strong)}
  button.btn.primary{background:var(--accent);border-color:var(--accent);color:#141008;font-weight:600}
  button.btn.primary:hover{background:#eab356}

  /* ---------- 视图切换动画 ---------- */
  .view{display:none}
  .view.active{display:block;animation:viewIn .24s var(--ease) both}
  @keyframes viewIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

  /* ---------- 卡片 ---------- */
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:18px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:14px 16px;transition:border-color .18s var(--ease),transform .18s var(--ease)}
  .card:hover{border-color:var(--border-strong);transform:translateY(-1px)}
  .card .label{font-size:11.5px;color:var(--dim);display:flex;align-items:center;gap:6px}
  .card .value{font-size:23px;font-weight:600;margin-top:7px;font-variant-numeric:tabular-nums;
    letter-spacing:-.2px}
  .card .value small{font-size:12px;color:var(--faint);font-weight:400;margin-left:4px}
  .card .trend{font-size:11px;margin-top:4px;color:var(--faint)}

  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media(max-width:960px){.grid{grid-template-columns:1fr}}
  .panel{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:16px 18px;margin-bottom:12px}
  .panel h2{font-size:12px;font-weight:600;letter-spacing:.6px;text-transform:uppercase;
    color:var(--faint);margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}
  .panel h2 .hint{font-size:11px;text-transform:none;letter-spacing:0;font-weight:400}

  /* ---------- 统计行（类别/应用条） ---------- */
  .stat-row{margin-bottom:9px}
  .stat-row .top{display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px}
  .stat-row .top .name{color:var(--text)}
  .stat-row .top .val{color:var(--dim);font-variant-numeric:tabular-nums}
  .bar{height:6px;border-radius:3px;background:var(--surface-3);overflow:hidden}
  .bar > i{display:block;height:100%;border-radius:3px;background:var(--accent);
    width:0;transition:width .7s var(--ease)}

  /* ---------- 表格 ---------- */
  .tbl-wrap{overflow:auto;border:1px solid var(--border);border-radius:var(--radius)}
  table.tbl{width:100%;border-collapse:collapse;font-size:12.5px;min-width:640px}
  .tbl th{text-align:left;padding:8px 10px;color:var(--faint);font-weight:500;font-size:11px;
    text-transform:uppercase;letter-spacing:.5px;background:var(--surface-2);
    border-bottom:1px solid var(--border);position:sticky;top:0;white-space:nowrap}
  .tbl td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:top}
  .tbl tbody tr{transition:background .12s var(--ease)}
  .tbl tbody tr:hover{background:var(--surface-2)}
  .tbl td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
  .tbl td.mono{font-family:var(--mono);font-size:11.5px}
  .tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;margin-right:5px;
    background:var(--surface-3);border:1px solid var(--border-strong);color:var(--dim);white-space:nowrap}
  .tag.ai{color:#e8b46a;border-color:rgba(232,180,106,.35);background:rgba(232,180,106,.08)}
  .url-cell{font-family:var(--mono);font-size:11px;color:var(--faint);max-width:280px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block;vertical-align:bottom}

  /* ---------- 日志 ---------- */
  .log-box{background:#0d1016;border:1px solid var(--border);border-radius:var(--radius);
    font-family:var(--mono);font-size:11.5px;line-height:1.7;padding:12px 14px;
    height:calc(100vh - 300px);min-height:280px;overflow:auto;white-space:pre-wrap;word-break:break-all}
  #view-log .grid{grid-template-columns:1fr}
  .log-line{display:flex;gap:10px}
  .log-line .ts{color:var(--faint);flex-shrink:0}
  .log-line .lv{flex-shrink:0;width:34px}
  .log-line .lv.INFO{color:var(--faint)}
  .log-line .lv.WARN{color:var(--warn)}
  .log-line .lv.ERRO{color:var(--danger)}
  .log-line .msg{color:var(--dim)}
  .log-line.err .msg{color:#d98a7d}

  /* ---------- 日报渲染 ---------- */
  .md h1{font-size:19px;border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:16px}
  .md h2{font-size:14px;color:#e8b46a;margin:20px 0 10px;letter-spacing:.3px}
  .md table{border-collapse:collapse;width:100%;font-size:12.5px;margin:8px 0 16px}
  .md th,.md td{border:1px solid var(--border);padding:6px 10px;text-align:left}
  .md th{background:var(--surface-2);color:var(--faint);font-weight:500}
  .md a{color:#8fb8ff;text-decoration:none}
  .md blockquote{color:var(--dim);border-left:3px solid var(--border-strong);padding-left:10px;margin:8px 0}
  .mdbar{height:6px;border-radius:3px;background:var(--surface-3);overflow:hidden;min-width:60px}
  .mdbar i{display:block;height:100%;background:var(--accent);border-radius:3px}
  .md p{font-size:12.5px;margin:6px 0}
  .md li{font-size:12.5px;margin:3px 0 3px 18px}

  /* ---------- 骨架屏 ---------- */
  .sk{position:relative;overflow:hidden;background:var(--surface-3);border-radius:6px}
  .sk::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.05),transparent);
    animation:shimmer 1.3s infinite}
  @keyframes shimmer{to{transform:translateX(100%)}}
  .empty{padding:34px 0;text-align:center;color:var(--faint);font-size:12.5px}
  canvas{width:100%;display:block}

  /* ---------- 热力图 ---------- */
  .hm{display:flex;gap:2px;min-height:264px}
  .hm .hl-col{display:flex;flex-direction:column;width:22px;gap:2px;flex-shrink:0}
  .hm .hl-col span{font-family:var(--mono);font-size:8.5px;color:var(--faint);line-height:11px;height:11px}
  .hm .grid-col{display:flex;flex-direction:column;gap:2px;flex:1}
  .hm .cell{height:11px;border-radius:2px;transition:background .2s var(--ease)}
  .hm-legend{display:flex;align-items:center;gap:6px;font-size:10.5px;color:var(--faint);
    justify-content:flex-end;margin-top:8px}
  .hm-legend .sw{width:10px;height:10px;border-radius:2px}

  /* ---------- 响应式 ---------- */
  @media(max-width:920px){
    .sidebar{transform:translateX(-100%);transition:transform .26s var(--ease)}
    .sidebar.open{transform:none;box-shadow:0 0 40px rgba(0,0,0,.5)}
    .content{margin-left:0;padding:18px 16px 50px}
    .hamburger{display:flex}
    .backdrop.show{opacity:1;pointer-events:auto}
    .page-head h1{display:none}
  }
  @media(prefers-reduced-motion:reduce){
    *,*::before,*::after{animation:none!important;transition:none!important}
  }

</style>
</head>
<body>
<div class="app">
  <aside class="sidebar" id="sidebar">
    <div class="brand">
      <svg width="30" height="30" viewBox="0 0 30 30" fill="none" aria-hidden="true">
        <circle cx="15" cy="15" r="13" stroke="#e0a53c" stroke-width="2.4"/>
        <path d="M15 9v6.2l4.2 2.4" stroke="#e0a53c" stroke-width="2" stroke-linecap="round"/>
      </svg>
      <div><b>UsageMonitor</b><span>电脑使用情况监控</span></div>
    </div>
    <nav class="nav" id="nav">
      <a class="nav-item active" data-view="overview" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="1.5" width="5.6" height="5.6" rx="1"/><rect x="8.9" y="1.5" width="5.6" height="5.6" rx="1"/><rect x="1.5" y="8.9" width="5.6" height="5.6" rx="1"/><rect x="8.9" y="8.9" width="5.6" height="5.6" rx="1"/></svg>
        概览
      </a>
      <a class="nav-item" data-view="trends" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M1.5 13.5h13M3 11l3-4 2.5 2.5L13 4.5"/></svg>
        趋势
      </a>
      <a class="nav-item" data-view="report" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M3.5 1.5h6l3 3v10h-9z"/><path d="M9.5 1.5v3h3M5.5 8.5h5M5.5 11h5"/></svg>
        日报
      </a>
      <a class="nav-item" data-view="sessions" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4h12M2 8h12M2 12h7"/></svg>
        会话
      </a>
      <a class="nav-item" data-view="log" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><path d="M5 6l2.2 2L5 10M9 10.5h2.5"/></svg>
        日志
      </a>
      <a class="nav-item" data-view="groups" href="#">
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 4.5h12v7H2z"/><path d="M2 7h12"/><path d="M6 4.5V9M10 4.5V9"/></svg>
        分组
      </a>
    </nav>
    <div class="side-foot">
      <div class="root" id="rootPath" title="数据目录"></div>
      <div>v1.0.0 · 仅监听 127.0.0.1 · 纯本地</div>
    </div>
  </aside>
  <div class="backdrop" id="backdrop"></div>
  <button class="hamburger" id="hamburger" aria-label="菜单">
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M2 4h12M2 8h12M2 12h12"/></svg>
  </button>

  <main class="content">
    <div class="page-head">
      <div><h1 id="pageTitle">概览</h1><div class="sub" id="pageSub"></div></div>
      <div class="controls" id="headControls"></div>
    </div>

    <!-- 概览 -->
    <section class="view active" id="view-overview">
      <div class="cards" id="ovCards"></div>
      <div class="grid">
        <div class="panel"><h2>最近 14 天活跃趋势</h2><canvas id="ovTrend" height="190"></canvas></div>
        <div class="panel"><h2>今日类别分布</h2><div id="ovCats"></div></div>
      </div>
      <div class="grid">
        <div class="panel"><h2>今日应用 Top 10</h2><div id="ovApps"></div></div>
        <div class="panel"><h2>AI 工具 / 联系人</h2><div id="ovMisc"></div></div>
      </div>
    </section>

    <!-- 趋势 -->
    <section class="view" id="view-trends">
      <div class="panel"><h2>活跃热力图（24 小时 × 最近 84 天）<span class="hint">行=小时 · 列=日期</span></h2>
        <div id="trHeatmap"></div>
        <div class="hm-legend">少 <span class="sw" style="background:#1c212b"></span><span class="sw" style="background:#2c3342"></span><span class="sw" style="background:#a06f24"></span><span class="sw" style="background:#e0a53c"></span> 多</div>
      </div>
      <div class="panel"><h2>日活跃柱状图</h2>
        <div class="controls" style="margin-bottom:12px">
          <button class="btn" data-range="14">14 天</button>
          <button class="btn primary" data-range="30">30 天</button>
        </div>
        <canvas id="trBars" height="210"></canvas>
      </div>
    </section>

    <!-- 日报 -->
    <section class="view" id="view-report">
      <div class="panel"><div class="md" id="rpMd"></div></div>
    </section>

    <!-- 会话 -->
    <section class="view" id="view-sessions">
      <div class="panel">
        <div class="controls" style="margin-bottom:12px">
          <select id="ssCat"></select>
          <select id="ssApp"></select>
          <input type="text" id="ssSearch" placeholder="搜索标题 / 应用…" style="width:200px">
          <span class="hint" id="ssCount" style="color:var(--faint);font-size:11.5px"></span>
        </div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>开始</th><th>时长</th><th>应用</th><th>标题</th><th>类别</th><th>备注</th></tr></thead>
          <tbody id="ssBody"></tbody>
        </table></div>
      </div>
    </section>

    <!-- 日志 -->
    <section class="view" id="view-log">
      <div class="grid">
        <div class="panel"><h2>运行日志（app.log）<span class="hint">自动刷新 15s</span></h2>
          <div class="log-box" id="lgEntries"></div>
        </div>
        <div class="panel"><h2>错误日志（errors.log · 最近 3 天）</h2>
          <div class="log-box" id="lgErrors"></div>
        </div>
      </div>
    </section>

    <!-- 分组管理 -->
    <section class="view" id="view-groups">
      <div class="panel">
        <div class="controls" style="margin-bottom:14px">
          <input type="text" id="grpSearch" placeholder="搜索应用…" style="width:200px">
          <input type="text" id="grpNewName" placeholder="新分组名称" style="width:150px" maxlength="20">
          <button class="btn primary" id="grpAdd">新增分组</button>
          <span id="grpStatus" style="color:var(--faint);font-size:11.5px"></span>
        </div>
        <div id="grpCats" style="margin-bottom:14px;line-height:2"></div>
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr><th>应用</th><th>当前分组</th><th>移动到</th></tr></thead>
          <tbody id="grpBody"></tbody>
        </table></div>
        <div id="grpCount" style="color:var(--faint);font-size:11.5px;margin-top:8px"></div>
      </div>
    </section>
  </main>
</div>

<script>
"use strict";
const ROOT_DIR = DATA_ROOT;
const TITLES = {overview:"概览",trends:"趋势",report:"日报",sessions:"会话",log:"日志",groups:"分组"};
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const state = { view:"overview", day:null, dates:[], loaded:{} };
const NO_ANIM = location.search.includes("static=1") || matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- 工具 ---------- */
async function api(path){
  const r = await fetch(path);
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function esc(s){ return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function fmtMs(ms){
  if(ms == null || ms < 0) return "-";
  const s = Math.floor(ms/1000), h = Math.floor(s/3600), m = Math.floor(s%3600/60), sec = s%60;
  const p = [];
  if(h) p.push(h + " 小时"); if(m) p.push(m + " 分钟"); if(!h && !m && sec) p.push(sec + " 秒");
  return p.join(" ") || "0 秒";
}
function fmtDurS(sec){ return fmtMs(Math.round(sec)*1000); }
function fmtCompact(ms){
  if(ms == null || ms < 0) return "-";
  const s = Math.floor(ms/1000);
  if(s >= 3600) return (s/3600).toFixed(1) + "h";
  if(s >= 60) return Math.round(s/60) + "m";
  return s + "s";
}
function countUp(el, value, fmt, dur){
  if(!el) return;
  if(NO_ANIM){ el.textContent = fmt(value); return; }
  const t0 = performance.now(), durMs = dur || 500;
  function tick(t){
    const p = Math.min(1, (t - t0)/durMs), e = 1 - Math.pow(1-p, 3);
    const v = Math.min(value, Math.max(0, Math.round(value * e)));
    el.textContent = fmt(v);
    if(p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}
function skeleton(rows){
  return '<div class="sk" style="height:' + (rows*14) + 'px;width:100%"></div>';
}
function statRows(data, fmt){
  const max = Math.max(1, ...Object.values(data));
  return Object.entries(data).sort((a,b)=>b[1]-a[1]).map(([k,v])=>{
    const pct = Math.max(2, Math.round(v/max*100));
    return '<div class="stat-row"><div class="top"><span class="name">' + esc(k) + '</span>' +
      '<span class="val">' + fmt(v) + '</span></div><div class="bar"><i style="width:' + pct + '%"></i></div></div>';
  }).join("") || '<div class="empty">（无数据）</div>';
}

/* ---------- 视图切换 ---------- */
function closeDrawer(){
  $("#sidebar").classList.remove("open");
  $("#backdrop").classList.remove("show");
}
function switchView(v, push){
  state.view = v;
  $$(".view").forEach(el => el.classList.toggle("active", el.id === "view-"+v));
  $$(".nav-item").forEach(a => a.classList.toggle("active", a.dataset.view === v));
  $("#pageTitle").textContent = TITLES[v];
  document.title = TITLES[v] + " · UsageMonitor";
  if(push !== false) history.replaceState(null, "", v === "overview" ? "/" : "/?view=" + v);
  closeDrawer();
  if(!state.loaded[v]){
    state.loaded[v] = true;
    Promise.resolve().then(async () => {
      try { await loaders[v](); }
      catch(err){ const box = $("#view-" + v); if(box) box.innerHTML = '<div class="empty">加载失败：' + esc(err.message) + '</div>'; }
    });
  }
}
const loaders = { overview:loadOverview, trends:loadTrends, report:loadReport,
                  sessions:loadSessions, log:loadLog, groups:loadGroups };

/* ---------- 头部控件（日期选择） ---------- */
function buildHeadControls(){
  const c = $("#headControls");
  c.innerHTML = '<select id="daySel"></select><button class="btn" id="btnToday">今天</button>';
  $("#btnToday").onclick = () => { pickDay(todayStr()); };
  $("#daySel").onchange = e => pickDay(e.target.value);
}
function pickDay(d){
  state.day = d; $("#daySel").value = d;
  if(state.view === "overview") loadOverview();
  if(state.view === "report") loadReport();
  if(state.view === "sessions") loadSessions();
}
function todayStr(){ const d=new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }

/* ---------- 画布图表 ---------- */
function setupCanvas(cv){
  const dpr = window.devicePixelRatio || 1;
  let w = cv.clientWidth, h = cv.clientHeight;
  if(w < 20){ w = cv.parentElement ? cv.parentElement.clientWidth : 600; }
  if(h < 20){ h = parseInt(cv.getAttribute("height") || "200", 10); }
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  return {ctx, w, h};
}
function niceMax(v){
  if(v <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / exp;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
}
function drawBarChart(cv, labels, values, fmt){
  const {ctx,w,h} = setupCanvas(cv);
  const padL = 44, padB = 22, padT = 14;
  const max = niceMax(Math.max(1, ...values));
  const n = values.length, bw = Math.max(6, (w-padL-8)/n);
  const labelStep = bw < 60 ? 2 : 1;
  const tickFmt = v => v >= 3600000 ? Math.round(v/3600000) + "h" : Math.round(v/60000) + "m";
  function paint(ease){
    ctx.clearRect(0,0,w,h);
    ctx.strokeStyle = "rgba(255,255,255,.06)"; ctx.fillStyle = "#6b7280";
    ctx.font = "10px " + getComputedStyle(document.body).fontFamily;
    ctx.lineWidth = 1;
    for(let g=0; g<=4; g++){
      const y = padT + (h-padT-padB) * g/4;
      ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(w-4,y); ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(tickFmt(Math.round(max * (1 - g/4))), padL-6, y+3);
    }
    values.forEach((v,i)=>{
      const bh = Math.max(2, (v/max) * (h-padT-padB) * ease);
      const x = padL + i*bw, y = h-padB-bh;
      ctx.fillStyle = v > 0 ? "#e0a53c" : "#232936";
      ctx.fillRect(x+2, y, bw-4, bh);
      if(i % labelStep === 0){
        ctx.fillStyle = "#6b7280"; ctx.textAlign = "center";
        ctx.fillText(String(labels[i]).slice(5), x+bw/2, h-8);
      }
    });
  }
  if(NO_ANIM){ paint(1); return; }
  const t0 = performance.now();
  function frame(t){
    const p = Math.min(1, (t - t0)/650);
    paint(1 - Math.pow(1-p, 3));
    if(p < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

/* ---------- 概览 ---------- */
async function loadOverview(){
  const cards = $("#ovCards");
  cards.innerHTML = [0,1,2,3].map(()=>'<div class="card"><div class="label">…</div><div class="sk" style="height:26px;margin-top:7px"></div></div>').join("");
  const d = await api("/api/day?date=" + state.day);
  const a = d.aggregate;
  const ai = Object.values(a.by_ai||{}).reduce((s,v)=>s+v,0);
  const social = (a.by_category||{})["社交聊天"] || 0;
  cards.innerHTML =
    '<div class="card"><div class="label">总活跃时长</div><div class="value" id="cTotal"></div><div class="trend">'+a.session_count+' 个会话</div></div>' +
    '<div class="card"><div class="label">AI 编程时长</div><div class="value" id="cAi"></div></div>' +
    '<div class="card"><div class="label">社交聊天</div><div class="value" id="cSocial"></div></div>' +
    '<div class="card"><div class="label">浏览器停留</div><div class="value" id="cUrl">—</div><div class="trend">标签页口径 · 含挂机</div></div>';
  countUp($("#cTotal"), a.total_active_ms, fmtMs);
  countUp($("#cAi"), ai, fmtMs);
  countUp($("#cSocial"), social, fmtMs);
  try{
    const u = await api("/api/urls?date=" + state.day);
    countUp($("#cUrl"), (u.total_duration_s||0)*1000, fmtMs);
  }catch(e){ $("#cUrl").textContent = "-"; }
  $("#ovCats").innerHTML = skeleton(6);
  $("#ovCats").innerHTML = statRows(a.by_category, fmtMs);
  $("#ovApps").innerHTML = statRows(a.by_app, fmtMs);
  const aiBlock = '<div style="margin-bottom:16px"><b style="font-size:12px;color:var(--dim);letter-spacing:.4px">AI 工具</b>' +
    (Object.keys(a.by_ai||{}).length ? statRows(a.by_ai, fmtMs) : '<div class="empty">（无 AI 工具记录）</div>') + '</div>';
  const ctBlock = '<div><b style="font-size:12px;color:var(--dim);letter-spacing:.4px">联系人</b>' +
    ((a.by_contact && Object.keys(a.by_contact).length) ? statRows(Object.fromEntries(Object.entries(a.by_contact).flatMap(([app,cs])=>Object.entries(cs).map(([c,v])=>[app+"/"+c, v]))), fmtMs) : '<div class="empty">（无联系人记录）</div>') + '</div>';
  $("#ovMisc").innerHTML = aiBlock + ctBlock;
  const days = await api("/api/days?n=14");
  drawBarChart($("#ovTrend"), days.days.map(x=>x.date), days.days.map(x=>x.total_ms),
    v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
}

/* ---------- 趋势 ---------- */
async function loadTrends(){
  $("#trHeatmap").innerHTML = skeleton(20);
  $("#trBars").style.display = "none";
  const hm = await api("/api/heatmap?days=84");
  const maxH = Math.max(1, ...hm.days.flatMap(d=>d.hourly_ms));
  const box = $("#trHeatmap");
  const lvl = ["#242a36","#2f3a4d","#6b5323","#a06f24","#e0a53c"];
  const nDays = hm.days.length;
  // 行=小时(24)，列=天数；flex 布局：标签列 + 每天一列
  const cols = [];
  for(let di=0; di<nDays; di++){
    const day = hm.days[di];
    let col = '<div class="grid-col" title="'+day.date+'">';
    for(let hi=0; hi<24; hi++){
      const ms = day.hourly_ms[hi] || 0;
      const s = ms>0 ? Math.max(1, Math.round(ms/maxH*3)) : 0;
      const delay = NO_ANIM ? 0 : (di*2+hi*0.3);
      col += '<div class="cell" style="background:'+lvl[s]+';opacity:1;transition-delay:'+delay+'ms" title="'+day.date+' '+String(hi).padStart(2,"0")+':00 — '+fmtMs(ms)+'"></div>';
    }
    col += '</div>';
    cols.push(col);
  }
  let labels = '<div class="hl-col">';
  for(let hi=0; hi<24; hi++){
    labels += '<span>'+(hi%4===0 ? String(hi).padStart(2,"0") : "")+'</span>';
  }
  labels += '</div>';
  box.innerHTML = labels + cols.join("");
  $("#trBars").style.display = "block";
  drawBarChart($("#trBars"), hm.days.slice(-30).map(d=>d.date), hm.days.slice(-30).map(d=>d.total_ms),
    v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
  $$("#view-trends [data-range]").forEach(b=>b.onclick = ()=>{
    $$("#view-trends [data-range]").forEach(x=>x.classList.remove("primary"));
    b.classList.add("primary");
    const n = +b.dataset.range;
    const days = hm.days.slice(-n);
    drawBarChart($("#trBars"), days.map(d=>d.date), days.map(d=>d.total_ms),
      v => v>=3600000 ? (v/3600000).toFixed(1)+"h" : Math.round(v/60000)+"m");
  });
}

/* ---------- Markdown 渲染（日报视图） ---------- */
function md2html(src){
  const lines = (src||"").split("\n"); let out = [], inT = false;
  for(const line of lines){
    if(line.startsWith("|") && line.endsWith("|")){
      const cells = line.slice(1,-1).split("|").map(c=>c.trim());
      if(cells.length && cells.every(c => /^:?-+:?$/.test(c))) continue; // 表头分隔行，跳过
      if(!inT){ out.push("<table>"); inT = true; }
      const tag = "td";
      out.push("<tr>" + cells.map(c=>{
        const pure = c.replace(/[█▇▆▅▄▃▂▁\s]/g, "");
        if(pure === "" && /[█▇▆▅▄▃▂▁]/.test(c)){
          const n = (c.match(/[█▇▆▅▄▃▂▁]/g) || []).length;
          return "<" + tag + "><div class='mdbar'><i style='width:" + Math.min(100, n * 10) + "%'></i></div></" + tag + ">";
        }
        if(c.trim() === "-" || c.trim() === "--") return "<" + tag + "><span style='color:var(--faint)'>—</span></" + tag + ">";
        return "<" + tag + ">" + inline(c) + "</" + tag + ">";
      }).join("") + "</tr>");
      continue;
    }
    if(inT){ out.push("</table>"); inT = false; }
    if(/^#\s/.test(line)) out.push("<h1>" + inline(line.slice(2)) + "</h1>");
    else if(/^##\s/.test(line)) out.push("<h2>" + inline(line.slice(3)) + "</h2>");
    else if(/^>\s?/.test(line)) out.push("<blockquote>" + inline(line.replace(/^>\s?/,"")) + "</blockquote>");
    else if(/^-\s/.test(line)) out.push("<li>" + inline(line.slice(2)) + "</li>");
    else if(!line.trim()) out.push("");
    else out.push("<p>" + inline(line) + "</p>");
  }
  if(inT) out.push("</table>");
  return out.join("\n");
  function inline(s){
    let t = esc(s);
    t = t.replace(/\[([^\]]*)\]\(([^)]*)\)/g, (m,a,b)=>{ const u=b.split("?")[0]; return '<a href="'+esc(b)+'">'+esc(a)+'</a><span class="url-cell">'+esc(u.slice(0,80))+'</span>'; });
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    if(/[█▇▆▅▄▃▂▁]/.test(t)) t = t.replace(/([█▇▆▅▄▃▂▁]+)/g, '<span style="color:#e0a53c;letter-spacing:1px">$1</span>');
    return t;
  }
}
async function loadReport(){
  $("#rpMd").innerHTML = skeleton(18);
  const r = await api("/api/report?date=" + state.day);
  if(!r.exists){ $("#rpMd").innerHTML = '<div class="empty">当日无日报（守护进程跨天时自动生成，或运行 report.py --day '+state.day+' --write）</div>'; return; }
  $("#rpMd").innerHTML = md2html(r.markdown);
}

/* ---------- 会话 ---------- */
async function loadSessions(){
  const d = await api("/api/day?date=" + state.day);
  const a = d.aggregate, sessions = a.sessions || [];
  const catSel = $("#ssCat"), appSel = $("#ssApp");
  catSel.innerHTML = '<option value="">全部类别</option>' + Object.keys(a.by_category).map(c=>'<option>'+esc(c)+'</option>').join("");
  appSel.innerHTML = '<option value="">全部应用</option>' + Object.keys(a.by_app).map(c=>'<option>'+esc(c)+'</option>').join("");
  $("#ssSearch").value = "";
  renderSessions(sessions, a);
}
function renderSessions(sessions, agg){
  const cat = $("#ssCat").value, app = $("#ssApp").value, q = $("#ssSearch").value.toLowerCase();
  const rows = sessions.filter(s =>
    (!cat || s.category === cat) && (!app || (s.app||s.exe) === app) &&
    (!q || ((s.title||"")+(s.app||"")+(s.exe||"")).toLowerCase().includes(q))
  ).slice(0, 300);
  $("#ssCount").textContent = rows.length + " / " + sessions.length + " 条" + (sessions.length>300 ? "（仅显示前 300）" : "");
  $("#ssBody").innerHTML = rows.map(s=>{
    const tags = [];
    if(s.ai_tool) tags.push('<span class="tag ai">AI:'+esc(s.ai_tool)+'</span>');
    if(s.term_tool) tags.push('<span class="tag term">终端:'+esc(s.term_tool)+'</span>');
    if(s.contact) tags.push('<span class="tag contact">'+esc(s.contact)+'</span>');
    if(s.subcategory) tags.push('<span class="tag sub">'+esc(s.subcategory)+'</span>');
    if(s.window_state && s.window_state !== "normal") tags.push('<span class="tag ws">'+({fullscreen:"全屏",maximized:"最大化"}[s.window_state]||s.window_state)+'</span>');
    if(s.url) tags.push(s.url === "[已隐藏]" ? '<span class="tag">URL已隐藏</span>' : '<span class="url-cell" title="'+esc(s.url)+'">'+esc(s.url.split("?")[0].slice(0,56))+'</span>');
    return "<tr><td class='mono num'>"+(s.start||"").slice(11,19)+"</td>" +
      "<td class='num'>"+fmtCompact(s.duration_ms||0)+"</td>" +
      "<td>"+esc(s.app||s.exe||"")+"</td>" +
      "<td class='mono'>"+esc(s.title||"")+"</td>" +
      "<td>"+esc(s.category||"")+"</td>" +
      "<td>"+tags.join("")+"</td></tr>";
  }).join("") || '<tr><td colspan="6" class="empty">无匹配会话</td></tr>';
  $("#ssCat").onchange = ()=>renderSessions(sessions, agg);
  $("#ssApp").onchange = ()=>renderSessions(sessions, agg);
  $("#ssSearch").oninput = ()=>renderSessions(sessions, agg);
}

/* ---------- 日志 ---------- */
function logRow(line, isErr){
  const m = line.match(/^(\S+ \S+) \[([A-Z]+)\] \[([^\]]+)\] (.*)$/);
  if(!m) return '<div class="log-line'+(isErr?' err':'')+'"><span class="msg">'+esc(line)+'</span></div>';
  return '<div class="log-line'+(isErr?' err':'')+'"><span class="ts">'+esc(m[1])+'</span>' +
    '<span class="lv '+m[2]+'">'+esc(m[2])+'</span>' +
    '<span class="msg">['+esc(m[3])+'] '+esc(m[4])+'</span></div>';
}
async function loadLog(){
  const d = await api("/api/log");
  $("#lgEntries").innerHTML = d.entries.length
    ? d.entries.map(l=>logRow(l)).join("")
    : '<div class="empty">暂无运行日志</div>';
  $("#lgErrors").innerHTML = d.errors.length
    ? d.errors.map(l=>logRow(l,true)).join("")
    : '<div class="empty">无错误记录</div>';
  scrollLogBottom();
}
let logTimer = null;
function scrollLogBottom(){
  const el = $("#lgEntries");
  if(el) el.scrollTop = el.scrollHeight;
}
function armLogTimer(){
  if(logTimer) clearInterval(logTimer);
  logTimer = setInterval(()=>{ if(state.view==="log" && state.loaded.log) loadLog(); }, 15000);
}

/* ---------- 分组管理 ---------- */
let grpFlashTimer = null;
function grpFlash(msg){
  const el = $("#grpStatus");
  el.textContent = msg;
  el.style.color = "var(--ok)";
  if(grpFlashTimer) clearTimeout(grpFlashTimer);
  grpFlashTimer = setTimeout(()=>{ el.textContent = ""; }, 3000);
}
async function postJson(url, obj){
  const r = await fetch(url, {method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify(obj)});
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
async function loadGroups(){
  const d = await api("/api/groups");
  state.groups = d;
  $("#grpCats").innerHTML = d.categories.map(c=>{
    const custom = d.custom_categories.includes(c);
    return '<span class="tag" style="padding:4px 10px;margin-right:6px">'+esc(c)+
      (custom ? ' <a href="#" class="grp-del" data-name="'+esc(c)+'" title="删除分组" style="color:var(--danger);text-decoration:none;margin-left:4px">✕</a>' : '')+'</span>';
  }).join("");
  renderGroups();
}
function renderGroups(){
  const q = $("#grpSearch").value.toLowerCase();
  const d = state.groups || {apps:[]};
  const rows = d.apps.filter(a => (a.app+" "+a.exe).toLowerCase().includes(q));
  $("#grpCount").textContent = rows.length + " / " + d.apps.length + " 个应用（下拉选分组即时生效；清空=恢复自动分类）";
  $("#grpBody").innerHTML = rows.map(a=>{
    const opts = ['<option value="">自动分类</option>'].concat(
      d.categories.map(c=>'<option value="'+esc(c)+'"'+(a.overridden && a.category===c ? " selected" : "")+'>'+esc(c)+'</option>')
    ).join("");
    return "<tr><td>"+esc(a.app)+"<span class='url-cell' style='margin-left:8px'>"+esc(a.exe)+"</span></td>"+
      "<td>"+(a.overridden ? '<span class="tag ai">'+esc(a.category)+'</span>' : '<span style="color:var(--dim)">'+esc(a.category)+'</span>')+"</td>"+
      "<td><select data-exe='"+esc(a.exe)+"'>"+opts+"</select></td></tr>";
  }).join("") || '<tr><td colspan="3" class="empty">无匹配应用</td></tr>';
  $$("#grpBody select").forEach(sel=>{
    sel.onchange = async ()=>{
      try{
        await postJson("/api/groups/set", {exe: sel.dataset.exe, category: sel.value});
        grpFlash("已保存：" + sel.dataset.exe + " → " + (sel.value || "自动分类"));
        await loadGroups();
        if(state.loaded.overview) loadOverview();
      }catch(e){ grpFlash("保存失败：" + e.message); }
    };
  });
}
async function groupsInit(){
  $("#grpAdd").onclick = async ()=>{
    const name = $("#grpNewName").value.trim();
    if(!name){ grpFlash("请输入分组名称"); return; }
    try{
      await postJson("/api/groups/add", {name});
      $("#grpNewName").value = "";
      grpFlash("已新增分组：" + name);
      await loadGroups();
    }catch(e){ grpFlash("新增失败：" + e.message); }
  };
  $("#grpSearch").oninput = renderGroups;
  $("#grpCats").onclick = async (e)=>{
    const el = e.target.closest(".grp-del");
    if(!el) return;
    e.preventDefault();
    const name = el.dataset.name;
    if(!confirm("删除分组「" + name + "」？组内应用将恢复自动分类。")) return;
    try{
      await postJson("/api/groups/delete", {name});
      grpFlash("已删除分组：" + name);
      await loadGroups();
      if(state.loaded.overview) loadOverview();
    }catch(err){ grpFlash("删除失败：" + err.message); }
  };
}

/* ---------- 初始化 ---------- */
(async function init(){
  buildHeadControls();
  const [dates] = await Promise.all([api("/api/dates")]);
  state.dates = dates.dates || [];
  $("#daySel").innerHTML = state.dates.map(d=>'<option value="'+d+'">'+d+'</option>').join("");
  state.day = state.dates.length ? state.dates[state.dates.length-1] : todayStr();
  if(!state.dates.includes(state.day)) {
    const opt = document.createElement("option");
    opt.value = state.day; opt.textContent = state.day + "（今天）";
    $("#daySel").appendChild(opt);
  }
  $("#daySel").value = state.day;
  $("#pageSub").textContent = "数据目录：" + ROOT_DIR;
  $("#rootPath").textContent = ROOT_DIR;

  // 导航
  $$(".nav-item").forEach(a => a.onclick = e => { e.preventDefault(); switchView(a.dataset.view); });
  // 移动端抽屉
  $("#hamburger").onclick = () => { $("#sidebar").classList.add("open"); $("#backdrop").classList.add("show"); };
  $("#backdrop").onclick = closeDrawer;

  // URL 视图
  const v = new URLSearchParams(location.search).get("view");
  const target = v && TITLES[v] ? v : "overview";
  groupsInit();
  if(target !== "overview") switchView(target, false);
  else { state.loaded.overview = true; loadOverview(); }
  armLogTimer();
  window.addEventListener("resize", ()=>{
    if(state.view==="overview") loadOverview();
    else if(state.view==="trends" && state.loaded.trends) loadTrends();
  });
})();
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

    def _origin_allowed(self, headers) -> bool:
        """同源校验：Origin/Referer 存在时必须匹配本服务（防恶意网页偷读隐私数据）。

        浏览器跨站 fetch/资源请求必然携带 Origin 或 Referer（指向恶意站点），
        校验拒绝即可堵住 CSRF/localhost 数据泄露；curl/无头脚本等无浏览器
        上下文的请求不带这两个头，予以放行（不是攻击向量）。
        """
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        for header in ("Origin", "Referer"):
            value = headers.get(header)
            if not value:
                continue
            try:
                parsed = urllib.parse.urlparse(value.strip())
            except ValueError:
                return False
            if parsed.netloc not in allowed:
                return False
        return True

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        root = self.server.data_root

        # 同源校验：跨站请求直接拒绝（隐私数据防偷读）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return

        if path == "/" or path == "/index.html":
            html = PAGE_TEMPLATE.replace("DATA_ROOT", json.dumps(root))
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                             "connect-src 'self'; frame-ancestors 'none'")
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

        if path == "/api/log":
            # 统一运行日志 + 最近几天 errors.log（仪表盘「日志」视图）
            try:
                n = max(10, min(500, int(query.get("n", ["200"])[0])))
            except ValueError:
                n = 200
            try:
                import applog  # noqa: PLC0415
                entries = applog.read_recent(root, n)
                err_days = _available_days(root)[-3:]
                errors = applog.read_errors(root, err_days, n)
            except Exception:  # noqa: BLE001
                entries, errors = [], []
            self._send_json({"entries": entries, "errors": errors})
            return

        if path == "/api/groups":
            # 应用分组管理：内置+自定义分组、全部已知应用及其当前分类
            try:
                import classifier as _clf  # noqa: PLC0415
                config = _clf.load_config(); config["data_root"] = root
                groups = _clf.load_app_groups(root)
                cats = _clf.all_categories(config, groups)
                known = _collect_known_apps(root)
                entries = []
                for exe, name in sorted(known.items(), key=lambda kv: kv[1].lower()):
                    entries.append({
                        "exe": exe,
                        "app": name,
                        "category": _clf.classify_category(exe, "", config),
                        "overridden": exe in groups["exe_groups"],
                    })
                self._send_json({
                    "exe_groups": groups["exe_groups"],
                    "custom_categories": groups["custom_categories"],
                    "categories": cats,
                    "apps": entries,
                })
            except Exception:  # noqa: BLE001
                self._send_json({"error": "groups unavailable"}, 500)
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        self._send_json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        root = self.server.data_root
        # 同源校验（与 GET 一致）
        if not self._origin_allowed(self.headers):
            self._send_json({"error": "forbidden"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            else:
                body = {}
            if not isinstance(body, dict):
                body = {}
        except Exception:  # noqa: BLE001
            body = {}

        try:
            import classifier as _clf  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            self._send_json({"error": "unavailable"}, 500)
            return

        if path == "/api/groups/set":
            # 设置/移出应用分组：{"exe": "steam.exe", "category": "游戏"}；category 为空=移出（自动分类）
            exe = str(body.get("exe", "")).lower()
            cat = str(body.get("category", "")).strip()
            if not exe:
                self._send_json({"error": "exe required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            if cat:
                groups["exe_groups"][exe] = cat
                # 未知分组自动登记为自定义分组
                if cat not in _clf.all_categories(_clf.load_config(), groups):
                    groups["custom_categories"].append(cat)
            else:
                groups["exe_groups"].pop(exe, None)
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True})
            return

        if path == "/api/groups/add":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            cats = _clf.all_categories(_clf.load_config(), groups)
            if name not in cats:
                groups["custom_categories"].append(name)
                _clf.save_app_groups(groups, root)
            self._send_json({"ok": True, "categories": _clf.all_categories(_clf.load_config(), groups)})
            return

        if path == "/api/groups/delete":
            name = str(body.get("name", "")).strip()
            if not name:
                self._send_json({"error": "name required"}, 400)
                return
            groups = _clf.load_app_groups(root)
            groups["custom_categories"] = [x for x in groups["custom_categories"] if x != name]
            groups["exe_groups"] = {k: v for k, v in groups["exe_groups"].items() if v != name}
            _clf.save_app_groups(groups, root)
            self._send_json({"ok": True})
            return

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


def _collect_known_apps(data_root: str) -> dict[str, str]:
    """收集"已知应用"：软件清单 exe + 最近 14 天 usage.jsonl 出现的 exe。

    返回 {exe(小写): 显示名}（供分组管理界面列出全部可分组应用）。
    """
    known: dict[str, str] = {}

    def _add(exe: str, name: str = "") -> None:
        exe = (exe or "").lower()
        if not exe:
            return
        if exe not in known or not known[exe]:
            known[exe] = name or exe

    # 1) 软件清单（今日 + 最近几天）
    for day in _available_days(data_root)[-7:]:
        inv_path = os.path.join(data_root, day, "software_inventory.json")
        if not os.path.isfile(inv_path):
            continue
        try:
            with open(inv_path, "r", encoding="utf-8") as fh:
                inv = json.load(fh)
            for app in inv.get("apps", []):
                if isinstance(app, dict):
                    _add(app.get("exe"), app.get("name", ""))
        except Exception:  # noqa: BLE001
            continue
    # 2) 最近 14 天 usage.jsonl
    for day in _available_days(data_root)[-14:]:
        usage_path = os.path.join(data_root, day, "usage.jsonl")
        if not os.path.isfile(usage_path):
            continue
        try:
            with open(usage_path, "r", encoding="utf-8-sig") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        if isinstance(rec, dict):
                            _add(rec.get("exe"), rec.get("app", ""))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return known


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
    try:
        import applog  # noqa: PLC0415
        applog.configure(data_root)
        applog.get_logger("dashboard").info("仪表盘启动 %s (data_root=%s)", url, data_root)
    except Exception:  # noqa: BLE001
        pass
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
