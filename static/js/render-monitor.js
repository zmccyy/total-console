// 服务监控：端口表 / 进程表 / 新端口提醒 / 侧栏榜单
import { state } from "./state.js";
import { $, esc, fmtMem, fmtUptime, STATUS_ZH, baseName, httpUrlFor, isTcp, managedUnitForPid, applyHtml } from "./util.js";

export const ORIGIN_ICONS = { bot: "🤖", code: "⌨", terminal: "▸", rocket: "🚀", server: "▦", package: "▣" };

export function renderProjectPorts(snap, projPorts) {
  const visible = projPorts.filter((p) => !p.isNew);
  $("#proj-port-count").textContent = visible.length;
  const procCpu = {};
  (snap.procs || []).forEach((p) => { procCpu[p.pid] = p; });
  const tbody = $("#project-ports-table tbody");
  const html = visible.map((p) => {
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
    // TCP 端口附加快捷操作：在浏览器打开 / 复制 URL
    const url = isTcp(p) ? httpUrlFor(p.addr) : null;
    const addrCell = '<td class="mono">' + esc(p.addr) +
      (url
        ? ' <button class="mini act-open-url" data-url="' + esc(url) + '" title="在浏览器打开">↗</button>' +
          ' <button class="mini act-copy-url" data-url="' + esc(url) + '" title="复制 URL">⧉</button>'
        : "") +
      "</td>";
    return (
      "<tr>" +
      "<td>" + esc(p.proc || "（不可见）") + "</td>" +
      addrCell +
      "<td>" + origin + "</td>" +
      "<td>" + originTag + "</td>" +
      '<td class="num">' + (proc ? proc.cpu : "—") + "</td>" +
      '<td class="num">' + (proc ? fmtUptime(proc.started) : "—") + "</td>" +
      "<td>" + actions + "</td>" +
      "</tr>"
    );
  }).join("") || '<tr><td colspan="7" class="muted">暂未发现项目相关端口。启动台里运行的服务或与项目目录关联的进程端口会出现在这里。</td></tr>';
  applyHtml(tbody, html);
}

export function renderOtherPorts(snap) {
  const others = (snap.ports || []).filter((p) => p.origin === "other");
  $("#other-port-count").textContent = others.length;
  applyHtml($("#other-ports-table tbody"), others.map((p) =>
    "<tr>" +
    '<td class="mono">' + esc(p.addr) + "</td>" +
    '<td class="mono">' + esc(p.proto) + "</td>" +
    "<td>" + esc(p.proc || "（不可见）") + "</td>" +
    '<td class="num">' + (p.pid || "—") + "</td>" +
    "</tr>"
  ).join("") || '<tr><td colspan="4" class="muted">没有其他端口</td></tr>');
  $("#other-ports-body").classList.toggle("hidden", !state.otherPortsOpen);
  $("#other-ports-toggle").textContent = state.otherPortsOpen ? "收起" : "展开";
}

export function renderProcs(snap) {
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
  const html = list.map((p) => {
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
  applyHtml($("#procs-table tbody"), html);
}

export function topPorts(limit) {
  const snap = state.snap || {};
  const procCpu = {};
  (snap.procs || []).forEach((p) => { procCpu[p.pid] = p; });
  return (snap.ports || [])
    .filter((p) => p.origin !== "other" && p.pid != null && procCpu[p.pid])
    .map((p) => ({ p, proc: procCpu[p.pid] }))
    .sort((a, b) => b.proc.cpu - a.proc.cpu)
    .slice(0, limit);
}

export function renderTopPorts() {
  const items = topPorts(5);
  const html = items.map(({ p, proc }, i) => {
    const unit = managedUnitForPid(p.pid);
    return '<div class="side-row"><span class="rank">' + (i + 1) + "</span>" +
      '<span class="name">' + esc(p.addr) + (unit ? " · " + esc(unit.name) : "") + "</span>" +
      '<span class="val">' + proc.cpu + "%</span></div>";
  }).join("") || '<div class="side-body">暂无项目端口</div>';
  applyHtml($("#side-ports"), html);
  applyHtml($("#side-ports-m"), html);
}

export function renderResources() {
  const snap = state.snap || {};
  let list = (snap.procs || []).slice();
  if (state.resTab === "cpu") list.sort((a, b) => b.cpu - a.cpu);
  else list.sort((a, b) => b.rss - a.rss);
  applyHtml($("#side-resources"), list.slice(0, 5).map((p, i) =>
    '<div class="side-row"><span class="rank">' + (i + 1) + "</span>" +
    '<span class="name">' + esc(p.name) + "</span>" +
    '<span class="val">' + (state.resTab === "cpu" ? p.cpu + "%" : fmtMem(p.rss)) + "</span></div>"
  ).join(""));
}

export function refreshResTabs() {
  $("#res-tab-cpu").classList.toggle("active", state.resTab === "cpu");
  $("#res-tab-mem").classList.toggle("active", state.resTab === "mem");
}

// ---------- 新端口发现（服务监控页顶部提醒条） ----------

export function renderNewPorts(snap) {
  const fresh = (snap.ports || []).filter((p) => p.isNew && p.origin !== "other");
  const el = $("#new-ports");
  if (!fresh.length) { el.classList.add("hidden"); el.innerHTML = ""; el.__html = ""; return; }
  el.classList.remove("hidden");
  applyHtml(el,
    '<div class="new-ports-head">🔔 发现新的监听端口</div>' +
    fresh.map((p) =>
      '<div class="new-port-row"><span class="mono">' + esc(p.addr) + "</span>" +
      '<span class="muted">' + esc(p.proc || "未知进程") + (p.pid ? " · PID " + p.pid : "") + "</span>" +
      '<span class="tag project">' + (p.origin === "managed" ? "已管理" : "项目") + "</span>" +
      '<span class="spacer"></span>' +
      (p.origin !== "managed" && p.pid
        ? '<button class="mini np-adopt" data-pid="' + esc(p.pid) + '">加入启动台</button>' : "") +
      '<button class="mini np-ignore" data-port="' + esc(p.addr.split(":")[1]) + '">忽略并隐藏</button>' +
      "</div>").join(""));
}
