"""总控台 — Windows 本地运维控制台服务端（单用户、仅回环）。

用法: python server.py [--port 9600] [--no-browser] [--data-dir PATH]

本模块只保留 HTTP 骨架（Host/令牌/Origin 鉴权、路由分发、静态文件）
与应用状态编排（快照、SSE、盘点、设置）。API 路由按域注册在 core/api/。
"""
import argparse
import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

from core import api, attribution, diagnose, inventory, launchpad, scanner, security, store
from core.meta import (APP_MARKER, DEFAULT_PORT, PORT_TRIES, REGISTRY_PATH,
                       ROOT_DIR, STATIC_DIR, VERSION)

HEALTH_TTL = 30          # 运行前健康检查缓存秒数
ORIGIN_TTL = 60          # 进程溯源缓存秒数
BASELINE_MAX_AGE = 14 * 86400  # 端口基线保留天数

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


def make_server(data_dir=None, port=DEFAULT_PORT):
    server = _OpsServer(("127.0.0.1", port), data_dir=data_dir)
    return server


class _OpsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, data_dir=None):
        super().__init__(addr, _OpsHandler)
        self.app = _OpsApp(self, data_dir=data_dir)

    def handle_error(self, request, client_address):
        import traceback
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError)):
            return
        traceback.print_exc()


class _OpsApp:
    def __init__(self, server, data_dir=None):
        self.server = server
        self.data_dir = data_dir
        self.token = secrets.token_urlsafe(24)
        self.config = store.load_config(data_dir)
        self.launchpad = launchpad.Launchpad(data_dir, config=self.config)
        self.snapshotters = scanner.Snapshotter()
        self.latest_snapshot = None
        self.inventory_cache = None
        self.inventory_scanning = False
        self._inventory_started_at = 0.0
        self._health_cache = {}   # unit_id -> (fingerprint, ts, result)
        self._origin_cache = {}   # pid -> (ts, {"label","icon"})
        self._subscribers = set()
        self._lock = threading.Lock()
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    # ----- 配置 -----

    @property
    def settings(self):
        return self.config["settings"]

    def save_config(self):
        store.save_config(self.data_dir, self.config)

    def update_settings(self, body):
        """校验并应用设置变更，落盘后返回最新 settings；非法值抛 ValueError。"""
        if not isinstance(body, dict):
            raise ValueError("JSON 格式错误")
        with store.CONFIG_LOCK:
            settings = self.settings
            if isinstance(body.get("scan_roots"), list):
                settings["scan_roots"] = [str(x) for x in body["scan_roots"] if str(x).strip()]
            if "poll_interval" in body:
                try:
                    value = max(1, min(300, int(body["poll_interval"])))
                except (TypeError, ValueError):
                    raise ValueError("poll_interval 必须为整数")
                settings["poll_interval"] = value
            if isinstance(body.get("theme"), str):
                settings["theme"] = body["theme"] if body["theme"] in ("light", "dark", "system") else "system"
            if isinstance(body.get("ignored_ports"), list):
                settings["ignored_ports"] = [str(x) for x in body["ignored_ports"]]
            if "notifications" in body:
                settings["notifications"] = bool(body["notifications"])
            self.save_config()
            return settings

    # ----- 扫描循环 -----

    def _tick_loop(self):
        while True:
            try:
                snap = self.snapshotters.snapshot()
            except Exception as e:
                snap = {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "error": "扫描异常: %s" % e,
                    "cpu": 0, "mem": 0, "proc_count": 0, "procs": [], "ports": [],
                }
            try:
                self.launchpad.refresh()
            except Exception:
                import traceback
                traceback.print_exc(file=sys.stderr)
            self._finalize_snapshot(snap)
            self.latest_snapshot = snap
            self.broadcast("snapshot", snap)
            try:
                interval = max(1, int(self.settings.get(
                    "poll_interval", store.DEFAULT_POLL_INTERVAL)))
            except (TypeError, ValueError):
                interval = store.DEFAULT_POLL_INTERVAL
            time.sleep(interval)

    def _finalize_snapshot(self, snap):
        """附加运行单元、端口归属（managed/project/other）、溯源与新端口标记。"""
        units = self.launchpad.units()
        snap["units"] = [self._unit_with_health(u) for u in units]
        snap["poll_interval"] = self.settings.get("poll_interval", store.DEFAULT_POLL_INTERVAL)
        try:
            cache = self.inventory_cache
            if cache is None:
                # 盘点可能要遍历整个扫描根（家目录可能很大），绝不能阻塞快照：
                # 首帧只做异步预热，盘点就绪前端口一律归为「其他」
                self.refresh_inventory_async()
            elif cache.get("error") and time.time() - self._inventory_started_at > 60:
                # 盘点失败后每分钟静默重试一次，避免后台线程崩溃循环
                self.refresh_inventory_async()
        except Exception:
            pass
        projects = (self.inventory_cache or {}).get("projects") or []
        ignored = {str(x) for x in self.settings.get("ignored_ports") or []}
        port_stats = {"managed": 0, "project": 0, "other": 0}
        table = {}
        kept = []
        # 锚点进程（PowerShell）的整棵子进程树都算受管——对齐上游
        # 「进程组」语义：npm/node 等真正的监听者通常是锚点的后代。
        # 父链直接用快照里已有的 ppid 数据建映射（一次系统调用都省了）。
        unit_by_pid = {u["pid"]: u for u in units if u["status"] == "running" and u["pid"]}
        ppid_map = {p["pid"]: p.get("ppid") for p in snap.get("procs", [])}
        for port in snap.get("ports", []):
            port_num = str(port.get("addr", "").rsplit(":", 1)[-1])
            if port_num in ignored:
                continue
            pid = port.get("pid")
            port["origin"] = "other"
            port["project"] = None
            port["origin_label"] = None
            port["unit_id"] = None
            port["unit_name"] = None
            owner = unit_by_pid.get(pid)
            if owner is None and pid:
                # 子进程监听：沿父链找本控制台的锚点
                owner = self._anchor_owner(pid, unit_by_pid, ppid_map)
            if owner:
                port["origin"] = "managed"
                port["unit_id"] = owner["id"]
                port["unit_name"] = owner["name"]
            else:
                proj = inventory.match_project(self.snapshotters.get_cwd(pid), projects)
                if proj:
                    port["origin"] = "project"
                    port["project"] = proj["path"]
            if port["origin"] != "other" and pid:
                port["origin_label"] = self._origin_for(pid, table)
            port_stats[port["origin"]] += 1
            kept.append(port)
        snap["ports"] = kept
        snap["port_stats"] = port_stats
        self._apply_port_baseline(snap)

    def _anchor_owner(self, pid, unit_by_pid, ppid_map):
        """pid 的祖先链上是否挂在本控制台某个锚点之下（纯字典查找）。"""
        cur = pid
        for _ in range(8):
            ppid = ppid_map.get(cur)
            if not ppid or ppid == cur:
                return None
            unit = unit_by_pid.get(ppid)
            if unit is not None:
                return unit
            cur = ppid
        return None

    def _unit_with_health(self, unit):
        """给单元附加运行前健康检查结果（带指纹 + TTL 缓存）。"""
        fp = "%s|%s|%s" % (unit.get("command", ""), unit.get("workdir", ""), unit.get("kind", ""))
        cached = self._health_cache.get(unit["id"])
        if cached and cached[0] == fp and time.time() - cached[1] < HEALTH_TTL:
            result = cached[2]
        else:
            try:
                result = diagnose.health(unit)
            except Exception:
                result = {"status": "unknown", "blocking": False, "issues": []}
            self._health_cache[unit["id"]] = (fp, time.time(), result)
        out = dict(unit)
        out["health"] = result
        return out

    def _origin_for(self, pid, table):
        """端口属主溯源（跨 tick 缓存；同一 tick 内共享祖先表）。"""
        cached = self._origin_cache.get(pid)
        if cached and time.time() - cached[0] < ORIGIN_TTL:
            return cached[1]
        try:
            table.update(attribution.build_ancestry_table(pid))
            result = attribution.attribute_origin(pid, table)
        except Exception:
            result = None
        self._origin_cache[pid] = (time.time(), result)
        return result

    def _apply_port_baseline(self, snap):
        """持久化端口基线：首次快照播种；此后新出现且非本控制台管理的
        端口标 isNew，供前端做「发现新端口」提醒。只在基线真正变化时落盘。"""
        with store.CONFIG_LOCK:
            baseline = self.config.setdefault("port_baseline", {})
            now = time.time()
            seeded = bool(baseline)
            addrs = set()
            changed = False
            for port in snap.get("ports", []):
                addr = port.get("addr")
                if not addr:
                    continue
                addrs.add(addr)
                if addr not in baseline:
                    baseline[addr] = now
                    changed = True
                    port["isNew"] = seeded and port.get("origin") != "managed"
                else:
                    port["isNew"] = False
            stale = [a for a, ts in baseline.items() if a not in addrs
                     and now - ts > BASELINE_MAX_AGE]
            for a in stale:
                del baseline[a]
                changed = True
            if changed:
                self.save_config()

    # ----- 诊断 / 健康 -----

    def get_diagnosis(self, unit_id):
        unit = self.launchpad.get_unit(unit_id)
        try:
            log_tail = self.launchpad.log_tail(unit_id, 6000)
        except Exception:
            log_tail = ""
        result = diagnose.diagnose(unit, log_tail)
        result["install_hint"] = diagnose.install_deps_hint(unit)
        return result

    # ----- 总控台自身重启 / 停止 -----

    def spawn_replacement(self, port):
        """以分离进程拉起新的总控台；它等本进程退出后接管同端口。"""
        args = [sys.executable, str(ROOT_DIR / "server.py"),
                "--port", str(port), "--wait-exit", str(os.getpid()), "--no-browser"]
        if self.data_dir:
            args += ["--data-dir", str(self.data_dir)]
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(args, cwd=str(ROOT_DIR), creationflags=flags, close_fds=True)

    def schedule_shutdown(self, delay=0.6):
        def _stop():
            try:
                self.server.shutdown()
            except Exception:
                pass
        timer = threading.Timer(delay, _stop)
        timer.daemon = True
        timer.start()

    # ----- SSE -----

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(q)
            latest = self.latest_snapshot
        if latest is None:
            # 首个订阅者：立即补一帧快照，避免等下一个 tick
            threading.Thread(target=self.sync_snapshot, daemon=True).start()
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)

    def broadcast(self, event, payload):
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            return
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait((event, text))
            except threading.QueueFull:
                pass

    def sync_snapshot(self):
        """同步取一帧全新快照（供 /api/snapshot 与首帧订阅者）。"""
        try:
            snap = self.snapshotters.snapshot()
        except Exception as e:
            snap = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "error": str(e),
                    "cpu": 0, "mem": 0, "proc_count": 0, "procs": [], "ports": []}
        self._finalize_snapshot(snap)
        self.latest_snapshot = snap
        self.broadcast("snapshot", snap)

    # ----- 盘点 -----

    def get_inventory(self):
        if self.inventory_cache is None:
            self._build_inventory()
        return self.inventory_cache

    def refresh_inventory_async(self):
        if self.inventory_scanning:
            return
        self.inventory_scanning = True
        self._inventory_started_at = time.time()
        threading.Thread(target=self._build_inventory, daemon=True).start()

    def _build_inventory(self):
        """构建盘点缓存。失败不再反复崩线程：缓存错误结果（前端可见），
        由 _finalize_snapshot 定时重试。"""
        try:
            registry = inventory.load_registry(REGISTRY_PATH)
            agents = inventory.evaluate_agents(registry, inventory.LocalProbe())
            projects = inventory.scan_projects(self.settings.get("scan_roots") or [])
            result = {
                "agents": agents,
                "projects": projects,
                "scan_roots": self.settings.get("scan_roots") or [],
                "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            import traceback
            traceback.print_exc(file=sys.stderr)
            result = {
                "agents": [], "projects": [],
                "scan_roots": self.settings.get("scan_roots") or [],
                "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": "盘点失败: %s" % e,
            }
        self.inventory_cache = result
        self.broadcast("inventory", result)
        self.inventory_scanning = False


class _OpsHandler(BaseHTTPRequestHandler):
    server_version = "TotalConsole/" + VERSION
    protocol_version = "HTTP/1.1"

    # ----- 入口 -----

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        app = self.server.app
        port = self.server.server_address[1]

        # 1. Host 校验（所有请求，防 DNS rebinding）
        if not security.is_host_allowed(self.headers.get("Host", ""), port):
            self._send(400, "Bad Host")
            return
        # 2. 写请求：token + Origin
        if method in ("POST", "PUT", "DELETE"):
            if "X-Ops-Token" not in self.headers or not secrets.compare_digest(
                    self.headers.get("X-Ops-Token", ""), app.token):
                self._send(401, "缺少或错误的操作令牌")
                return
            if not security.is_origin_allowed(self.headers.get("Origin"), port):
                self._send(403, "Origin 不被允许")
                return
        # 3. 路由分发（core/api/ 注册表），未命中则静态文件兜底
        try:
            handler, params = api.match(method, path)
            if handler is not None:
                return handler(api.ApiContext(self, app, port, params, query))
            if method == "GET":
                return self.handle_static(path)
            self._send(404, "Not Found")
        except Exception as e:  # 兜底：面板不能崩
            import traceback
            traceback.print_exc(file=sys.stderr)
            self._send(500, "服务器内部错误: %s" % e)

    # ----- 静态文件 -----

    def handle_static(self, path):
        rel = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._send(404, "Not Found")
        ext = target.suffix.lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        payload = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    # ----- 工具 -----

    def _send(self, status, text):
        payload = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass  # 安静模式


# ----- 单实例检测与端口选择 -----

def is_ops_instance(port, timeout=1.0):
    """探测 127.0.0.1:port 上是否已有本应用的实例在跑。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
            s.sendall(("GET /api/ping HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                       "Connection: close\r\n\r\n" % port).encode("ascii"))
            chunks = []
            while True:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks).decode("utf-8", errors="replace")
            return APP_MARKER in data
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="总控台 — Windows 本地运维控制台")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="首选端口（默认 9600，被占则尝试 9601–9609）")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--data-dir", default=None, help="数据目录（默认 %%APPDATA%%\\总控台）")
    parser.add_argument("--wait-exit", type=int, default=None, metavar="PID",
                        help="等该进程退出后再绑定端口（供「重启」接管端口）")
    args = parser.parse_args(argv)

    if args.wait_exit is not None:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if not psutil.pid_exists(args.wait_exit):
                    break
            except Exception:
                break
            time.sleep(0.2)

    for port in range(args.port, args.port + PORT_TRIES):
        if is_ops_instance(port):
            print("总控台已在运行：http://127.0.0.1:%d" % port)
            webbrowser.open("http://127.0.0.1:%d" % port)
            return 0
        try:
            server = make_server(data_dir=args.data_dir, port=port)
            break
        except OSError:
            continue
    else:
        print("错误：端口 %d-%d 均被占用，且不是总控台实例" % (args.port, args.port + PORT_TRIES - 1))
        return 1

    url = "http://127.0.0.1:%d" % server.server_address[1]
    print("总控台已启动：%s （Ctrl+C 停止）" % url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
