"""进程/端口快照。

热路径用一次 NtQuerySystemInformation 批量枚举拿到全部进程的
名称、父进程、创建时间、工作集内存与 CPU 时间（对齐上游 macOS
`ps -axo` 单调用哲学）；psutil 只用于端口表、cwd/命令行按需查询、
用户名/状态列的渐进补齐，以及原生路径不可用时的整体回退。
CPU% 由两次扫描的 CPU 时间差计算，避免逐进程两次采样的开销。
"""

import socket
import time

import psutil

_TRUNCATE_LEN = 200
_CMDLINE_TRUNC = 240
_STATIC_TTL_TICKS = 6   # cwd（端口属主）每 6 个 tick 刷新一次
_EXTRA_BUDGET = 1.0     # 用户名/状态列渐进补齐的单 tick 时间预算（秒）
_FILETIME_EPOCH = 116444736000000000  # 1601-01-01 → unix 的 100ns 偏移


# ---------------- 原生批量枚举（Windows） ----------------

_PTR_FMT = None  # 惰性确定（x64 "<Q" / x86 "<I"）


def _native_cpu_times_table():
    """一次系统调用返回 {pid: (name, ppid, create_time_unix, rss, cpu_time_100ns)}。

    仅 Windows 可用；任何异常由调用方捕获后回退 psutil。
    """
    global _PTR_FMT
    import ctypes
    import struct

    if _PTR_FMT is None:
        _PTR_FMT = "<Q" if ctypes.sizeof(ctypes.c_void_p) == 8 else "<I"
    ntdll = ctypes.windll.ntdll
    size = 1 << 20
    buf = None
    for _ in range(8):
        buf = ctypes.create_string_buffer(size)
        status = ntdll.NtQuerySystemInformation(5, buf, size, None)  # SystemProcessInformation
        if status == 0:
            break
        if status == 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH
            size *= 2
            continue
        return None
    else:
        return None

    out = {}
    off = 0
    ptr = _PTR_FMT
    while True:
        next_off = struct.unpack_from("<I", buf, off)[0]
        create_time, user_t, kernel_t = struct.unpack_from("<3q", buf, off + 0x20)
        name_len = struct.unpack_from("<H", buf, off + 0x38)[0]
        name_ptr = struct.unpack_from(ptr, buf, off + 0x40)[0]
        pid = struct.unpack_from(ptr, buf, off + 0x50)[0]
        ppid = struct.unpack_from(ptr, buf, off + 0x58)[0]
        rss = struct.unpack_from("<Q", buf, off + 0x90)[0]
        name = ""
        if name_len and name_ptr:
            try:
                name = ctypes.string_at(name_ptr, name_len).decode("utf-16-le", errors="replace")
            except Exception:
                pass
        out[pid] = (
            name,
            ppid,
            (create_time - _FILETIME_EPOCH) / 1e7 if create_time > 0 else None,
            rss,
            user_t + kernel_t,
        )
        if not next_off:
            break
        off += next_off
    return out


def _validate_native(table):
    """抽样对照 psutil：任何一处名称/父进程不一致即判不可信。"""
    import random

    candidates = [pid for pid in table if pid > 4]
    if not candidates:
        return False
    for pid in random.sample(candidates, min(5, len(candidates))):
        try:
            p = psutil.Process(pid)
            name, ppid, ct, rss, _ = table[pid]
            if p.name() != name or p.ppid() != ppid:
                return False
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return True


# ---------------- 快照器 ----------------

class Snapshotter:
    def __init__(self):
        self._native_ok = None           # None=未验证；True/False=验证结果
        self._cpu_prev = {}              # pid -> (cpu_time_100ns, wall_ts)
        self._extra = {}                 # pid -> {"username":…, "status":…}
        self._cwd_cache = {}
        self._tick = 0
        self._cores = max(1, psutil.cpu_count() or 1)

    # ----- 对外小工具（保持既有 API） -----

    def get_cwd(self, pid):
        """端口属主工作目录（用于项目归属判断）。缓存，静态轮换时刷新。"""
        if pid is None:
            return None
        if self._tick % _STATIC_TTL_TICKS == 1 or pid not in self._cwd_cache:
            try:
                cwd = psutil.Process(pid).cwd()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                cwd = None
            self._cwd_cache[pid] = cwd
        return self._cwd_cache.get(pid)

    def snapshot(self):
        self._tick += 1
        now = time.time()
        if self._native_ok is None:
            try:
                table = _native_cpu_times_table()
                self._native_ok = bool(table) and _validate_native(table)
            except Exception:
                table = None
                self._native_ok = False
        elif self._native_ok:
            try:
                table = _native_cpu_times_table()
            except Exception:
                table = None
            if not table:
                table = None
        else:
            table = None

        if table is not None:
            procs, pid_names = self._snapshot_native(table, now)
        else:
            procs, pid_names = self._snapshot_psutil()
        alive = set(pid_names)
        self._cpu_prev = {p: v for p, v in self._cpu_prev.items() if p in alive}
        self._extra = {p: v for p, v in self._extra.items() if p in alive}
        for pid in list(self._cwd_cache):
            if pid not in alive:
                del self._cwd_cache[pid]

        ports = self._scan_ports(pid_names)
        return {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "cpu": round(psutil.cpu_percent(None), 1),
            "mem": psutil.virtual_memory().percent,
            "proc_count": len(procs),
            "procs": procs,
            "ports": ports,
        }

    # ----- 原生快照 -----

    def _snapshot_native(self, table, now):
        procs = []
        pid_names = {}
        for pid, (name, ppid, created, rss, cpu_total) in table.items():
            if pid == 0:  # System Idle Process：无意义
                continue
            prev = self._cpu_prev.get(pid)
            self._cpu_prev[pid] = (cpu_total, now)
            if prev is None or now <= prev[1]:
                cpu = 0.0
            else:
                cpu = max(0.0, (cpu_total - prev[0]) / 1e7 / (now - prev[1]) * 100.0)
            extra = self._extra.get(pid) or {}
            info = {
                "pid": pid,
                "ppid": ppid if ppid else None,
                "name": name or "",
                "status": extra.get("status", ""),
                "username": extra.get("username", ""),
                "rss": rss,
                "started": int(created) if created else None,
                "cpu": round(cpu, 1),
            }
            procs.append(info)
            if info["name"]:
                pid_names[pid] = info["name"]
        self._fill_extra(procs)
        return procs, pid_names

    def _fill_extra(self, procs):
        """用户名/状态列按需渐进补齐（原生枚举不提供这两个字段）。"""
        start = time.time()
        pending = [p for p in procs if p["pid"] not in self._extra]
        for p in pending:
            try:
                proc = psutil.Process(p["pid"])
                with proc.oneshot():
                    username = proc.username() or ""
                    status = proc.status() or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                username, status = "", ""
            self._extra[p["pid"]] = {"username": username, "status": status}
            p["username"], p["status"] = username, status
            if time.time() - start > _EXTRA_BUDGET:
                break

    # ----- psutil 回退快照 -----

    def _snapshot_psutil(self):
        procs = []
        pid_names = {}
        for p in psutil.process_iter():
            pid = p.pid
            if pid == 0:
                continue
            cpu = 0.0
            try:
                cpu = round(p.cpu_percent(None), 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            try:
                with p.oneshot():
                    info = {
                        "pid": pid,
                        "ppid": p.ppid(),
                        "name": p.name() or "",
                        "status": p.status() or "",
                        "username": p.username() or "",
                        "rss": p.memory_info().rss,
                        "started": int(p.create_time()),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                try:
                    info = {"pid": pid, "ppid": None, "name": p.name() or "", "status": "",
                            "username": "", "rss": 0, "started": None}
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
            info["cpu"] = cpu
            procs.append(info)
            if info["name"]:
                pid_names[pid] = info["name"]
        return procs, pid_names

    # ----- 端口 -----

    def _scan_ports(self, pid_names):
        ports = []
        try:
            conns = psutil.net_connections(kind="inet")
        except (psutil.AccessDenied, OSError):
            conns = []
        for c in conns:
            listening = (
                (c.type == socket.SOCK_STREAM and c.status == psutil.CONN_LISTEN)
                or (c.type == socket.SOCK_DGRAM and c.status == psutil.CONN_NONE)
            )
            if not listening or c.laddr.port == 0:
                continue
            proto = "tcp" if c.type == socket.SOCK_STREAM else "udp"
            ports.append({
                "proto": proto,
                "addr": f"{c.laddr.ip}:{c.laddr.port}",
                "pid": c.pid,
                "proc": pid_names.get(c.pid, ""),
            })
        ports.sort(key=lambda x: (x["addr"], x["proto"]))
        return ports


def get_cmdline(pid):
    try:
        return _truncate(" ".join(psutil.Process(pid).cmdline() or []), _CMDLINE_TRUNC)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""


def get_proc_context(pid):
    """按需取进程上下文：命令行 + 工作目录（用于「端口 → 项目」关联）。"""
    ctx = {"pid": pid, "cmdline": "", "cwd": None}
    try:
        p = psutil.Process(pid)
        ctx["cmdline"] = _truncate(" ".join(p.cmdline() or []), _CMDLINE_TRUNC)
        ctx["cwd"] = p.cwd()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        pass
    return ctx


def _truncate(text, limit=_TRUNCATE_LEN):
    return text[:limit]
