// 对话框：添加/编辑命令、认领端口、诊断弹窗
import { api } from "./api.js";
import { state } from "./state.js";
import { $, $$, esc, baseName, findProjectByCwd } from "./util.js";
import { showError, refreshSnapshot } from "./actions.js";
import { pushFeed } from "./feed.js";
import { updateProjectSelect } from "./render-inventory.js";

export function openUnitDialog(opts = {}) {
  state.editingUnitId = opts.id || null;
  $("#unit-dialog-title").textContent = opts.id ? "编辑命令" : "添加命令";
  updateProjectSelect();
  $("#unit-name").value = opts.name || "";
  $("#unit-command").value = opts.command || "";
  $("#unit-workdir").value = opts.workdir || "";
  $("#unit-env").value = Object.entries(opts.env || {})
    .map(([k, v]) => k + "=" + v).join("\n");
  $("#unit-envfile").value = opts.env_file || "";
  const kind = opts.kind || "service";
  $$('input[name="unit-kind"]').forEach((r) => { r.checked = r.value === kind; });
  $("#unit-candidates").innerHTML = "";
  $("#unit-project-select").value = opts.workdir || "";
  $("#unit-dialog").showModal();
}

// 「环境变量」多行文本 → 对象（每行 KEY=VALUE，# 注释与空行忽略）
export function parseEnvText(text) {
  const env = {};
  String(text || "").split("\n").forEach((line) => {
    const s = line.trim();
    if (!s || s.startsWith("#")) return;
    const i = s.indexOf("=");
    if (i > 0) env[s.slice(0, i).trim()] = s.slice(i + 1).trim();
  });
  return env;
}

export function closeUnitDialog() {
  $("#unit-dialog").close();
  state.editingUnitId = null;
  state.claimPid = null;
}

// 「加入启动台」：取进程上下文 → 预填项目信息 → 打开对话框
export async function adoptPort(pid) {
  const ctx = await api("/api/proc-context?pid=" + pid).then(
    (r) => (r.ok ? r.json() : { cmdline: "", cwd: null }));
  const proj = findProjectByCwd(ctx.cwd);
  state.claimPid = pid;
  openUnitDialog({
    name: baseName(ctx.cwd) || "port-" + pid,
    command: proj && proj.candidates.length ? proj.candidates[0].command : "",
    workdir: proj ? proj.path : "",
  });
}

export async function openDiagnose(unitId) {
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
  const fixBtn = $("#diag-fix-install");
  if (result.install_hint) {
    fixBtn.classList.remove("hidden");
    fixBtn.dataset.hint = result.install_hint;
    fixBtn.dataset.workdir = u.workdir || "";
  } else {
    fixBtn.classList.add("hidden");
  }
  $("#diag-body").innerHTML = issues.length
    ? issues.map((i) =>
        '<div class="diag-issue"><div class="diag-title">▸ ' + esc(i.title) + "</div>" +
        '<div class="diag-detail">' + esc(i.detail) + "</div>" +
        '<div class="diag-fix">修复建议：' + esc(i.fix) + "</div></div>").join("")
    : '<div class="diag-issue ok">✓ 未发现问题。配置可正常运行。</div>';
}

// ---------- 事件绑定（模块加载时执行；module script 天然延迟到 DOM 就绪） ----------

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
    env: parseEnvText($("#unit-env").value),
    env_file: $("#unit-envfile").value.trim(),
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
      // 认领当前监听进程为本单元受管进程（原子校验）
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
