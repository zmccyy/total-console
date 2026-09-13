"""设置读写。"""
from . import Route
from .common import ApiContext


def settings_get(ctx: ApiContext):
    ctx.send_json(200, ctx.app.settings)


def settings_put(ctx: ApiContext):
    body = ctx.read_json()
    if body is None:
        return ctx.send_text(400, "JSON 格式错误")
    try:
        settings = ctx.app.update_settings(body)
    except ValueError as e:
        return ctx.send_text(400, str(e))
    ctx.send_json(200, settings)


ROUTES = [
    Route("GET", "/api/settings", settings_get, "public"),
    Route("PUT", "/api/settings", settings_put, "write"),
]
