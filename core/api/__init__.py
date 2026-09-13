"""API 路由注册表与分发。

路由按域模块聚合（snapshot/units/settings/inventory/console），
每条 = Route(method, 模式, 处理函数, auth)。模式中以 `<name>` 标注的
段捕获为路径参数；匹配按声明顺序进行，精确路由写在含参数路由之前。
auth 当前为文档性标注（write=需令牌，public=仅读取），实际写鉴权
在进入路由前统一执行（与 v0.2 行为一致）。

注意：Route 须先于子模块导入定义——子模块会 `from . import Route`。
"""
from collections import namedtuple

Route = namedtuple("Route", "method pattern handler auth")

from .common import ApiContext  # noqa: E402
from . import console, inventory, settings, snapshot, units  # noqa: E402

ROUTES = []
for _mod in (snapshot, units, settings, inventory, console):
    ROUTES.extend(_mod.ROUTES)


def match(method, path):
    """按 (method, path) 找路由，返回 (handler, params)；未命中 (None, None)。

    path 不含查询串。段数与各段（或 `<param>`）须完全一致。
    """
    want = path.split("/")
    for route in ROUTES:
        if route.method != method:
            continue
        pat = route.pattern.split("/")
        if len(pat) != len(want):
            continue
        params = {}
        for p, w in zip(pat, want):
            if p.startswith("<") and p.endswith(">"):
                params[p[1:-1]] = w
            elif p != w:
                break
        else:
            return route.handler, params
    return None, None
