// 启动台「实时动态」信息流
import { state } from "./state.js";
import { $ } from "./util.js";

export function pushFeed(html) {
  if (state.feedCleared) return;
  const t = new Date().toTimeString().slice(0, 5);
  state.feed.push({ t, html });
  if (state.feed.length > 30) state.feed.shift();
  renderFeed();
}

export function renderFeed() {
  const el = $("#feed");
  if (!el) return;
  applyFeed(el);
}

function applyFeed(el) {
  const html = state.feed.map((f) =>
    '<div class="feed-item"><span class="t">' + f.t + '</span><span class="ev">' + f.html + "</span></div>"
  ).join("") || '<div class="feed-item"><span class="ev muted">暂无动态</span></div>';
  if (el.__html !== html) {
    el.__html = html;
    el.innerHTML = html;
  }
}

export function clearFeed() {
  state.feed = [];
  state.feedCleared = true;
  renderFeed();
}
