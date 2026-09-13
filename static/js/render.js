// 快照渲染中枢：两页统计 + 各视图定向渲染 + 变化追踪
import { state } from "./state.js";
import { $ } from "./util.js";
import { showError, trackNewPorts, trackUnitChanges, notifyUnitExits } from "./actions.js";
import { renderUnitCards } from "./render-launchpad.js";
import { renderProjectPorts, renderOtherPorts, renderProcs, renderNewPorts, renderTopPorts, renderResources } from "./render-monitor.js";

export function renderSnapshot(snap) {
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
  $("#poll-badge").textContent = (snap.poll_interval || 2) + "s";
  $("#top-summary").textContent =
    "运行中 " + units.filter((u) => u.status === "running").length +
    " · 服务 " + units.filter((u) => u.kind === "service").length;
  if (snap.error) showError(snap.error);
  else $("#alerts").textContent = "运行良好，暂无告警";

  const projPorts = (snap.ports || []).filter((p) => p.origin !== "other");
  $("#stat-port").textContent = projPorts.length;
  $("#stat-proj-ports").textContent = projPorts.length;
  $("#stat-mysvc").textContent = units.filter((u) => u.status === "running").length;

  trackNewPorts(projPorts);
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
