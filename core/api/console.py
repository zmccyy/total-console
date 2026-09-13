"""总控台自身控制（重启 / 停止只影响面板服务，不影响已运行的命令单元）。"""
import os
from urllib.parse import urlsplit

from . import Route
from .common import ApiContext


def console_restart(ctx: ApiContext):
    ctx.app.spawn_replacement(ctx.port)
    ctx.app.schedule_shutdown(0.6)
    ctx.send_json(200, {"status": "restarting"})


def console_stop(ctx: ApiContext):
    ctx.app.schedule_shutdown(0.4)
    ctx.send_json(200, {"status": "stopping"})


def open_url(ctx: ApiContext):
    """在本机默认浏览器打开 URL（供端口一键打开）。

    仅允许 http(s) 协议——面板仅回环可信，但不得成为 file:// 等本地
    处理器协议的跳板。
    """
    body = ctx.read_json()
    url = str((body or {}).get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ctx.send_text(400, "仅允许打开 http(s) 地址")
    try:
        os.startfile(url)  # Windows：交给默认浏览器
    except OSError as e:
        return ctx.send_text(500, "打开失败: %s" % e)
    ctx.send_json(200, {"ok": True})


ROUTES = [
    Route("POST", "/api/console/restart", console_restart, "write"),
    Route("POST", "/api/console/stop", console_stop, "write"),
    Route("POST", "/api/open-url", open_url, "write"),
]
