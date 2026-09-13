// 盘点视图（Agent 工具 / 项目表）与设置面板
import { api, getJSON } from "./api.js";
import { state } from "./state.js";
import { $, esc, applyHtml } from "./util.js";

// Git 分支徽章（脏 = 橙色带 *，干净 = 绿色）
export function gitBadge(p) {
  if (!p.git_branch) return "";
  return '<span class="tag ' + (p.git_dirty ? "git-dirty" : "git-clean") + '" title="' +
    (p.git_dirty ? "有未提交改动" : "工作区干净") + '">⎇ ' + esc(p.git_branch) +
    (p.git_dirty ? " *" : "") + "</span>";
}

export function renderInventory() {
  const inv = state.inv;
  if (!inv) return;
  $("#inv-scanned").textContent = "上次盘点 " + (inv.scanned_at || "—") + " · 扫描根 " + (inv.scan_roots || []).length + " 个";
  applyHtml($("#agents-table tbody"), (inv.agents || []).map((a) =>
    "<tr>" + "<td>" + esc(a.name) + "</td>" +
    '<td class="mono">' + esc(a.version || "未知") + "</td>" +
    '<td class="mono cmd" title="' + esc(a.path || "") + '">' + esc(a.path || "未安装") + "</td>" +
    '<td class="mono">' + (a.config_dir_exists ? "存在" : "—") + "</td>" +
    "<td>" + (a.logged_in ? '<span class="tag managed">已登录</span>'
      : (a.found ? '<span class="tag">未登录</span>' : '<span class="tag">未安装</span>')) + "</td></tr>"
  ).join(""));
  applyHtml($("#projects-table tbody"), (inv.projects || []).map((p, idx) =>
    "<tr>" +
    '<td class="mono cmd" title="' + esc(p.path) + '">' + esc(p.path) + "</td>" +
    "<td>" + esc(p.type) + (p.has_git ? " · git" : "") + " " + gitBadge(p) + "</td>" +
    "<td>" + p.candidates.map((c) =>
      '<button class="chip add-candidate" data-idx="' + idx + '" data-cmd="' + esc(c.command) + '">' + esc(c.label) + "</button>"
    ).join("") + "</td>" +
    '<td><button class="ghost add-from-project" data-idx="' + idx + '">加入启动台</button></td>' +
    "</tr>"
  ).join("") || '<tr><td colspan="4" class="muted">扫描根内尚未发现项目。可在右侧设置中添加 E:\\项目 这类目录。</td></tr>');
}

export function updateProjectSelect() {
  const sel = $("#unit-project-select");
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">— 手动填写 —</option>' +
    ((state.inv && state.inv.projects) || []).map((p) =>
      '<option value="' + esc(p.path) + '">' + esc(p.path) + "</option>").join("");
  sel.value = cur || "";
}

// ---------- 设置面板 ----------

export function renderSettings() {
  const s = state.settings;
  if (!s) return;
  $("#poll-input").value = s.poll_interval;
  applyHtml($("#scan-roots"), s.scan_roots.map((r, i) =>
    '<span class="chip root-chip">' + esc(r) + '<button class="del-root" data-i="' + i + '">×</button></span>'
  ).join(""));
  $("#notify-toggle").checked = s.notifications !== false;
  applyHtml($("#watch-keywords"), (s.watch_keywords || []).map((k, i) =>
    '<span class="chip root-chip">' + esc(k) + '<button class="del-watch" data-i="' + i + '">×</button></span>'
  ).join("") || '<span class="muted">无</span>');
  applyHtml($("#hidden-procs"), (s.hidden_procs || []).map((n, i) =>
    '<span class="chip root-chip">' + esc(n) + '<button class="del-hidden" data-i="' + i + '">×</button></span>'
  ).join("") || '<span class="muted">无</span>');
  renderIgnoredPorts();
}

export function renderIgnoredPorts() {
  const el = $("#ignored-ports");
  const list = (state.settings && state.settings.ignored_ports) || [];
  applyHtml(el, list.map((p, i) =>
    '<span class="chip root-chip">:' + esc(p) + '<button class="del-ignored" data-i="' + i + '">×</button></span>'
  ).join("") || '<span class="muted">无</span>');
}

export async function loadSettings() {
  const s = await getJSON("/api/settings");
  if (s) state.settings = s;
}
