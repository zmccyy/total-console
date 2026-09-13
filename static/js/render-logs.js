// 日志查看：单元日志抽屉与日志中心共用同一套拉取/滚动逻辑
import { state } from "./state.js";
import { $, esc, applyHtml } from "./util.js";
import { getText } from "./api.js";

const LOG_REFRESH_MS = 3000;

async function refreshLogInto(pre, unitId) {
  if (!pre || !unitId) return;
  try {
    const text = await getText("/api/logs/" + encodeURIComponent(unitId));
    if (text == null || pre.textContent === text) return;
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30;
    pre.textContent = text;
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  } catch (e) { /* 连接抖动忽略 */ }
}

// ---------- 日志抽屉 ----------

export async function openLog(unitId, name) {
  state.logUnitId = unitId;
  $("#log-title").textContent = "日志 · " + name;
  $("#log-body").textContent = "加载中…";
  $("#log-drawer").classList.remove("hidden");
  await refreshLogInto($("#log-body"), unitId);
  clearInterval(state.logTimer);
  state.logTimer = setInterval(() => refreshLogInto($("#log-body"), state.logUnitId), LOG_REFRESH_MS);
}

export function closeLog() {
  clearInterval(state.logTimer);
  state.logTimer = null;
  state.logUnitId = null;
  $("#log-drawer").classList.add("hidden");
}

// ---------- 日志中心 ----------

export function openLogCenter(unitId) {
  $("#logcenter").classList.remove("hidden");
  renderLcList();
  const units = (state.snap && state.snap.units) || [];
  const target = unitId || state.lcUnitId || (units[0] && units[0].id);
  if (target) selectLcUnit(target);
  clearInterval(state.lcTimer);
  state.lcTimer = setInterval(refreshLcLog, LOG_REFRESH_MS);
}

export function renderLcList() {
  const units = (state.snap && state.snap.units) || [];
  applyHtml($("#lc-list"), units.map((u) =>
    '<div class="lc-item' + (u.id === state.lcUnitId ? " active" : "") + '" data-id="' + esc(u.id) + '">' +
    '<span class="dot ' + (u.status === "running" ? "on" : "off") + '"></span>' +
    '<span class="lc-name">' + esc(u.name) + "</span>" +
    '<span class="lc-kind">' + (u.kind === "task" ? "任务" : "服务") + "</span></div>"
  ).join("") || '<div class="muted lc-item">还没有命令单元</div>');
}

export async function selectLcUnit(unitId) {
  state.lcUnitId = unitId;
  renderLcList();
  $("#lc-body").textContent = "加载中…";
  await refreshLcLog();
}

async function refreshLcLog() {
  if (!state.lcUnitId || $("#logcenter").classList.contains("hidden")) return;
  await refreshLogInto($("#lc-body"), state.lcUnitId);
}

export function closeLogCenter() {
  clearInterval(state.lcTimer);
  state.lcTimer = null;
  $("#logcenter").classList.add("hidden");
}
