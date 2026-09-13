"""盘点：Agent 工具与项目。"""
from . import Route
from .common import ApiContext


def inventory_get(ctx: ApiContext):
    ctx.send_json(200, ctx.app.get_inventory())


def inventory_refresh(ctx: ApiContext):
    ctx.app.refresh_inventory_async()
    ctx.send_json(202, {"status": "scanning"})


ROUTES = [
    Route("GET", "/api/inventory", inventory_get, "public"),
    Route("POST", "/api/inventory/refresh", inventory_refresh, "write"),
]
