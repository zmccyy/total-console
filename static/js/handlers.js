// 事件绑定：全局点击分发、控件绑定、拖拽/键盘排序、快捷键
import { api } from "./api.js";
import { state } from "./state.js";
import { $, $$, esc, switchView } from "./util.js";
import { showError, setConn, refreshSnapshot, unitAction, ignorePort } from "./actions.js";
import { hideProcName } from "./actions.js";
import { clearFeed, pushFeed } from "./feed.js";
import { applyTheme, nextTheme } from "./theme.js";
import { renderUnitCards } from "./render-launchpad.js";
import { renderOtherPorts, renderProcs, renderResources, refreshResTabs } from "./render-monitor.js";
import { renderSettings, renderIgnoredPorts } from "./render-inventory.js";
import { openLog, closeLog, openLogCenter, closeLogCenter, renderLcList, selectLcUnit } from "./render-logs.js";
import { openPalette, renderPalette, execPalette } from "./render-palette.js";
import { openUnitDialog, closeUnitDialog, adoptPort, openDiagnose } from "./dialogs.js";

export function bind() {
  document.querySelectorAll(".rail-btn").forEach((b) =>
    b.addEventListener("click", () => switchView(b.dataset.view)));

  $("#add-service-btn").addEventListener("click", () => openUnitDialog({ kind: "service" }));
  $("#add-task-btn").addEventListener("click", () => openUnitDialog({ kind: "task" }));
  $("#refresh-btn").addEventListener("click", refreshSnapshot);
  $("#mon-refresh-btn").addEventListener("click", refreshSnapshot);
  $("#q-refresh").addEventListener("click", refreshSnapshot);
  $("#q-stop-all").addEventListener("click", async () => {
    const running = state.snap.units.filter((u) => u.status === "running");
    if (!running.length) return showError("当前没有运行中的服务");
    if (!confirm("批量停止 " + running.length + " 个运行中的服务？")) return;
    for (const u of running) await unitAction(u.id, "stop");
  });
  $("#feed-clear").addEventListener("click", clearFeed);
  $("#other-ports-toggle").addEventListener("click", () => {
    state.otherPortsOpen = !state.otherPortsOpen;
    renderOtherPorts(state.snap);
  });
  $("#proc-filter").addEventListener("input", () => { if (state.snap) renderProcs(state.snap); });
  $("#res-tab-cpu").addEventListener("click", () => { state.resTab = "cpu"; refreshResTabs(); renderResources(); });
  $("#res-tab-mem").addEventListener("click", () => { state.resTab = "mem"; refreshResTabs(); renderResources(); });

  $("#log-close").addEventListener("click", closeLog);
  $("#refresh-inv-btn").addEventListener("click", async () => {
    $("#refresh-inv-btn").disabled = true;
    $("#inv-scanned").textContent = "盘点中…";
    const res = await api("/api/inventory/refresh", { method: "POST" });
    if (!res.ok) { showError(await res.text()); $("#refresh-inv-btn").disabled = false; }
  });

  $("#svc-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]");
    if (!b) return;
    state.filter.svc = b.dataset.f;
    $$("#svc-filters button").forEach((x) => x.classList.toggle("active", x === b));
    renderUnitCards();
  });
  $("#task-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-f]");
    if (!b) return;
    state.filter.task = b.dataset.f;
    $$("#task-filters button").forEach((x) => x.classList.toggle("active", x === b));
    renderUnitCards();
  });

  $("#add-root-btn").addEventListener("click", () => {
    const v = $("#new-root-input").value.trim();
    if (!v || !state.settings) return;
    if (!state.settings.scan_roots.includes(v)) {
      state.settings.scan_roots.push(v);
      renderSettings();
    }
    $("#new-root-input").value = "";
  });
  $("#scan-roots").addEventListener("click", (e) => {
    const btn = e.target.closest(".del-root");
    if (!btn || !state.settings) return;
    state.settings.scan_roots.splice(Number(btn.dataset.i), 1);
    renderSettings();
  });
  $("#add-watch-btn").addEventListener("click", () => {
    const v = $("#new-watch-input").value.trim();
    if (!v || !state.settings) return;
    state.settings.watch_keywords = state.settings.watch_keywords || [];
    if (!state.settings.watch_keywords.includes(v)) {
      state.settings.watch_keywords.push(v);
      saveSettingsRemote();
    }
    $("#new-watch-input").value = "";
  });
  $("#watch-keywords").addEventListener("click", (e) => {
    const btn = e.target.closest(".del-watch");
    if (!btn || !state.settings) return;
    state.settings.watch_keywords.splice(Number(btn.dataset.i), 1);
    saveSettingsRemote();
  });
  $("#hidden-procs").addEventListener("click", (e) => {
    const btn = e.target.closest(".del-hidden");
    if (!btn || !state.settings) return;
    state.settings.hidden_procs.splice(Number(btn.dataset.i), 1);
    saveSettingsRemote();
  });
  $("#save-settings-btn").addEventListener("click", async () => {
    const poll = Number($("#poll-input").value);
    if (!Number.isFinite(poll) || poll < 1 || poll > 300) return showError("刷新间隔需为 1–300 的整数");
    const res = await api("/api/settings", {
      method: "PUT",
      body: { scan_roots: state.settings.scan_roots, poll_interval: poll,
              notifications: $("#notify-toggle").checked },
    });
    if (!res.ok) return showError(await res.text());
    state.settings = await res.json();
    renderIgnoredPorts();
    showError("设置已保存", 3000);
  });

  $("#theme-btn").addEventListener("click", async () => {
    const next = nextTheme();
    state.settings = Object.assign({}, state.settings, { theme: next });
    applyTheme();
    await api("/api/settings", { method: "PUT", body: { theme: next } });
  });

  // 命令面板 / 日志中心 / 总控台自身控制
  $("#palette-btn").addEventListener("click", openPalette);
  $("#logcenter-btn").addEventListener("click", () => openLogCenter());
  $("#logcenter-close").addEventListener("click", closeLogCenter);
  $("#console-restart-btn").addEventListener("click", () =>
    consoleAction("restart", "重启总控台服务？\n（不会停止启动台里已运行的命令单元）"));
  $("#console-stop-btn").addEventListener("click", () =>
    consoleAction("stop", "停止总控台服务？\n（不会停止启动台里已运行的命令单元，刷新页面可重连若服务仍在运行）"));
  $("#diag-close").addEventListener("click", () => $("#diag-dialog").close());
  $("#diag-edit").addEventListener("click", () => {
    const id = $("#diag-edit").dataset.id;
    $("#diag-dialog").close();
    const u = ((state.snap && state.snap.units) || []).find((x) => x.id === id);
    if (!u) return;
    openUnitDialog({ id: u.id, name: u.name, kind: u.kind, command: u.command,
                     workdir: u.workdir, env: u.env, env_file: u.env_file });
  });
  $("#palette-input").addEventListener("input", (e) => {
    state.paletteIndex = 0;
    renderPalette(e.target.value);
  });
  $("#palette-list").addEventListener("click", (e) => {
    const item = e.target.closest(".pal-item");
    if (!item || item.dataset.i == null) return;
    state.paletteIndex = Number(item.dataset.i);
    execPalette();
  });
  $("#lc-list").addEventListener("click", (e) => {
    const item = e.target.closest(".lc-item[data-id]");
    if (item) selectLcUnit(item.dataset.id);
  });
  bindReorder("service-cards");
  bindReorder("task-cards");

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if ($("#palette").open) $("#palette").close();
      else openPalette();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
      e.preventDefault();
      if ($("#logcenter").classList.contains("hidden")) openLogCenter();
      else closeLogCenter();
      return;
    }
    if ($("#palette").open) {
      if (e.key === "ArrowDown") { e.preventDefault(); state.paletteIndex = Math.min(state.paletteIndex + 1, state.paletteFiltered.length - 1); renderPalette($("#palette-input").value); }
      else if (e.key === "ArrowUp") { e.preventDefault(); state.paletteIndex = Math.max(0, state.paletteIndex - 1); renderPalette($("#palette-input").value); }
      else if (e.key === "Enter") { e.preventDefault(); execPalette(); }
      return;
    }
    const focusedCard = document.activeElement && document.activeElement.classList
      && document.activeElement.classList.contains("unit-card")
      ? document.activeElement : null;
    if (focusedCard && e.ctrlKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      e.preventDefault();
      nudgeUnit(focusedCard, e.key === "ArrowLeft" ? -1 : 1);
    }
  });

  // 全局点击分发（事件委托，卡片/表格里的按钮都在这里处理）
  document.addEventListener("click", (e) => handleClick(e));
}

async function consoleAction(action, confirmText) {
  if (!confirm(confirmText)) return;
  try {
    const res = await api("/api/console/" + action, { method: "POST" });
    if (!res.ok) return showError(await res.text());
    pushFeed(action === "restart" ? "总控台服务重启中…" : "总控台服务停止中…");
    if (action === "stop") setConn(false);
  } catch (e) {
    showError("请求失败：" + e);
  }
}

// 在终端 / 编辑器打开单元工作目录（卡片按钮与命令面板共用）
export async function openUnitTool(unitId, action, name) {
  const res = await api("/api/units/" + unitId + "/" + action, { method: "POST" });
  if (!res.ok) return showError(await res.text());
  const result = await res.json();
  pushFeed((result.feed || "已打开") + " <b>" + esc(name) + "</b>（" + esc(result.tool || "") + "）");
}

// 设置增量保存（关注关键字 / 隐藏进程芯片即时生效，不打断轮询）
async function saveSettingsRemote() {
  const s = state.settings;
  const res = await api("/api/settings", {
    method: "PUT",
    body: { watch_keywords: s.watch_keywords || [], hidden_procs: s.hidden_procs || [],
            ignored_ports: s.ignored_ports || [] },
  });
  if (!res.ok) return showError(await res.text());
  state.settings = await res.json();
  renderSettings();
}

function handleClick(e) {
  const t = e.target;

  const openUrl = t.closest(".act-open-url");
  if (openUrl) {
    // 交给本机默认浏览器打开（后端 os.startfile），不受弹窗拦截影响
    api("/api/open-url", { method: "POST", body: { url: openUrl.dataset.url } })
      .then(async (res) => { if (!res.ok) showError(await res.text()); })
      .catch(() => showError("打开失败"));
    return;
  }

  const copyUrl = t.closest(".act-copy-url");
  if (copyUrl) {
    navigator.clipboard.writeText(copyUrl.dataset.url)
      .then(() => pushFeed("已复制地址 <b>" + esc(copyUrl.dataset.url) + "</b>"))
      .catch(() => showError("复制失败"));
    return;
  }

  const rail = t.closest(".rail-btn");
  if (rail && rail.dataset.action === "logcenter") return openLogCenter();
  if (rail) return switchView(rail.dataset.view);

  const addCard = t.closest(".add-card");
  if (addCard) return openUnitDialog({ kind: addCard.dataset.addKind });

  const start = t.closest(".act-start");
  if (start) return unitAction(start.dataset.id, "start");

  const stop = t.closest(".act-stop");
  if (stop) {
    const u = state.snap.units.find((x) => x.id === stop.dataset.id);
    return unitAction(stop.dataset.id, "stop",
      "将结束以下进程及其子进程树：\n" + (u ? u.name : "") + " (PID " + (u ? u.pid : "?") + ")\n\n确认停止？");
  }

  const logBtn = t.closest(".act-log");
  if (logBtn) return openLog(logBtn.dataset.id, logBtn.dataset.name);

  const diagBtn = t.closest(".act-diag") || t.closest(".health-chip");
  if (diagBtn) return openDiagnose(diagBtn.dataset.id);

  const openTerm = t.closest(".act-open-terminal");
  if (openTerm) {
    const u = state.snap.units.find((x) => x.id === openTerm.dataset.id);
    return openUnitTool(openTerm.dataset.id, "open-terminal", u ? u.name : "");
  }

  const openEdit = t.closest(".act-open-editor");
  if (openEdit) {
    const u = state.snap.units.find((x) => x.id === openEdit.dataset.id);
    return openUnitTool(openEdit.dataset.id, "open-editor", u ? u.name : "");
  }

  const restartBtn = t.closest(".act-restart");
  if (restartBtn) {
    const u = state.snap.units.find((x) => x.id === restartBtn.dataset.id);
    return unitAction(restartBtn.dataset.id, "restart",
      "重启 " + (u ? u.name : "") + "？（先停止再启动）");
  }

  const npAdopt = t.closest(".np-adopt");
  if (npAdopt) return adoptPort(npAdopt.dataset.pid);

  const npIgnore = t.closest(".np-ignore") || t.closest(".ignore-port-btn");
  if (npIgnore) return ignorePort(npIgnore.dataset.port);

  const hideProc = t.closest(".hide-proc-btn");
  if (hideProc) return hideProcName(hideProc.dataset.name);

  const delIgnored = t.closest(".del-ignored");
  if (delIgnored && state.settings) {
    state.settings.ignored_ports.splice(Number(delIgnored.dataset.i), 1);
    renderIgnoredPorts();
    return api("/api/settings", { method: "PUT", body: { ignored_ports: state.settings.ignored_ports } });
  }

  const copy = t.closest(".act-copy");
  if (copy) {
    navigator.clipboard.writeText(copy.dataset.cmd)
      .then(() => pushFeed("已复制命令到剪贴板"))
      .catch(() => showError("复制失败"));
    return;
  }

  const edit = t.closest(".act-edit");
  if (edit) {
    const u = state.snap.units.find((x) => x.id === edit.dataset.id);
    if (!u) return;
    if (u.status === "running") return showError("单元运行中，先停止再编辑");
    return openUnitDialog({ id: u.id, name: u.name, kind: u.kind, command: u.command,
                            workdir: u.workdir, env: u.env, env_file: u.env_file });
  }

  const del = t.closest(".act-del");
  if (del) {
    const u = state.snap.units.find((x) => x.id === del.dataset.id);
    if (!u) return;
    if (!confirm("删除命令 \"" + u.name + "\"？（不影响已运行的进程）")) return;
    return api("/api/units/" + del.dataset.id, { method: "DELETE" }).then(async (res) => {
      if (!res.ok) showError(await res.text());
      return refreshSnapshot();
    });
  }

  const adopt = t.closest(".adopt-btn");
  if (adopt) return adoptPort(adopt.dataset.pid);

  const fromProj = t.closest(".add-from-project");
  if (fromProj) {
    const p = state.inv.projects[Number(fromProj.dataset.idx)];
    if (!p) return;
    return openUnitDialog({
      name: p.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop(),
      command: p.candidates.length ? p.candidates[0].command : "",
      workdir: p.path,
    });
  }

  const addCand = t.closest(".add-candidate");
  if (addCand) {
    const p = state.inv.projects[Number(addCand.dataset.idx)];
    if (!p) return;
    return openUnitDialog({ name: p.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop(), command: addCand.dataset.cmd, workdir: p.path });
  }
}

// ---------- 拖拽 / 键盘排序 ----------

function bindReorder(containerId) {
  const root = document.getElementById(containerId);
  if (!root) return;
  root.addEventListener("dragstart", (e) => {
    const card = e.target.closest(".unit-card");
    if (!card) return;
    state.dragId = card.dataset.id;
    card.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    try { e.dataTransfer.setData("text/plain", state.dragId); } catch (er) { /* IE */ }
  });
  root.addEventListener("dragend", () => {
    $$(".unit-card").forEach((c) => c.classList.remove("dragging", "drop-before"));
    state.dragId = null;
  });
  root.addEventListener("dragover", (e) => {
    if (!state.dragId) return;
    e.preventDefault();
    const over = e.target.closest(".unit-card");
    if (!over || over.dataset.id === state.dragId) return;
    $$(".unit-card").forEach((c) => c.classList.remove("drop-before"));
    over.classList.add("drop-before");
  });
  root.addEventListener("drop", async (e) => {
    if (!state.dragId) return;
    e.preventDefault();
    const over = e.target.closest(".unit-card");
    $$(".unit-card").forEach((c) => c.classList.remove("dragging", "drop-before"));
    if (!over || over.dataset.id === state.dragId) return;
    await moveUnit(state.dragId, over.dataset.id);
  });
}

function orderedIdsForKind(kind) {
  return ((state.snap && state.snap.units) || []).filter((u) => u.kind === kind).map((u) => u.id);
}

async function moveUnit(dragId, beforeId) {
  const units = (state.snap && state.snap.units) || [];
  const kind = (units.find((u) => u.id === dragId) || {}).kind;
  if (!kind) return;
  const ids = orderedIdsForKind(kind).filter((id) => id !== dragId);
  const idx = ids.indexOf(beforeId);
  if (idx < 0) return;
  ids.splice(idx, 0, dragId);
  const otherKind = kind === "service" ? "task" : "service";
  const res = await api("/api/units/reorder", { method: "POST", body: { ids: ids.concat(orderedIdsForKind(otherKind)) } });
  if (!res.ok) return showError(await res.text());
  await refreshSnapshot();
}

async function nudgeUnit(cardEl, dir) {
  const id = cardEl.dataset.id;
  const units = (state.snap && state.snap.units) || [];
  const u = units.find((x) => x.id === id);
  if (!u) return;
  const ids = orderedIdsForKind(u.kind);
  const i = ids.indexOf(id);
  const j = i + dir;
  if (j < 0 || j >= ids.length) return;
  ids.splice(j, 0, ids.splice(i, 1)[0]);
  const otherKind = u.kind === "service" ? "task" : "service";
  const res = await api("/api/units/reorder",
    { method: "POST", body: { ids: ids.concat(orderedIdsForKind(otherKind)) } });
  if (!res.ok) return showError(await res.text());
  await refreshSnapshot();
}
