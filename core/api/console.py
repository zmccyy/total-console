"""总控台自身控制（重启 / 停止只影响面板服务，不影响已运行的命令单元）。"""
from . import Route
from .common import ApiContext


def console_restart(ctx: ApiContext):
    ctx.app.spawn_replacement(ctx.port)
    ctx.app.schedule_shutdown(0.6)
    ctx.send_json(200, {"status": "restarting"})


def console_stop(ctx: ApiContext):
    ctx.app.schedule_shutdown(0.4)
    ctx.send_json(200, {"status": "stopping"})


ROUTES = [
    Route("POST", "/api/console/restart", console_restart, "write"),
    Route("POST", "/api/console/stop", console_stop, "write"),
]
