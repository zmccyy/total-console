// DOM 与格式化工具、纯状态查询
import { state, PALETTE_COLORS } from "./state.js";

export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => Array.from(document.querySelectorAll(sel));

export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function hashColor(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE_COLORS[h % PALETTE_COLORS.length];
}

export function fmtMem(bytes) {
  if (!bytes) return "—";
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(0) + " MB";
  return Math.round(bytes / 1024) + " KB";
}

export function fmtUptime(started) {
  if (!started) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - started));
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60);
  if (d > 0) return d + "天" + h + "时";
  if (h > 0) return h + "时" + m + "分";
  return m + "分" + (secs % 60) + "秒";
}

export const STATUS_ZH = {
  running: "运行中", sleeping: "睡眠", stopped: "已停止", zombie: "僵尸",
  disk_sleep: "磁盘等待", idle: "空闲", lock: "等待", "wake-kill": "唤醒中",
};

export function unitStatusZh(u) {
  if (u.status === "running") return "运行中";
  if (u.status === "exited") return "已退出" + (u.exit_code != null ? " (码 " + u.exit_code + ")" : "");
  return "已停止";
}

export function baseName(p) {
  if (!p) return "—";
  return p.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
}

export function switchView(name) {
  $$(".rail-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
}

// ---------- 纯状态查询 ----------

export function managedUnitForPid(pid) {
  if (!pid || !state.snap) return null;
  return state.snap.units.find((u) => u.status === "running" && u.pid === pid) || null;
}

export function portForUnit(uid) {
  if (!state.snap) return null;
  for (const p of state.snap.ports) {
    if (p.unit_id === uid || (p.pid != null && state.snap.units.find((u) => u.id === uid && u.pid === p.pid))) return p;
  }
  return null;
}

export function findProjectByCwd(cwd) {
  if (!cwd || !state.inv) return null;
  const norm = cwd.replace(/\\/g, "/").toLowerCase();
  let best = null;
  for (const p of state.inv.projects) {
    const pn = p.path.replace(/\\/g, "/").toLowerCase();
    if ((norm === pn || norm.startsWith(pn + "/")) && (!best || pn.length > best.path.length)) best = p;
  }
  return best;
}

// ---------- 定向渲染 ----------

function fromHtml(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

// 内容不变则完全跳过写 DOM（避免每 2s 一帧的无效重排）
export function applyHtml(el, html) {
  if (!el) return;
  if (el.__html === html) return;
  el.__html = html;
  el.innerHTML = html;
}

// 按 key 增量调和子元素：未变化的卡片保留 DOM 节点（拖拽 / 焦点状态不丢）
export function renderKeyed(container, items) {
  if (!container) return;
  const byKey = new Map();
  for (const el of Array.from(container.children)) {
    if (el.__key != null) byKey.set(el.__key, el);
    else el.remove();
  }
  let cursor = null;
  const seen = new Set();
  for (const it of items) {
    let el = byKey.get(it.key);
    if (el && el.__html !== it.html) {
      const fresh = fromHtml(it.html);
      fresh.__key = it.key;
      fresh.__html = it.html;
      el.replaceWith(fresh);
      el = fresh;
    }
    if (!el) {
      el = fromHtml(it.html);
      el.__key = it.key;
      el.__html = it.html;
    }
    seen.add(it.key);
    if (cursor == null) container.prepend(el);
    else cursor.after(el);
    cursor = el;
  }
  for (const [key, el] of byKey) {
    if (!seen.has(key)) el.remove();
  }
}
