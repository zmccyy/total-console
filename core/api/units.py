"""命令单元：CRUD / 排序 / 生命周期 / 认领 / 诊断 / 日志 / 外部工具。"""
from .. import diagnose, integrations, launchpad
from . import Route
from .common import ApiContext


def units_list(ctx: ApiContext):
    ctx.send_json(200, ctx.app.launchpad.units())


def unit_create(ctx: ApiContext):
    body = ctx.read_json()
    if body is None:
        return ctx.send_text(400, "JSON 格式错误")
    name = str(body.get("name") or "").strip()
    command = str(body.get("command") or "").strip()
    if not name or not command:
        return ctx.send_text(400, "name 与 command 为必填")
    kind = body.get("kind") if body.get("kind") in ("service", "task") else "service"
    workdir = str(body.get("workdir") or "").strip()
    unit = ctx.app.launchpad.create_unit(name, kind, command, workdir)
    ctx.send_json(201, unit)


def units_reorder(ctx: ApiContext):
    body = ctx.read_json()
    if body is None:
        return ctx.send_text(400, "JSON 格式错误")
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return ctx.send_text(400, "ids 必须为字符串数组")
    ctx.app.launchpad.reorder(ids)
    ctx.send_json(200, {"ok": True})


def unit_update(ctx: ApiContext):
    body = ctx.read_json()
    if body is None:
        return ctx.send_text(400, "JSON 格式错误")
    try:
        unit = ctx.app.launchpad.update_unit(ctx.params["unit_id"], body)
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    except launchpad.UnitBusyError as e:
        return ctx.send_text(409, str(e))
    except ValueError as e:
        return ctx.send_text(400, str(e))
    ctx.send_json(200, unit)


def unit_delete(ctx: ApiContext):
    try:
        ctx.app.launchpad.delete_unit(ctx.params["unit_id"])
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    except launchpad.UnitBusyError as e:
        return ctx.send_text(409, str(e))
    ctx.send_text(204, "")


def unit_diagnose(ctx: ApiContext):
    try:
        return ctx.send_json(200, ctx.app.get_diagnosis(ctx.params["unit_id"]))
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")


def unit_health(ctx: ApiContext):
    try:
        unit = ctx.app.launchpad.get_unit(ctx.params["unit_id"])
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    ctx.send_json(200, diagnose.health(unit))


def unit_claim(ctx: ApiContext):
    body = ctx.read_json()
    pid = body.get("pid") if isinstance(body, dict) else None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return ctx.send_text(400, "pid 必须为整数")
    try:
        unit = ctx.app.launchpad.claim(
            pid, name=body.get("name"), kind=body.get("kind") or "service",
            command=body.get("command") or "", workdir=body.get("workdir"),
            unit_id=body.get("unit_id"),
        )
    except launchpad.ClaimError as e:
        return ctx.send_text(409, str(e))
    ctx.send_json(201, unit)


def unit_lifecycle(ctx: ApiContext):
    action = ctx.params["action"]
    if action in ("start", "stop", "restart"):
        return _lifecycle(ctx, action)
    if action == "open-terminal":
        return _open_with(ctx, integrations.open_terminal, "已在终端打开工作目录")
    if action == "open-editor":
        return _open_with(ctx, integrations.open_editor, "已在编辑器打开工作目录")
    return ctx.send_text(404, "Not Found")


def _lifecycle(ctx: ApiContext, action):
    unit_id = ctx.params["unit_id"]
    try:
        if action == "start":
            unit = ctx.app.launchpad.start(unit_id)
        elif action == "stop":
            unit = ctx.app.launchpad.stop(unit_id)
        else:
            unit = ctx.app.launchpad.restart(unit_id)
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    except launchpad.AlreadyRunningError as e:
        return ctx.send_text(409, str(e))
    except launchpad.StopRejectedError as e:
        return ctx.send_text(409, str(e))
    except launchpad.Error as e:
        return ctx.send_text(500, str(e))
    ctx.send_json(200, unit)


def _open_with(ctx: ApiContext, opener, ok_feed):
    unit_id = ctx.params["unit_id"]
    try:
        unit = ctx.app.launchpad.get_unit(unit_id)
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    try:
        result = opener(unit.get("workdir") or "")
    except integrations.OpenError as e:
        return ctx.send_text(400, str(e))
    result["feed"] = ok_feed
    ctx.send_json(200, result)


def unit_logs(ctx: ApiContext):
    try:
        text = ctx.app.launchpad.log_tail(ctx.params["unit_id"])
    except launchpad.UnitNotFoundError:
        return ctx.send_text(404, "单元不存在")
    ctx.send_raw(200, "text/plain; charset=utf-8", text.encode("utf-8"))


ROUTES = [
    Route("GET", "/api/units", units_list, "public"),
    Route("POST", "/api/units", unit_create, "write"),
    Route("POST", "/api/units/reorder", units_reorder, "write"),
    Route("GET", "/api/logs/<unit_id>", unit_logs, "public"),
    Route("PUT", "/api/units/<unit_id>", unit_update, "write"),
    Route("DELETE", "/api/units/<unit_id>", unit_delete, "write"),
    Route("GET", "/api/units/<unit_id>/diagnose", unit_diagnose, "public"),
    Route("GET", "/api/units/<unit_id>/health", unit_health, "public"),
    Route("POST", "/api/units/<unit_id>/claim", unit_claim, "write"),
    # <action> 路由须在 claim 之后声明（claim 也是五段路径）；
    # 非法 action 在处理器内回落 404
    Route("POST", "/api/units/<unit_id>/<action>", unit_lifecycle, "write"),
]
