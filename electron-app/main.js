// -*- coding: utf-8 -*-
/* main.js — 电脑使用情况监控 · 桌面壳（Electron）
 *
 * 职责：
 *  1. 探测 127.0.0.1:8765 是否已有仪表盘服务在跑（托盘常驻时直接复用）；
 *  2. 没有则由本壳启动 Python 仪表盘服务（dashboard.py，数据引擎仍是 Python）；
 *  3. 创建独立应用窗口加载本地仪表盘（不弹默认浏览器）；
 *  4. 窗口关闭时，若服务是本壳启动的则一并退出（复用则保留）。
 *
 * 环境变量：
 *  USAGEMON_PROJECT_DIR   项目目录（默认本文件上级）
 *  USAGEMON_DATA_ROOT     data_root 覆盖（默认取项目 config.json）
 *  USAGEMON_PORT          仪表盘端口（默认 8765）
 *  USAGEMON_PYTHON        Python 解释器（默认自动探测 py/pythonw/python）
 *
 * 冒烟模式：electron . --smoke [输出png] —— 启动→截图→自动退出（CI/自检用）。
 */
"use strict";

const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const net = require("net");
const path = require("path");
const fs = require("fs");

const SMOKE = process.argv.includes("--smoke");
const SMOKE_OUT = process.argv[process.argv.indexOf("--smoke") + 1] ||
    path.join(__dirname, "smoke.png");
const PORT = Number(process.env.USAGEMON_PORT || 8765);
const URL = `http://127.0.0.1:${PORT}/`;
const DATA_ROOT = process.env.USAGEMON_DATA_ROOT || "";
const PROJECT_DIR = process.env.USAGEMON_PROJECT_DIR ||
    path.resolve(__dirname, "..");

let child = null;
let win = null;

/* ---------- 工具 ---------- */
function projectScript(name) {
  const p = path.join(PROJECT_DIR, name);
  return fs.existsSync(p) ? p : null;
}

function isPortOpen(port, cb) {
  const sock = net.connect({ host: "127.0.0.1", port }, () => {
    sock.destroy();
    cb(true);
  });
  sock.on("error", () => cb(false));
  sock.setTimeout(1500, () => { sock.destroy(); cb(false); });
}

function httpGet(url, cb) {
  const req = http.get(url, (res) => {
    res.resume();
    cb(res.statusCode);
  });
  req.on("error", () => cb(0));
  req.setTimeout(2000, () => { req.destroy(); cb(0); });
}

function waitForDashboard(ms, cb) {
  const start = Date.now();
  const tick = () => {
    httpGet(URL, (code) => {
      if (code === 200) return cb(null);
      if (Date.now() - start > ms) return cb(new Error("仪表盘服务启动超时"));
      setTimeout(tick, 400);
    });
  };
  tick();
}

/* ---------- Python 服务启动 ---------- */
function startDashboard(cb) {
  const script = projectScript("dashboard.py");
  if (!script) return cb(new Error(`dashboard.py 不存在：${script}`));

  const candidates = [
    process.env.USAGEMON_PYTHON,
    "py",
    "pythonw",
    "python",
  ].filter(Boolean);

  const args = [script, "--port", String(PORT)];
  if (DATA_ROOT) args.push("--data-root", DATA_ROOT);

  const tryNext = (i) => {
    if (i >= candidates.length) return cb(new Error("未找到可用的 Python 解释器"));
    const exe = candidates[i];
    const cmd = exe === "py" ? [exe, "-3", ...args] : [exe, ...args];
    const p = spawn(cmd[0], cmd.slice(1), {
      cwd: PROJECT_DIR,
      stdio: "ignore",
      windowsHide: true,
    });
    p.on("error", () => tryNext(i + 1));        // 解释器不存在
    p.on("exit", (code) => {
      if (code && code !== 0) tryNext(i + 1);   // 启动即崩溃
    });
    // 短暂等待确认进程存活
    setTimeout(() => {
      if (p.exitCode === null) { child = p; cb(null); }
    }, 600);
  };
  tryNext(0);
}

/* ---------- 窗口 ---------- */
function createWindow() {
  const icon = projectScript(path.join("assets", "icon.png"));
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 600,
    title: "电脑使用情况监控",
    icon: icon || undefined,
    autoHideMenuBar: true,
    show: false,
    backgroundColor: "#101318",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.once("ready-to-show", () => win.show());
  win.webContents.setBackgroundThrottling(false);
  win.loadURL(URL);
  win.on("closed", () => {
    win = null;
    if (child && child.exitCode === null) {
      child.kill();
      child = null;
    }
    app.quit();
  });
}

/* ---------- 入口 ---------- */
app.whenReady().then(() => {
  if (SMOKE) {
    app.commandLine.appendSwitch("disable-gpu");
  }
  isPortOpen(PORT, (open) => {
    if (open) return createWindow();          // 已有服务（托盘常驻），直接复用
    startDashboard((err) => {
      if (err) {
        if (SMOKE) { console.error("SMOKE FAIL:", err.message); app.exit(1); return; }
        dialog.showErrorBox("电脑使用情况监控",
          `无法启动仪表盘服务：${err.message}\n请确认已安装 Python 3.10+`);
        app.quit();
        return;
      }
      waitForDashboard(15000, (err2) => {
        if (err2) {
          if (SMOKE) { console.error("SMOKE FAIL:", err2.message); app.exit(1); return; }
          dialog.showErrorBox("电脑使用情况监控", `仪表盘服务启动超时：${err2.message}`);
          app.quit();
          return;
        }
        createWindow();
      });
    });
  });
});

/* 冒烟模式：加载完成后截图并退出 */
app.on("browser-window-created", (_e, w) => {
  if (!SMOKE) return;
  w.webContents.on("did-finish-load", () => {
    const grab = async (attempt) => {
      try {
        const img = await w.webContents.capturePage();
        const buf = img.toPNG();
        if (buf.length > 0) {
          fs.writeFileSync(SMOKE_OUT, buf);
          console.log("SMOKE OK ->", SMOKE_OUT);
          app.exit(0);
          return;
        }
        if (attempt < 4) { setTimeout(() => grab(attempt + 1), 1200); return; }
        console.error("SMOKE FAIL: capture empty");
        app.exit(1);
      } catch (e) {
        console.error("SMOKE FAIL:", e.message);
        app.exit(1);
      }
    };
    setTimeout(() => grab(1), 2500);
  });
});

/* 无论何种方式退出，清理本壳启动的 Python 服务 */
app.on("will-quit", () => {
  if (child && child.exitCode === null) {
    child.kill();
    child = null;
  }
});

app.on("window-all-closed", () => app.quit());
