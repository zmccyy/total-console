// 后端 API 访问：所有请求统一走这里（写请求自动带令牌与 JSON 头）
import { state } from "./state.js";

export async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers["X-Ops-Token"] = state.token;
  const init = Object.assign({}, opts, { headers });
  if (opts.body && typeof opts.body !== "string") {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  return fetch(path, init);
}

export async function getJSON(path) {
  const res = await fetch(path);
  return res.ok ? res.json() : null;
}

export async function getText(path) {
  const res = await fetch(path);
  return res.ok ? res.text() : null;
}
