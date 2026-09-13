// 主题（system / light / dark），服务端持久化 + localStorage 防首帧闪烁
import { state } from "./state.js";

export const THEMES = ["system", "light", "dark"];
export const THEME_LABEL = { system: "跟随系统", light: "浅色", dark: "深色" };

export function nextTheme() {
  const cur = (state.settings && state.settings.theme) || "system";
  return THEMES[(THEMES.indexOf(cur) + 1) % THEMES.length];
}

export function applyTheme() {
  const theme = state.settings ? state.settings.theme : "system";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("ops-theme", theme); } catch (e) { /* 隐私模式忽略 */ }
}

export function applyLocalTheme() {
  try {
    const saved = localStorage.getItem("ops-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch (e) { /* 忽略 */ }
}
