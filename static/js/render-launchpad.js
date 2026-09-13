// 启动台：单元卡片（按 key 增量调和）与过滤计数
import { state } from "./state.js";
import { $, esc, hashColor, fmtUptime, unitStatusZh, portForUnit, renderKeyed } from "./util.js";

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

const ADD_CARD = (kind) =>
  '<div class="unit-card empty-card add-card" data-add-kind="' + kind + '">' +
  '<span class="plus">＋</span><span>添加' + (kind === "task" ? "任务" : "服务") + "</span></div>";

function picks(list, f) {
  if (f === "all") return list;
  return list.filter((u) => u.status === f);
}

export function renderUnitCards() {
  const units = (state.snap && state.snap.units) || [];
  const svc = units.filter((u) => u.kind === "service");
  const task = units.filter((u) => u.kind === "task");
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
  const svcItems = svcList.map((u) => ({ key: u.id, html: unitCard(u) }))
    .concat([{ key: "__add__service", html: ADD_CARD("service") }]);
  const taskItems = taskList.map((u) => ({ key: u.id, html: unitCard(u) }));
  if (taskList.length < 3) taskItems.push({ key: "__add__task", html: ADD_CARD("task") });
  renderKeyed($("#service-cards"), svcItems);
  renderKeyed($("#task-cards"), taskItems);
}
