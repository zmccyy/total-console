// 命令面板 Ctrl+K（搜索只过滤展示，不再覆盖完整条目池）
import { state } from "./state.js";
import { $, esc, switchView, applyHtml } from "./util.js";
import { unitAction, refreshSnapshot } from "./actions.js";
import { openUnitTool } from "./handlers.js";
import { openLog, openLogCenter } from "./render-logs.js";
import { openUnitDialog, openDiagnose } from "./dialogs.js";
import { nextTheme, THEME_LABEL } from "./theme.js";

export function buildPaletteItems() {
  const items = [];
  const units = (state.snap && state.snap.units) || [];
  for (const u of units) {
    const kindZh = u.kind === "task" ? "任务" : "服务";
    const st = u.status === "running" ? "运行中" : u.status === "exited" ? "已退出" : "已停止";
    items.push({ label: u.name, hint: kindZh + " · " + st, keywords: u.command + " " + u.workdir,
      action: () => switchView("launchpad") });
    if (u.status === "running") {
      items.push({ label: "停止 " + u.name, hint: kindZh, keywords: "stop",
        action: () => unitAction(u.id, "stop", "确认停止 " + u.name + "？") });
      items.push({ label: "重启 " + u.name, hint: kindZh, keywords: "restart",
        action: () => unitAction(u.id, "restart") });
    } else {
      items.push({ label: "启动 " + u.name, hint: kindZh, keywords: "start run",
        action: () => unitAction(u.id, "start") });
    }
    items.push({ label: "日志 " + u.name, hint: kindZh, keywords: "log",
      action: () => openLog(u.id, u.name) });
    items.push({ label: "诊断 " + u.name, hint: kindZh, keywords: "diagnose",
      action: () => openDiagnose(u.id) });
    if (u.workdir) {
      items.push({ label: "在终端打开 " + u.name, hint: u.workdir, keywords: "terminal cmd",
        action: () => openUnitTool(u.id, "open-terminal", u.name) });
      items.push({ label: "在编辑器打开 " + u.name, hint: u.workdir, keywords: "editor vscode code",
        action: () => openUnitTool(u.id, "open-editor", u.name) });
    }
  }
  items.push({ label: "启动台", hint: "页面", keywords: "launchpad view",
    action: () => switchView("launchpad") });
  items.push({ label: "服务监控", hint: "页面", keywords: "monitor view",
    action: () => switchView("monitor") });
  items.push({ label: "盘点", hint: "页面", keywords: "inventory view",
    action: () => switchView("inventory") });
  items.push({ label: "添加服务", hint: "操作", keywords: "add service new",
    action: () => { switchView("launchpad"); openUnitDialog({ kind: "service" }); } });
  items.push({ label: "添加任务", hint: "操作", keywords: "add task new",
    action: () => { switchView("launchpad"); openUnitDialog({ kind: "task" }); } });
  items.push({ label: "日志中心", hint: "操作", keywords: "log center",
    action: () => openLogCenter() });
  items.push({ label: "批量停止服务", hint: "操作", keywords: "stop all",
    action: () => $("#q-stop-all").click() });
  items.push({ label: "刷新状态", hint: "操作", keywords: "refresh",
    action: () => refreshSnapshot() });
  items.push({ label: "切换主题 → " + THEME_LABEL[nextTheme()], hint: "主题",
    keywords: "theme", action: () => $("#theme-btn").click() });
  return items;
}

export function openPalette() {
  $("#palette").showModal();
  $("#palette-input").value = "";
  state.paletteItems = buildPaletteItems();
  state.paletteIndex = 0;
  renderPalette("");
  $("#palette-input").focus();
}

export function renderPalette(query) {
  const q = query.trim().toLowerCase();
  // 始终从完整条目池过滤；修复旧版过滤后覆盖池子导致搜索结果越搜越少的缺陷
  let items = state.paletteItems;
  if (q) {
    items = items.filter((it) =>
      it.label.toLowerCase().includes(q) || (it.keywords || "").toLowerCase().includes(q));
  }
  items = items.slice(0, 12);
  state.paletteFiltered = items;
  state.paletteIndex = Math.min(state.paletteIndex, Math.max(0, items.length - 1));
  applyHtml($("#palette-list"), items.map((it, i) =>
    '<div class="pal-item' + (i === state.paletteIndex ? " active" : "") + '" data-i="' + i + '">' +
    '<span class="pal-label">' + esc(it.label) + '</span><span class="pal-hint">' + esc(it.hint || "") + "</span></div>"
  ).join("") || '<div class="pal-item muted">无匹配结果</div>');
}

export function execPalette() {
  const it = state.paletteFiltered[state.paletteIndex];
  if (!it) return;
  $("#palette").close();
  it.action();
}
