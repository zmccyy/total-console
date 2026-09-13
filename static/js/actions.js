// 用户动作与快照追踪：启停、告警、通知、端口忽略、新端口/单元状态追踪
// （与 render.js 存在函数级循环引用：调用均发生在运行期，ESM 允许）
import { api } from "./api.js";
import { state } from "./state.js";
import { $, esc, portForUnit } from "./util.js";
import { pushFeed } from "./feed.js";
import { renderIgnoredPorts, renderSettings } from "./render-inventory.js";
import { renderSnapshot } from "./render.js";

export function showError(text, ttl) {
  const el = $("#alerts");
  if (el) el.textContent = text;
  clearTimeout(showError._t);
  showError._t = setTimeout(() => {
    if (state.snap && !state.snap.error) el.textContent = "运行良好，暂无告警";
  }, ttl || 8000);
}

export function setConn(on) {
  const dot = $("#conn-dot");
  if (!dot) return;
  dot.className = "dot " + (on ? "on" : "off");
  dot.title = on ? "已连接" : "连接断开";
}

export async function refreshSnapshot() {
  const snap = await api("/api/snapshot").then((r) => (r.ok ? r.json() : null));
  if (snap) renderSnapshot(snap);
}

export async function unitAction(id, action, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  if (action === "start") ensureNotifyPermission();
  const res = await api("/api/units/" + id + "/" + action, { method: "POST" });
  if (!res.ok) showError(await res.text());
  await refreshSnapshot();
}

export async function ignorePort(port) {
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

export async function hideProcName(name) {
  if (!state.settings || !name) return;
  if (!confirm("隐藏所有名为「" + name + "」的进程？（盘点页设置中可恢复）")) return;
  const cur = state.settings.hidden_procs || [];
  if (cur.includes(name)) return;
  const res = await api("/api/settings", {
    method: "PUT",
    body: { hidden_procs: cur.concat([name]) },
  });
  if (res.ok) {
    state.settings = await res.json();
    renderSettings(); // 设置面板同步显示已隐藏芯片，供恢复
    pushFeed("进程 <b>" + esc(name) + "</b> 已隐藏（设置中可恢复）");
  } else showError(await res.text());
}

// ---------- 快照变化追踪（动态流 + 浏览器通知） ----------

export function trackNewPorts(projPorts) {
  if (state.firstSnap) {
    projPorts.forEach((p) => state.knownPorts.add(p.addr));
    state.firstSnap = false;
    return;
  }
  for (const p of projPorts) {
    if (!state.knownPorts.has(p.addr)) {
      state.knownPorts.add(p.addr);
      const owner = p.origin === "managed" ? "已管理服务" : "项目";
      pushFeed("<b>" + esc(p.addr) + "</b> 已就绪（" + owner + "）");
    }
  }
}

export function trackUnitChanges(units) {
  for (const u of units) {
    const prev = state.prevUnits[u.id];
    if (!prev) continue;
    if (prev !== u.status) {
      if (u.status === "running") {
        const port = portForUnit(u.id);
        pushFeed("<b>" + esc(u.name) + "</b> 已启动" + (port ? " :" + esc(port.addr.split(":")[1]) : ""));
      } else if (u.status === "exited") {
        pushFeed("<b>" + esc(u.name) + "</b> 已结束（码 " + (u.exit_code != null ? u.exit_code : "未知") + "）");
      } else {
        pushFeed("<b>" + esc(u.name) + "</b> 已停止");
      }
    }
  }
  state.prevUnits = {};
  units.forEach((u) => { state.prevUnits[u.id] = u.status; });
}

export function notifyUnitExits(units) {
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

export function ensureNotifyPermission() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission === "default") Notification.requestPermission();
}
