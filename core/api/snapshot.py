"""快照 / 会话 / 进程上下文 / SSE 流。"""
import os
import threading

from .. import scanner
from ..meta import APP_MARKER, VERSION
from . import Route
from .common import ApiContext


def ping(ctx: ApiContext):
    ctx.send_json(200, {"app": APP_MARKER, "name": "总控台",
                        "version": VERSION, "pid": os.getpid()})


def session(ctx: ApiContext):
    ctx.send_json(200, {"token": ctx.app.token, "app": APP_MARKER,
                        "version": VERSION, "port": ctx.port})


def snapshot_now(ctx: ApiContext):
    # 原生批量扫描后整帧成本极低，直接取全新帧，避免秒级状态陈旧
    ctx.app.sync_snapshot()
    ctx.send_json(200, ctx.app.latest_snapshot
                  or {"ts": "", "procs": [], "ports": [], "units": []})


def cmdline(ctx: ApiContext):
    pid = ctx.query.get("pid", ["0"])[0]
    ctx.send_json(200, {"cmdline": scanner.get_cmdline(int(pid or 0))})


def proc_context(ctx: ApiContext):
    pid = ctx.query.get("pid", ["0"])[0]
    ctx.send_json(200, scanner.get_proc_context(int(pid or 0)))


def stream(ctx: ApiContext):
    """SSE：snapshot/inventory 推送，20s 无事件发 ping 行。"""
    h = ctx.h
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Connection", "keep-alive")
    h.end_headers()
    q = ctx.app.subscribe()
    try:
        h.wfile.write(b"retry: 2000\n\n")
        h.wfile.flush()
        while True:
            try:
                event, text = q.get(timeout=20)
                h.wfile.write(("event: %s\ndata: %s\n\n" % (event, text)).encode("utf-8"))
            except threading.QueueEmpty:
                h.wfile.write(b": ping\n\n")
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        ctx.app.unsubscribe(q)


ROUTES = [
    Route("GET", "/api/ping", ping, "public"),
    Route("GET", "/api/session", session, "public"),
    Route("GET", "/api/snapshot", snapshot_now, "public"),
    Route("GET", "/api/cmdline", cmdline, "public"),
    Route("GET", "/api/proc-context", proc_context, "public"),
    Route("GET", "/api/stream", stream, "public"),
]
