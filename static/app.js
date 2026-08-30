"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  token: null,
  snap: null,
  inv: null,
  settings: null,
  filter: { svc: "all", task: "all" },
  knownPorts: new Set(),
  firstSnap: true,
  prevUnits: {},
  feed: [],
  feedCleared: false,
  otherPortsOpen: false,
  resTab: "cpu",
  logTimer: null,
  logUnitId: null,
  editingUnitId: null,
  claimPid: null,
  paletteItems: [],
  paletteIndex: 0,
  lcUnitId: null,
  lcTimer: null,
  dragId: null,
  notifiedExit: new Set(),
};

const PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#a78bfa", "#22d3ee", "#f472b6", "#ef4444", "#60a5fa"];

/* ---------- 工具 ---------- */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function hashColor(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function fmtMem(bytes) {
  if (!bytes) return "—";
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(0) + " MB";
  return Math.round(bytes / 1024) + " KB";
}

function fmtUptime(started) {
  if (!started) return "—";
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - started));
  const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60);
  if (d > 0) return d + "天" + h + "时";
  if (h > 0) return h + "时" + m + "分";
  return m + "分" + (secs % 60) + "秒";
}

const STATUS_ZH = {
  running: "运行中", sleeping: "睡眠", stopped: "已停止", zombie: "僵尸",
  disk_sleep: "磁盘等待", idle: "空闲", lock: "等待", "wake-kill": "唤醒中",
};

function unitStatusZh(u) {
  if (u.status === "running") return "运行中";
  if (u.status === "exited") return "已退出" + (u.exit_code != null ? " (码 " + u.exit_code + ")" : "");
  return "已停止";
}

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers["X-Ops-Token"] = state.token;
  if (opts.body) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.body); }
  return fetch(path, Object.assign({}, opts, { headers }));
}

function showError(text) {
  const el = $("#alerts");
  if (el) { el.textContent = text; }
  clearTimeout(showError._t);
  showError._t = setTimeout(() => { if (state.snap && !state.snap.error) el.textContent = "运行良好，暂无告警"; }, 8000);
}

function setConn(on) {
  $("#conn-dot").className = "dot " + (on ? "on" : "off");
  $("#conn-dot").title = on ? "已连接" : "连接断开";
}

function pushFeed(html) {
  if (state.feedCleared) return;
  const t = new Date().toTimeString().slice(0, 5);
  state.feed.push({ t, html });
  if (state.feed.length > 30) state.feed.shift();
  renderFeed();
}

function renderFeed() {
  $("#feed").innerHTML = state.feed.map((f) =>
    '<div class="feed-item"><span class="t">' + f.t + '</span><span class="ev">' + f.html + "</span></div>"
  ).join("") || '<div class="feed-item"><span class="ev muted">暂无动态</span></div>';
}

/* ---------- 视图切换 ---------- */

function switchView(name) {
  $$(".rail-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + name));
}

/* ---------- 单元辅助 ---------- */

function managedUnitForPid(pid) {
  if (!pid || !state.snap) return null;
  return state.snap.units.find((u) => u.status === "running" && u.pid === pid) || null;
}

function portForUnit(uid) {
  if (!state.snap) return null;
  for (const p of state.snap.ports) {
    if (p.unit_id === uid || (p.pid != null && state.snap.units.find((u) => u.id === uid && u.pid === p.pid))) return p;
  }
  return null;
}

/* ---------- 主快照渲染 ---------- */

function renderSnapshot(snap) {
  state.snap = snap;
  const units = snap.units || [];

  // 统计卡（两页共用一套快照）
  $("#stat-units").textContent = units.length;
  $("#stat-running").textContent = units.filter((u) => u.status === "running").length;
  $("#stat-tasks").textContent = units.filter((u) => u.kind === "task").length;
  $("#stat-cpu").textContent = snap.cpu != null ? snap.cpu + "%" : "—";
  $("#stat-mem").textContent = snap.mem != null ? snap.mem + "%" : "—";
  $("#stat-cpu-m").textContent = snap.cpu != null ? snap.cpu + "%" : "—";
  $("#stat-mem-m").textContent = snap.mem != null ? snap.mem + "%" : "—";
  $("#stat-ts").textContent = snap.ts ? snap.ts.slice(11, 19) : "—";
  $("#poll-badge").textContent = (snap.poll_interval || 5) + "s";
  $("#top-summary").textContent =
    "运行中 " + units.filter((u) => u.status === "running").length +
    " · 服务 " + units.filter((u) => u.kind === "service").length;
  if (snap.error) showError(snap.error);
  else $("#alerts").textContent = "运行良好，暂无告警";

  const projPorts = (snap.ports || []).filter((p) => p.origin !== "other");
  $("#stat-port").textContent = projPorts.length;
  $("#stat-proj-ports").textContent = projPorts.length;
  $("#stat-mysvc").textContent = units.filter((u) => u.status === "running").length;

  trackNewPorts(snap, projPorts);
  trackUnitChanges(units);
  notifyUnitExits(units);

  renderUnitCards();
  renderNewPorts(snap);
  renderProjectPorts(snap, projPorts);
  renderOtherPorts(snap);
  renderProcs(snap);
  renderTopPorts();
  renderResources();
}

function trackNewPorts(snap, projPorts) {
  if (state.firstSnap) {
    projPorts.forEach((p) => state.knownPorts.add(p.addr));
    state.firstSnap = false;
    return;
  }
  for (const p of projPorts) {
    if (!state.knownPorts.has(p.addr)) {
      state.knownPorts.add(p.addr);
      const owner = p.origin === "managed" ? "已管理服务" : "项目";
      pushFeed('<b>' + esc(p.addr) + "</b> 已就绪（" + owner + "）");
    }
  }
}

function trackUnitChanges(units) {
  for (const u of units) {
    const prev = state.prevUnits[u.id];
    if (!prev) continue;
    if (prev !== u.status) {
      if (u.status === "running") {
        const port = portForUnit(u.id);
        pushFeed('<b>' + esc(u.name) + '</b> 已启动' + (port ? ' :' + esc(port.addr.split(":")[1]) : ""));
      } else if (u.status === "exited") {
        pushFeed('<b>' + esc(u.name) + '</b> 已结束（码 ' + (u.exit_code != null ? u.exit_code : "未知") + '）');
      } else {
        pushFeed('<b>' + esc(u.name) + '</b> 已停止');
      }
    }
  }
  state.prevUnits = {};
  units.forEach((u) => { state.prevUnits[u.id] = u.status; });
}

/* ---------- 启动台卡片 ---------- */

function unitIcon(u) {
  const letter = (u.name || "?").trim().charAt(0).toUpperCase();
  const color = hashColor(u.name);
  return '<span class="card-icon" style="background:' + color + '">' + esc(letter) + "</span>";
}

function unitCard(u) {
  const running = u.status === "running";
  const port = running ? portForUnit(u.id) : null;
  const claimed = u.owned === "claimed";
  const healthBad = u.health && u.health.status === "error" && !running;
  const statusLine = running
    ? '<span class="status running">运行中</span>' + (claimed ? '<span class="tag claimed">已认领</span>' : "") +
      (port ? '<span class="port-chip">:' + esc(port.addr.split(":")[1]) + "</span>" : "") +
      '<span class="muted">PID ' + u.pid + " · " + fmtUptime(u.last_started_at) + "</span>"
    : u.status === "exited"
      ? '<span class="status exited">' + esc(unitStatusZh(u)) + "</span>"
      : '<span class="status stopped">已停止</span>';
  const healthChip = healthBad
    ? '<button class="chip health-chip" data-id="' + esc(u.id) + '" title="' +
      esc(u.health.issues.map((i) => i.title).join("；")) + '">⚠ 需检查 ' + u.health.issues.length + "</button>"
    : "";
  return (
    '<div class="unit-card" data-id="' + esc(u.id) + '" draggable="true" tabindex="0">' +
      '<div class="card-head">' +
        unitIcon(u) +
        '<div class="card-title"><div class="card-name" title="' + esc(u.name) + '">' + esc(u.name) + "</div>" +
        '<div class="card-status">' + statusLine + "</div></div>" +
        '<span class="drag-grip" title="拖拽排序">⋮⋮</span>' +
      "</div>" +
      (healthChip ? '<div class="card-health">' + healthChip + "</div>" : "") +
      '<div class="card-cmd" title="' + esc(u.command) + '">' + esc(u.command) + "</div>" +
      '<div class="card-actions">' +
        (running
          ? '<button class="big-btn stop act-stop" data-id="' + esc(u.id) + '">停止</button>'
          : '<button class="big-btn start act-start" data-id="' + esc(u.id) + '">启动</button>') +
        '<button class="icon-btn act-copy" data-cmd="' + esc(u.command) + '" title="复制命令">⧉</button>' +
        '<button class="icon-btn act-log" data-id="' + esc(u.id) + '" data-name="' + esc(u.name) + '" title="日志">▤</button>' +
        '<button class="icon-btn act-diag" data-id="' + esc(u.id) + '" title="诊断">⚑</button>' +
        (running
          ? '<button class="icon-btn act-restart" data-id="' + esc(u.id) + '" title="重启">↻</button>'
          : "") +
        '<button class="icon-btn act-edit" data-id="' + esc(u.id) + '" title="编辑">✎</button>' +
        '<button class="icon-btn act-del" data-id="' + esc(u.id) + '" title="删除">×</button>' +
      "</div>" +
    "</div>"
  );
}

const ADD_CARD = (id, kind) =>
  '<div class="unit-card empty-card add-card" data-add-kind="' + kind + '" id="' + id + '">' +
  '<span class="plus">＋</span><span>添加' + (kind === "task" ? "任务" : "服务") + "</span></div>";

function renderUnitCards() {
  const units = (state.snap && state.snap.units) || [];
  const svc = units.filter((u) => u.kind === "service");
  const task = units.filter((u) => u.kind === "task");
  const picks = (list, f, counts) => {
    let out = list;
    if (f !== "all") out = list.filter((u) => u.status === f);
    if (f === "exited") out = list.filter((u) => u.status === "exited");
    if (f === "stopped") out = list.filter((u) => u.status === "stopped");
    if (f === "running") out = list.filter((u) => u.status === "running");
    void counts;
    return out;
  };
  // 计数徽标
  const badge = (list, st) => list.filter((u) => u.status === st).length;
  $("#svc-count").textContent = svc.length;
  $("#task-count").textContent = task.length;
  $("#f-all").textContent = svc.length;
  $("#f-running").textContent = badge(svc, "running");
  $("#f-stopped").textContent = badge(svc, "stopped");
  $("#f-exited").textContent = badge(svc, "exited");
  $("#tf-all").textContent = task.length;
  $("#tf-running").textContent = badge(task, "running");
  $("#tf-stopped").textContent = badge(task, "stopped");
  $("#tf-exited").textContent = badge(task, "exited");

  const svcList = picks(svc, state.filter.svc);
  const taskList = picks(task, state.filter.task);
  $("#service-cards").innerHTML = svcList.map(unitCard).join("") + (svcList.length ? "" : ADD_CARD(null, "service"));
  $("#task-cards").innerHTML = taskList.map(unitCard).join("") + (taskList.length < 3 ? ADD_CARD(null, "task") : "");
}

/* ---------- 服务监控：端口表 ---------- */

function renderProjectPorts(snap, projPorts) {
  const visible = projPorts.filter((p) => !p.isNew);
  $("#proj-port-count").textContent = visible.length;
  const procCpu = {};
  (snap.procs || []).forEach((p) => { procCpu[p.pid] = p; });
  const tbody = $("#project-ports-table tbody");
  tbody.innerHTML = visible.map((p) => {
    const unit = p.origin === "managed"
      ? ((snap.units || []).find((u) => u.id === p.unit_id) || managedUnitForPid(p.pid))
      : null;
    const proc = procCpu[p.pid];
    const origin = unit
      ? '<span class="tag managed">已管理 · ' + esc(unit.name) + "</span>"
      : '<span class="tag project">项目 · ' + esc(baseName(p.project)) + "</span>";
    const originTag = p.origin_label
      ? '<span class="tag origin origin-' + esc(p.origin_label.icon) + '" title="启动者溯源">' +
        esc(ORIGIN_ICONS[p.origin_label.icon] || "▸") + " " + esc(p.origin_label.label) + "</span>"
      : '<span class="muted">—</span>';
    const actions = unit
      ? '<button class="ghost act-log" data-id="' + esc(unit.id) + '" data-name="' + esc(unit.name) + '">日志</button>'
      : '<button class="ghost adopt-btn" data-pid="' + esc(p.pid) + '">加入启动台</button>' +
        '<button class="ghost ignore-port-btn" data-port="' + esc(p.addr.split(":")[1]) + '">忽略</button>';
    return (
      "<tr>" +
      "<td>" + esc(p.proc || "（不可见）") + "</td>" +
      '<td class="mono">' + esc(p.addr) + "</td>" +
      "<td>" + origin + "</td>" +
      "<td>" + originTag + "</td>" +
      '<td class="num">' + (proc ? proc.cpu : "—") + "</td>" +
      '<td class="num">' + (proc ? fmtUptime(proc.started) : "—") + "</td>" +
      "<td>" + actions + "</td>" +
      "</tr>"
    );
  }).join("") || '<tr><td colspan="7" class="muted">暂未发现项目相关端口。启动台里运行的服务或与项目目录关联的进程端口会出现在这里。</td></tr>';
}

const ORIGIN_ICONS = { bot: "🤖", code: "⌨", terminal: "▸", rocket: "🚀", server: "▦", package: "▣" };

function renderOtherPorts(snap) {
  const others = (snap.ports || []).filter((p) => p.origin === "other");
  $("#other-port-count").textContent = others.length;
  $("#other-ports-table tbody").innerHTML = others.map((p) =>
    "<tr>" +
    '<td class="mono">' + esc(p.addr) + "</td>" +
    '<td class="mono">' + esc(p.proto) + "</td>" +
    "<td>" + esc(p.proc || "（不可见）") + "</td>" +
    '<td class="num">' + (p.pid || "—") + "</td>" +
    "</tr>"
  ).join("") || '<tr><td colspan="4" class="muted">没有其他端口</td></tr>';
  $("#other-ports-body").classList.toggle("hidden", !state.otherPortsOpen);
  $("#other-ports-toggle").textContent = state.otherPortsOpen ? "收起" : "展开";
}

function renderProcs(snap) {
  const filter = $("#proc-filter").value.trim().toLowerCase();
  let list = (snap.procs || []).filter((p) => {
    if (!filter) return true;
    return p.name.toLowerCase().includes(filter)
      || String(p.pid).includes(filter)
      || (p.cmdline || "").toLowerCase().includes(filter);
  });
  list = list.slice().sort((a, b) => b.cpu - a.cpu).slice(0, 300);
  const portOwners = new Map();
  (snap.ports || []).forEach((p) => { if (p.pid) portOwners.set(p.pid, p); });
  const unitById = new Map((snap.units || []).map((u) => [u.id, u]));
  $("#procs-table tbody").innerHTML = list.map((p) => {
    const port = portOwners.get(p.pid);
    const badges = [];
    if (port) badges.push('<span class="tag">端口·' + esc(port.addr.split(":")[1]) + "</span>");
    const u = (port && port.unit_id && unitById.get(port.unit_id)) || managedUnitForPid(p.pid);
    if (u) badges.push('<span class="tag managed">' + esc(u.name) + "</span>");
    return (
      "<tr>" +
      "<td>" + esc(p.name) + badges.join(" ") + "</td>" +
      '<td class="num">' + p.pid + "</td>" +
      '<td class="num">' + (p.cpu != null ? p.cpu : "—") + "</td>" +
      '<td class="num">' + fmtMem(p.rss) + "</td>" +
      "<td>" + esc(STATUS_ZH[p.status] || p.status || "—") + "</td>" +
      '<td class="num">' + fmtUptime(p.started) + "</td>" +
      "<td>" + esc(p.username || "—") + "</td>" +
      "</tr>");
  }).join("");
}

/* ---------- 侧栏 ---------- */

function topPorts(limit) {
  const snap = state.snap || {};
  const procCpu = {};
  (snap.procs || []).forEach((p) => { procCpu[p.pid] = p; });
  return (snap.ports || [])
    .filter((p) => p.origin !== "other" && p.pid != null && procCpu[p.pid])
    .map((p) => ({ p, proc: procCpu[p.pid] }))
    .sort((a, b) => b.proc.cpu - a.proc.cpu)
    .slice(0, limit);
}

function renderTopPorts() {
  const items = topPorts(5);
  const html = items.map(({ p, proc }, i) =>
    '<div class="side-row"><span class="rank">' + (i + 1) + "</span>" +
    '<span class="name">' + esc(p.addr) + (managedUnitForPid(p.pid) ? " · " + esc(managedUnitForPid(p.pid).name) : "") + "</span>" +
    '<span class="val">' + proc.cpu + "%</span></div>"
  ).join("") || '<div class="side-body">暂无项目端口</div>';
  $("#side-ports").innerHTML = html;
  $("#side-ports-m").innerHTML = html;
}

function renderResources() {
  const snap = state.snap || {};
  let list = (snap.procs || []).slice();
  if (state.resTab === "cpu") list.sort((a, b) => b.cpu - a.cpu);
  else list.sort((a, b) => b.rss - a.rss);
  $("#side-resources").innerHTML = list.slice(0, 5).map((p, i) =>
    '<div class="side-row"><span class="rank">' + (i + 1) + "</span>" +
    '<span class="name">' + esc(p.name) + "</span>" +
    '<span class="val">' + (state.resTab === "cpu" ? p.cpu + "%" : fmtMem(p.rss)) + "</span></div>"
  ).join("");
}

/* ---------- 项目归属 / 加入启动台 ---------- */

function baseName(p) {
  if (!p) return "—";
  return p.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
}

async function adoptPort(pid) {
  const res = await fetch("/api/proc-context?pid=" + pid);
  const ctx = res.ok ? await res.json() : { cmdline: "", cwd: null };
  const proj = findProjectByCwd(ctx.cwd);
  state.claimPid = pid;
  openUnitDialog({
    name: baseName(ctx.cwd) || "port-" + pid,
    command: proj && proj.candidates.length ? proj.candidates[0].command : "",
    workdir: proj ? proj.path : "",
  });
  updateFeedNote();
}

function updateFeedNote() { /* 预留 */ }

function findProjectByCwd(cwd) {
  if (!cwd || !state.inv) return null;
  const norm = cwd.replace(/\\/g, "/").toLowerCase();
  let best = null;
  for (const p of state.inv.projects) {
    const pn = p.path.replace(/\\/g, "/").toLowerCase();
    if ((norm === pn || norm.startsWith(pn + "/")) && (!best || pn.length > best.path.length)) best = p;
  }
  return best;
}

/* ---------- 盘点 ---------- */

function renderInventory() {
  const inv = state.inv;
  if (!inv) return;
  $("#inv-scanned").textContent = "上次盘点 " + (inv.scanned_at || "—") + " · 扫描根 " + inv.scan_roots.length + " 个";
  $("#agents-table tbody").innerHTML = inv.agents.map((a) =>
    "<tr>" + "<td>" + esc(a.name) + "</td>" +
    '<td class="mono">' + esc(a.version || "未知") + "</td>" +
    '<td class="mono cmd" title="' + esc(a.path || "") + '">' + esc(a.path || "未安装") + "</td>" +
    '<td class="mono">' + (a.config_dir_exists ? "存在" : "—") + "</td>" +
    "<td>" + (a.logged_in ? '<span class="tag managed">已登录</span>'
      : (a.found ? '<span class="tag">未登录</span>' : '<span class="tag">未安装</span>')) + "</td></tr>"
  ).join("");
  $("#projects-table tbody").innerHTML = inv.projects.map((p, idx) =>
    "<tr>" +
    '<td class="mono cmd" title="' + esc(p.path) + '">' + esc(p.path) + "</td>" +
    "<td>" + esc(p.type) + (p.has_git ? " · git" : "") + "</td>" +
    "<td>" + p.candidates.map((c) =>
      '<button class="chip add-candidate" data-idx="' + idx + '" data-cmd="' + esc(c.command) + '">' + esc(c.label) + "</button>"
    ).join("") + "</td>" +
    '<td><button class="ghost add-from-project" data-idx="' + idx + '">加入启动台</button></td>' +
    "</tr>"
  ).join("") || '<tr><td colspan="4" class="muted">扫描根内尚未发现项目。可在右侧设置中添加 E:\项目 这类目录。</td></tr>';
}

function updateProjectSelect() {
  const sel = $("#unit-project-select");
  const cur = sel.value;
  sel.innerHTML = '<option value="">— 手动填写 —</option>' +
    (state.inv ? state.inv.projects.map((p) =>
      '<option value="' + esc(p.path) + '">' + esc(p.path) + "</option>").join("") : "");
  sel.value = cur || "";
}

/* ---------- 设置 / 主题 ---------- */

function renderSettings() {
  const s = state.settings;
  if (!s) return;
  $("#poll-input").value = s.poll_interval;
  $("#scan-roots").innerHTML = s.scan_roots.map((r, i) =>
    '<span class="chip root-chip">' + esc(r) + '<button class="del-root" data-i="' + i + '">×</button></span>'
  ).join("");
  $("#notify-toggle").checked = s.notifications !== false;
  renderIgnoredPorts();
}

function renderIgnoredPorts() {
  const el = $("#ignored-ports");
  const list = (state.settings && state.settings.ignored_ports) || [];
  el.innerHTML = list.map((p, i) =>
    '<span class="chip root-chip">:' + esc(p) + '<button class="del-ignored" data-i="' + i + '">×</button></span>'
  ).join("") || '<span class="muted">无</span>';
}

async function loadSettings() {
  const res = await fetch("/api/settings");
  if (res.ok) state.settings = await res.json();
}

const THEMES = ["system", "light", "dark"];
function nextTheme() {
  const cur = (state.settings && state.settings.theme) || "system";
  return THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
}
function applyTheme() {
  document.documentElement.dataset.theme = state.settings ? state.settings.theme : "system";
}

/* ---------- 日志抽屉 ---------- */

async function openLog(unitId, name) {
  state.logUnitId = unitId;
  $("#log-title").textContent = "日志 · " + name;
  $("#log-body").textContent = "加载中…";
  $("#log-drawer").classList.remove("hidden");
  await refreshLog();
  clearInterval(state.logTimer);
  state.logTimer = setInterval(refreshLog, 3000);
}

async function refreshLog() {
  if (!state.logUnitId) return;
  try {
    const res = await fetch("/api/logs/" + encodeURIComponent(state.logUnitId));
    $("#log-body").textContent = res.ok ? await res.text() : "(" + res.status + ")";
    $("#log-body").scrollTop = $("#log-body").scrollHeight;
  } catch (e) { /* 连接抖动忽略 */ }
}

function closeLog() {
  clearInterval(state.logTimer);
  state.logTimer = null;
  state.logUnitId = null;
  $("#log-drawer").classList.add("hidden");
}

/* ---------- 对话框 ---------- */

function openUnitDialog(opts = {}) {
  state.editingUnitId = opts.id || null;
  $("#unit-dialog-title").textContent = opts.id ? "编辑命令" : "添加命令";
  updateProjectSelect();
  $("#unit-name").value = opts.name || "";
  $("#unit-command").value = opts.command || "";
  $("#unit-workdir").value = opts.workdir || "";
  const kind = opts.kind || "service";
  $$('input[name="unit-kind"]').forEach((r) => { r.checked = r.value === kind; });
  $("#unit-candidates").innerHTML = "";
  $("#unit-project-select").value = opts.workdir || "";
  $("#unit-dialog").showModal();
}

function closeUnitDialog() {
  $("#unit-dialog").close();
  state.editingUnitId = null;
  state.claimPid = null;
}

$("#unit-project-select").addEventListener("change", () => {
  const path = $("#unit-project-select").value;
  if (!path) return;
  const proj = ((state.inv && state.inv.projects) || []).find((x) => x.path === path);
  if (!proj) return;
  $("#unit-workdir").value = proj.path;
  $("#unit-candidates").innerHTML = proj.candidates.map((c) =>
    '<button type="button" class="chip" data-cmd="' + esc(c.command) + '">' + esc(c.label + " · " + c.command) + "</button>"
  ).join("");
});

$("#unit-candidates").addEventListener("click", (e) => {
  const btn = e.target.closest(".chip");
  if (btn) $("#unit-command").value = btn.dataset.cmd;
});

$("#unit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    name: $("#unit-name").value.trim(),
    kind: document.querySelector('input[name="unit-kind"]:checked').value,
    command: $("#unit-command").value.trim(),
    workdir: $("#unit-workdir").value.trim(),
  };
  if (!body.name || !body.command) return;
  const claimPid = state.claimPid;
  state.claimPid = null;
  const path = state.editingUnitId ? "/api/units/" + state.editingUnitId : "/api/units";
  const res = await api(path, { method: state.editingUnitId ? "PUT" : "POST", body });
  if (res.ok) {
    const saved = await res.json();
    const wasEdit = !!state.editingUnitId;
    closeUnitDialog();
    if (claimPid) {
      // 「加入启动台」：认领当前监听进程为本单元受管进程（原子校验）
      const claimRes = await api("/api/units/" + saved.id + "/claim",
        { method: "POST", body: { pid: Number(claimPid) } });
      if (!claimRes.ok) showError(await claimRes.text());
      else pushFeed("已认领进程 <b>PID " + esc(claimPid) + "</b> → " + esc(saved.name));
    } else if (!wasEdit) {
      pushFeed("已保存命令 <b>" + esc(saved.name) + "</b>");
    }
    state.firstSnap = true;
    await refreshSnapshot();
  } else showError(await res.text());
});

$("#unit-cancel").addEventListener("click", closeUnitDialog);

/* ---------- 动作 ---------- */

async function refreshSnapshot() {
  const res = await fetch("/api/snapshot");
  if (res.ok) renderSnapshot(await res.json());
}

async function unitAction(id, action, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  if (action === "start") ensureNotifyPermission();
  const res = await api("/api/units/" + id + "/" + action, { method: "POST" });
  if (!res.ok) showError(await res.text());
  await refreshSnapshot();
}

document.addEventListener("click", async (e) => {
  const t = e.target;

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

  const restart = null; // 启动台大按钮已覆盖启停；重启在编辑内

  const logBtn = t.closest(".act-log");
  if (logBtn) return openLog(logBtn.dataset.id, logBtn.dataset.name);

  const diagBtn = t.closest(".act-diag") || t.closest(".health-chip");
  if (diagBtn) return openDiagnose(diagBtn.dataset.id);

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

  const delIgnored = t.closest(".del-ignored");
  if (delIgnored && state.settings) {
    state.settings.ignored_ports.splice(Number(delIgnored.dataset.i), 1);
    renderIgnoredPorts();
    return api("/api/settings", { method: "PUT", body: { ignored_ports: state.settings.ignored_ports } });
  }

  const copy = t.closest(".act-copy");
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.cmd); pushFeed("已复制命令到剪贴板"); } catch (er) { showError("复制失败"); }
    return;
  }

  const edit = t.closest(".act-edit");
  if (edit) {
    const u = state.snap.units.find((x) => x.id === edit.dataset.id);
    if (!u) return;
    if (u.status === "running") return showError("单元运行中，先停止再编辑");
    return openUnitDialog({ id: u.id, name: u.name, kind: u.kind, command: u.command, workdir: u.workdir });
  }

  const del = t.closest(".act-del");
  if (del) {
    const u = state.snap.units.find((x) => x.id === del.dataset.id);
    if (!u) return;
    if (!confirm("删除命令 \"" + u.name + "\"？（不影响已运行的进程）")) return;
    const res = await api("/api/units/" + del.dataset.id, { method: "DELETE" });
    if (!res.ok) showError(await res.text());
    return refreshSnapshot();
  }

  const adopt = t.closest(".adopt-btn");
  if (adopt) return adoptPort(adopt.dataset.pid);

  const fromProj = t.closest(".add-from-project");
  if (fromProj) {
    const p = state.inv.projects[Number(fromProj.dataset.idx)];
    if (!p) return;
    return openUnitDialog({
      name: baseName(p.path),
      command: p.candidates.length ? p.candidates[0].command : "",
      workdir: p.path,
    });
  }

  const addCand = t.closest(".add-candidate");
  if (addCand) {
    const p = state.inv.projects[Number(addCand.dataset.idx)];
    if (!p) return;
    return openUnitDialog({ name: baseName(p.path), command: addCand.dataset.cmd, workdir: p.path });
  }
});

/* ---------- 绑定 ---------- */

function bind() {
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
  $("#feed-clear").addEventListener("click", () => { state.feed = []; state.feedCleared = true; renderFeed(); });
  $("#other-ports-toggle").addEventListener("click", () => { state.otherPortsOpen = !state.otherPortsOpen; renderOtherPorts(state.snap); });
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

  $("#add-root-btn").addEventListener("click", async () => {
    const v = $("#new-root-input").value.trim();
    if (!v || !state.settings) return;
    if (!state.settings.scan_roots.includes(v)) {
      state.settings.scan_roots.push(v);
      renderSettings();
    }
    $("#new-root-input").value = "";
  });
  $("#scan-roots").addEventListener("click", async (e) => {
    const btn = e.target.closest(".del-root");
    if (!btn || !state.settings) return;
    state.settings.scan_roots.splice(Number(btn.dataset.i), 1);
    renderSettings();
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
    showError("设置已保存");
    clearTimeout(showError._t);
    showError._t = setTimeout(() => { if (state.snap && !state.snap.error) $("#alerts").textContent = "运行良好，暂无告警"; }, 3000);
  });

  $("#theme-btn").addEventListener("click", async () => {
    const next = nextTheme();
    state.settings = Object.assign({}, state.settings, { theme: next });
    applyTheme();
    await api("/api/settings", { method: "PUT", body: { theme: next } });
  });

  // 复刻 local-ops：命令面板 / 日志中心 / 总控台自身控制
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
    openUnitDialog({ id: u.id, name: u.name, kind: u.kind, command: u.command, workdir: u.workdir });
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
      if (e.key === "ArrowDown") { e.preventDefault(); state.paletteIndex = Math.min(state.paletteIndex + 1, state.paletteItems.length - 1); renderPalette($("#palette-input").value); }
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
}

function refreshResTabs() {
  $("#res-tab-cpu").classList.toggle("active", state.resTab === "cpu");
  $("#res-tab-mem").classList.toggle("active", state.resTab === "mem");
}

/* ---------- 新端口发现（服务监控页顶部提醒条） ---------- */

function renderNewPorts(snap) {
  const fresh = (snap.ports || []).filter((p) => p.isNew && p.origin !== "other");
  const el = $("#new-ports");
  if (!fresh.length) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  el.innerHTML =
    '<div class="new-ports-head">🔔 发现新的监听端口</div>' +
    fresh.map((p) =>
      '<div class="new-port-row"><span class="mono">' + esc(p.addr) + "</span>" +
      '<span class="muted">' + esc(p.proc || "未知进程") + (p.pid ? " · PID " + p.pid : "") + "</span>" +
      '<span class="tag project">' + (p.origin === "managed" ? "已管理" : "项目") + "</span>" +
      '<span class="spacer"></span>' +
      (p.origin !== "managed" && p.pid
        ? '<button class="mini np-adopt" data-pid="' + esc(p.pid) + '">加入启动台</button>' : "") +
      '<button class="mini np-ignore" data-port="' + esc(p.addr.split(":")[1]) + '">忽略并隐藏</button>' +
      "</div>").join("");
}

async function ignorePort(port) {
  if (!state.settings) return;
  const cur = state.settings.ignored_ports || [];
  if (cur.includes(String(port))) return;
  const res = await api("/api/settings", {
    method: "PUT",
    body: { ignored_ports: cur.concat([String(port)]) },
  });
  if (res.ok) {
    state.settings = await res.json();
    renderIgnoredPorts();
    pushFeed("端口 <b>:" + esc(port) + "</b> 已忽略并隐藏");
  } else showError(await res.text());
}

/* ---------- 任务完成通知 ---------- */

function notifyUnitExits(units) {
  if (!state.settings || state.settings.notifications === false) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  for (const u of units) {
    if (u.status !== "exited") continue;
    const key = u.id + ":" + (u.last_exit && u.last_exit.at);
    if (state.notifiedExit.has(key)) continue;
    state.notifiedExit.add(key);
    if (state.notifiedExit.size > 200) state.notifiedExit.clear();
    const ok = u.exit_code === 0;
    const kindZh = u.kind === "task" ? "任务" : "服务";
    const title = u.kind === "task" ? (ok ? kindZh + "完成" : kindZh + "失败") : kindZh + "退出";
    try {
      new Notification("总控台 · " + u.name, {
        body: title + (u.exit_code != null ? "（退出码 " + u.exit_code + "）" : ""),
        silent: false,
      });
    } catch (e) { /* 通知失败静默 */ }
  }
}

function ensureNotifyPermission() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "default") Notification.requestPermission();
}

/* ---------- 命令面板 Ctrl+K ---------- */

function buildPaletteItems() {
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
  const themes = ["system", "light", "dark"];
  const cur = (state.settings && state.settings.theme) || "system";
  items.push({ label: "切换主题 → " + ({ system: "跟随系统", light: "浅色", dark: "深色" }[nextTheme()]),
    hint: "当前 " + ({ system: "跟随系统", light: "浅色", dark: "深色" }[cur]), keywords: "theme",
    action: () => $("#theme-btn").click() });
  return items;
}

function openPalette() {
  $("#palette").showModal();
  $("#palette-input").value = "";
  state.paletteItems = buildPaletteItems();
  state.paletteIndex = 0;
  renderPalette("");
  $("#palette-input").focus();
}

function renderPalette(query) {
  const q = query.trim().toLowerCase();
  let items = state.paletteItems;
  if (q) {
    items = items.filter((it) =>
      it.label.toLowerCase().includes(q) || (it.keywords || "").toLowerCase().includes(q));
  }
  items = items.slice(0, 12);
  state.paletteItems = items;
  state.paletteIndex = Math.min(state.paletteIndex, Math.max(0, items.length - 1));
  $("#palette-list").innerHTML = items.map((it, i) =>
    '<div class="pal-item' + (i === state.paletteIndex ? " active" : "") + '" data-i="' + i + '">' +
    '<span class="pal-label">' + esc(it.label) + '</span><span class="pal-hint">' + esc(it.hint || "") + "</span></div>"
  ).join("") || '<div class="pal-item muted">无匹配结果</div>';
}

function execPalette() {
  const it = state.paletteItems[state.paletteIndex];
  if (!it) return;
  $("#palette").close();
  it.action();
}

/* ---------- 日志中心 Ctrl+J ---------- */

function openLogCenter(unitId) {
  $("#logcenter").classList.remove("hidden");
  renderLcList();
  const units = (state.snap && state.snap.units) || [];
  const target = unitId || state.lcUnitId || (units[0] && units[0].id);
  if (target) selectLcUnit(target);
  clearInterval(state.lcTimer);
  state.lcTimer = setInterval(refreshLcLog, 3000);
}

function renderLcList() {
  const units = (state.snap && state.snap.units) || [];
  $("#lc-list").innerHTML = units.map((u) =>
    '<div class="lc-item' + (u.id === state.lcUnitId ? " active" : "") + '" data-id="' + esc(u.id) + '">' +
    '<span class="dot ' + (u.status === "running" ? "on" : "off") + '"></span>' +
    '<span class="lc-name">' + esc(u.name) + '</span>' +
    '<span class="lc-kind">' + (u.kind === "task" ? "任务" : "服务") + "</span></div>"
  ).join("") || '<div class="muted lc-item">还没有命令单元</div>';
}

async function selectLcUnit(unitId) {
  state.lcUnitId = unitId;
  renderLcList();
  $("#lc-body").textContent = "加载中…";
  await refreshLcLog();
}

async function refreshLcLog() {
  if (!state.lcUnitId || $("#logcenter").classList.contains("hidden")) return;
  try {
    const res = await fetch("/api/logs/" + encodeURIComponent(state.lcUnitId));
    const body = $("#lc-body");
    const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
    body.textContent = res.ok ? await res.text() : "(" + res.status + ")";
    if (atBottom) body.scrollTop = body.scrollHeight;
  } catch (e) { /* 连接抖动忽略 */ }
}

function closeLogCenter() {
  clearInterval(state.lcTimer);
  state.lcTimer = null;
  $("#logcenter").classList.add("hidden");
}

/* ---------- 诊断弹窗 ---------- */

async function openDiagnose(unitId) {
  const u = ((state.snap && state.snap.units) || []).find((x) => x.id === unitId);
  if (!u) return;
  $("#diag-title").textContent = "诊断 · " + u.name;
  $("#diag-body").innerHTML = '<div class="muted">分析中…</div>';
  $("#diag-edit").classList.remove("hidden");
  $("#diag-edit").dataset.id = unitId;
  $("#diag-dialog").showModal();
  const res = await api("/api/units/" + unitId + "/diagnose");
  if (!res.ok) { $("#diag-body").innerHTML = '<div class="diag-issue">诊断失败：' + esc(await res.text()) + "</div>"; return; }
  const result = await res.json();
  const issues = result.issues || [];
  $("#diag-body").innerHTML = issues.length
    ? issues.map((i) =>
        '<div class="diag-issue"><div class="diag-title">▸ ' + esc(i.title) + "</div>" +
        '<div class="diag-detail">' + esc(i.detail) + "</div>" +
        '<div class="diag-fix">修复建议：' + esc(i.fix) + "</div></div>").join("")
    : '<div class="diag-issue ok">✓ 未发现问题。配置可正常运行。</div>';
}

/* ---------- 拖拽 / 键盘排序 ---------- */

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
  root.addEventListener("dragend", (e) => {
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
  const all = ids.concat(orderedIdsForKind(otherKind));
  const res = await api("/api/units/reorder", { method: "POST", body: { ids: all } });
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

/* ---------- 总控台自身：重启 / 停止 ---------- */

async function consoleAction(action, confirmText) {
  if (!confirm(confirmText)) return;
  try {
    const res = await api("/api/console/" + action, { method: "POST" });
    if (!res.ok) return showError(await res.text());
    pushFeed(action === "restart" ? "总控台服务重启中…" : "总控台服务停止中…");
    if (action === "stop") {
      setConn(false);
    }
  } catch (e) {
    showError("请求失败：" + e);
  }
}



/* ---------- 启动 ---------- */

async function boot() {
  document.documentElement.dataset.theme = "system";
  try {
    const ses = await (await fetch("/api/session")).json();
    state.token = ses.token;
    $("#rail-port").textContent = "127.0.0.1:" + ses.port;
  } catch (e) {
    showError("无法连接面板服务，请确认 server.py 在运行");
    return;
  }
  await loadSettings();
  applyTheme();
  try {
    const invRes = await fetch("/api/inventory");
    if (invRes.ok) { state.inv = await invRes.json(); renderInventory(); updateProjectSelect(); }
  } catch (e) { /* 盘点失败不阻塞主界面 */ }
  renderSettings();
  bind();
  await refreshSnapshot();

  const es = new EventSource("/api/stream");
  es.addEventListener("snapshot", (e) => {
    $("#conn-banner").classList.add("hidden");
    renderSnapshot(JSON.parse(e.data));
  });
  es.addEventListener("inventory", (e) => {
    state.inv = JSON.parse(e.data);
    renderInventory();
    updateProjectSelect();
    $("#refresh-inv-btn").disabled = false;
  });
  es.onopen = () => { setConn(true); $("#conn-banner").classList.add("hidden"); };
  es.onerror = () => { setConn(false); $("#conn-banner").classList.remove("hidden"); };
}

boot();
