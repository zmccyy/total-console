import getpass
import os
import secrets
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import psutil

from . import attribution, store

LOG_LIMIT = 2 * 1024 * 1024
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
_KILL_TIMEOUT = 15


class Error(Exception):
    pass


class AlreadyRunningError(Error):
    pass


class StopRejectedError(Error):
    pass


class ClaimError(Error):
    pass


class UnitBusyError(Error):
    pass


class UnitNotFoundError(Error):
    pass


def current_username():
    """当前用户名（小写），用于「只动自己进程」的校验。"""
    return getpass.getuser().lower()


def _same_user(proc_username):
    """psutil 返回 '机器\\用户'；与当前用户名做不区分大小写的尾段比较。"""
    if not proc_username:
        return False
    tail = proc_username.replace("\\\\", "\\").rsplit("\\", 1)[-1].lower()
    return tail == current_username()


def parse_env_file(path):
    """解析 .env 文件：KEY=VALUE 每行一条，# 注释与空行忽略，
    支持可选 export 前缀与成对引号。坏行跳过不报错。"""
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def effective_env(unit):
    """单元的生效环境变量：进程环境 + .env 文件（先加载）+ 手写 env（覆盖）。"""
    extra = {}
    env_file = str(unit.get("env_file") or "").strip()
    if env_file:
        path = Path(env_file)
        if not path.is_absolute():
            base = unit.get("workdir") or "."
            path = Path(base) / path
        extra.update(parse_env_file(path))
    for k, v in (unit.get("env") or {}).items():
        extra[str(k)] = str(v)
    if not extra:
        return None
    env = dict(os.environ)
    env.update(extra)
    return env


class Launchpad:
    def __init__(self, data_dir=None, config=None):
        self.data_dir = data_dir
        # config 传入时共享受调用方持有的配置对象（服务端与设置读写共用一份，避免分叉）
        self.config = config if config is not None else store.load_config(data_dir)
        self._procs = {}
        self._powershell = shutil.which("powershell") or shutil.which("pwsh") or "powershell.exe"

    # ---------- 查询 ----------

    def get_unit(self, unit_id):
        for u in self.config["units"]:
            if u["id"] == unit_id:
                return u
        raise UnitNotFoundError(unit_id)

    def units(self):
        return self.config["units"]

    def log_tail(self, unit_id, limit=LOG_LIMIT):
        self.get_unit(unit_id)
        path = self._log_path(unit_id)
        if not path.exists():
            return "(暂无日志)"
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            f.seek(max(0, size - limit))
            data = f.read()
        return data.decode("utf-8", errors="replace")

    # ---------- 变更 ----------

    def create_unit(self, name, kind, command, workdir="", order=None,
                    env=None, env_file=None):
        with store.CONFIG_LOCK:
            if kind not in ("service", "task"):
                kind = "service"
            unit = {
                "id": uuid.uuid4().hex[:12],
                "kind": kind,
                "name": name,
                "command": command,
                "workdir": workdir or "",
                "order": order if order is not None else len(self.config["units"]),
                "status": "stopped",
                "pid": None,
                "start_time": None,
                "exit_code": None,
                "last_exit": None,
                "run_token": None,
                "owned": "managed",
                "created_at": time.time(),
                "last_started_at": None,
                "env": dict(env or {}),
                "env_file": str(env_file or "").strip(),
            }
            self.config["units"].append(unit)
            self._save()
            return dict(unit)

    def update_unit(self, unit_id, fields):
        """编辑单元（运行中禁止），返回更新后的副本；字段缺失抛 ValueError。"""
        with store.CONFIG_LOCK:
            unit = self.get_unit(unit_id)
            if unit["status"] == "running":
                raise UnitBusyError("单元运行中，先停止再编辑")
            fields = fields or {}
            name = str(fields.get("name") or "").strip()
            command = str(fields.get("command") or "").strip()
            if not name or not command:
                raise ValueError("name 与 command 为必填")
            unit["name"] = name
            unit["command"] = command
            unit["workdir"] = str(fields.get("workdir") or "").strip()
            if fields.get("kind") in ("service", "task"):
                unit["kind"] = fields["kind"]
            env = fields.get("env")
            unit["env"] = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
            unit["env_file"] = str(fields.get("env_file") or "").strip()
            self._save()
            return dict(unit)

    def reorder(self, ids):
        """按收到的 id 顺序重排（稳定排序：未涉及的 id 相对顺序不变，
        服务/任务两区可独立排序互不干扰）。"""
        with store.CONFIG_LOCK:
            order = {i: n for n, i in enumerate(ids)}
            self.config["units"].sort(key=lambda u: order.get(u["id"], u.get("order", len(order))))
            for n, u in enumerate(self.config["units"]):
                u["order"] = n
            self._save()

    def claim(self, pid, name=None, kind="service", command="", workdir=None, unit_id=None):
        """把一个已在运行的当前用户进程认领为受管单元。

        与 local-ops 的 attach 同规则：只读校验（存在、非自身、属当前
        用户、能读 cwd），校验通过才写入。unit_id 给定时绑定到既有单元
        （对话框先建后认领的流程），否则新建单元。认领的进程没有
        run token，停止时退化为 create_time + 用户身份双重校验。
        """
        pid = int(pid)
        if pid == psutil.Process().pid:
            raise ClaimError("不能认领总控台自身")
        with store.CONFIG_LOCK:
            if not psutil.pid_exists(pid):
                raise ClaimError("进程已不存在，无法认领")
            for u in self.config["units"]:
                if u["status"] == "running" and u["pid"] == pid \
                        and (unit_id is None or u["id"] != unit_id):
                    raise ClaimError("该进程已被「%s」管理，不能重复认领" % u["name"])
            try:
                proc = psutil.Process(pid)
                create_time = proc.create_time()
                username = proc.username()
                if workdir is None:
                    workdir = proc.cwd() or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
                raise ClaimError("无法读取进程信息，已取消认领：%s" % e)
            if not _same_user(username):
                raise ClaimError("该进程不属于当前用户，不能认领")
            if unit_id is not None:
                unit_ref = self.get_unit(unit_id)
            else:
                unit = self.create_unit(
                    name or ("pid-%d" % pid),
                    kind if kind in ("service", "task") else "service",
                    command or ("（外部进程，PID %d）" % pid),
                    workdir,
                )
                unit_ref = self.get_unit(unit["id"])
            unit_ref["status"] = "running"
            unit_ref["pid"] = pid
            unit_ref["start_time"] = create_time
            unit_ref["last_started_at"] = time.time()
            unit_ref["owned"] = "claimed"
            unit_ref["run_token"] = None
            self._save()
            return dict(unit_ref)

    def delete_unit(self, unit_id):
        with store.CONFIG_LOCK:
            unit = self.get_unit(unit_id)
            self.refresh()
            if unit["status"] == "running":
                raise UnitBusyError("单元正在运行，先停止再删除")
            self.config["units"] = [u for u in self.config["units"] if u["id"] != unit_id]
            self._procs.pop(unit_id, None)
            self._save()

    def start(self, unit_id):
        with store.CONFIG_LOCK:
            self.refresh()
            unit = self.get_unit(unit_id)
            if unit["status"] == "running":
                raise AlreadyRunningError("单元已在运行")
            token = secrets.token_urlsafe(16)
            log = self._open_log_append(unit_id)
            log.write(("\n===== 启动 %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8"))
            log.flush()
            # 运行令牌以 PowerShell 注释形式追加：命令行可校验归属，
            # 溯源引擎据此把受管进程标记为「总控台」启动，且不影响执行语义。
            script = "%s\n# %s%s" % (unit["command"], attribution.RUN_TOKEN_ARG_PREFIX, token)
            cmd = [self._powershell, "-NoProfile", "-NonInteractive", "-Command", script]
            try:
                popen = subprocess.Popen(
                    cmd,
                    cwd=unit["workdir"] or None,
                    env=effective_env(unit),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
                )
            except OSError as e:
                log.close()
                raise Error("启动失败: %s" % e)
            log.close()  # 子进程已继承句柄；关掉本端避免句柄泄漏
            unit["pid"] = popen.pid
            unit["status"] = "running"
            unit["exit_code"] = None
            unit["run_token"] = token
            unit["owned"] = "managed"
            unit["last_started_at"] = time.time()
            try:
                unit["start_time"] = psutil.Process(popen.pid).create_time()
            except psutil.Error:
                unit["start_time"] = None
            self._procs[unit_id] = popen
            self._save()
            return dict(unit)

    def stop(self, unit_id):
        with store.CONFIG_LOCK:
            return self._stop_locked(unit_id)

    def _stop_locked(self, unit_id):
        unit = self.get_unit(unit_id)
        if unit["pid"] is None:
            unit["status"] = "stopped"
            self._save()
            return dict(unit)
        if not psutil.pid_exists(unit["pid"]):
            self._mark_gone(unit)
            self._save()
            return dict(unit)
        proc = psutil.Process(unit["pid"])
        # 三重校验（对齐上游 run token + 进程组 + UID）：
        # ① create_time 与记录一致（防 PID 复用）② 命令行含本次运行令牌
        # ③ 进程属当前用户。认领的进程没有令牌，退化为 ①+③。
        try:
            actual = proc.create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            actual = None
        if unit["start_time"] is not None:
            if actual is None or abs(actual - unit["start_time"]) > 1.0:
                raise StopRejectedError(
                    "PID %d 的创建时间与记录不符，疑似已被复用，拒绝终止" % unit["pid"])
        try:
            cmdline = " ".join(proc.cmdline() or [])
            username = proc.username()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            cmdline, username = None, None
        if unit["run_token"] and cmdline is not None \
                and attribution.RUN_TOKEN_ARG_PREFIX + unit["run_token"] not in cmdline:
            raise StopRejectedError(
                "PID %d 的命令行与本次运行令牌不符，拒绝终止外部进程" % unit["pid"])
        if cmdline is not None and not _same_user(username):
            raise StopRejectedError(
                "PID %d 不属于当前用户，拒绝终止" % unit["pid"])
        result = subprocess.run(
            ["taskkill", "/PID", str(unit["pid"]), "/T", "/F"],
            capture_output=True, timeout=_KILL_TIMEOUT,
        )
        if result.returncode != 0:
            # taskkill 失败：进程可能恰好自行退出了——确认后按已消失处理，
            # 确认还活着则如实报错，不再静默当作已停止
            if psutil.pid_exists(unit["pid"]):
                detail = (result.stderr or b"").decode("utf-8", "replace").strip()
                raise Error("终止 PID %d 失败：%s" % (
                    unit["pid"], detail or "taskkill 返回码 %d" % result.returncode))
            self._mark_gone(unit)
            self._save()
            return dict(unit)
        popen = self._procs.pop(unit_id, None)
        if popen is not None:
            popen.poll()  # 回收退出状态，避免 Popen 残留告警
        unit["pid"] = None
        unit["start_time"] = None
        unit["exit_code"] = None
        unit["status"] = "stopped"
        self._save()
        return dict(unit)

    def restart(self, unit_id):
        unit = self.get_unit(unit_id)
        if unit["status"] == "running":
            self.stop(unit_id)
        return self.start(unit_id)

    # ---------- 状态调和 ----------

    def refresh(self):
        with store.CONFIG_LOCK:
            dirty = False
            for unit in self.config["units"]:
                popen = self._procs.get(unit["id"])
                if popen is not None and popen.poll() is not None:
                    unit["status"] = "exited"
                    unit["exit_code"] = popen.returncode
                    unit["last_exit"] = self._exit_record(unit, popen.returncode)
                    unit["pid"] = None
                    unit["start_time"] = None
                    del self._procs[unit["id"]]
                    dirty = True
                elif unit["status"] == "running" and unit["pid"] is not None \
                        and not psutil.pid_exists(unit["pid"]):
                    self._mark_gone(unit)
                    dirty = True
            if dirty:
                self._save()

    def _exit_record(self, unit, code):
        started = unit.get("last_started_at")
        return {
            "code": code,
            "at": time.time(),
            "duration": (time.time() - started) if started else None,
        }

    def _mark_gone(self, unit):
        unit["pid"] = None
        unit["start_time"] = None
        unit["last_exit"] = self._exit_record(unit, None)
        if unit["kind"] == "task":
            unit["status"] = "exited"
        else:
            unit["status"] = "stopped"

    # ---------- 内部 ----------

    def _save(self):
        store.save_config(self.data_dir, self.config)

    def _log_path(self, unit_id):
        d = store.logs_dir(self.data_dir)
        d.mkdir(parents=True, exist_ok=True)
        return d / ("%s.log" % unit_id)

    def _open_log_append(self, unit_id):
        path = self._log_path(unit_id)
        if path.exists() and path.stat().st_size > LOG_LIMIT:
            with open(path, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - LOG_LIMIT // 2))
                tail = f.read()
            with open(path, "wb") as f:
                f.write("[日志已按上限裁剪]\n".encode("utf-8"))
                f.write(tail)
        return open(path, "ab")
