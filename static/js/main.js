// 入口：启动流程与 SSE 实时通道
import { getJSON } from "./api.js";
import { state } from "./state.js";
import { $ } from "./util.js";
import { applyTheme, applyLocalTheme } from "./theme.js";
import { showError, setConn, refreshSnapshot } from "./actions.js";
import { renderSnapshot } from "./render.js";
import { renderInventory, updateProjectSelect, loadSettings, renderSettings } from "./render-inventory.js";
import { bind } from "./handlers.js";

async function boot() {
  applyLocalTheme(); // 内联脚本已先行应用，这里兜底
  try {
    const ses = await getJSON("/api/session");
    state.token = ses.token;
    $("#rail-port").textContent = "127.0.0.1:" + ses.port;
  } catch (e) {
    showError("无法连接面板服务，请确认 server.py 在运行");
    return;
  }
  await loadSettings();
  applyTheme();
  try {
    const inv = await getJSON("/api/inventory");
    if (inv) { state.inv = inv; renderInventory(); updateProjectSelect(); }
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
