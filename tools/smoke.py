"""总控台前后端联通性冒烟验证。

以临时数据目录 + 随机端口启动真实服务器，逐一请求全部 API 路由
（含 4xx 分支）、SSE 首帧与静态资源，输出逐项 pass/fail；
任何失败以非零码退出。每个开发闭环收尾时运行：

    python tools/smoke.py
"""
import argparse
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import server as core_server  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    mark = "PASS" if cond else "FAIL"
    RESULTS.append((mark, name, detail))
    print("[%s] %s%s" % (mark, name, ("  --  " + detail) if (detail and not cond) else ""))


def req(method, path, port, token=None, body=None, headers=None, timeout=30):
    """发一次 HTTP 请求，返回 (status, body_bytes)。写请求自动带同源 Origin。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    hdrs = dict(headers or {})
    if token:
        hdrs["X-Ops-Token"] = token
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if method in ("POST", "PUT", "DELETE") and "Origin" not in hdrs:
        hdrs["Origin"] = "http://127.0.0.1:%d" % port
    try:
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, data
    finally:
        conn.close()


def req_json(method, path, port, **kw):
    status, data = req(method, path, port, **kw)
    try:
        return status, json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return status, None


def wait_ready(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, body = req("GET", "/api/ping", port, timeout=2)
            if status == 200 and b"total-console" in body:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def sse_first_event(port, timeout=25):
    """打开 /api/stream，读到第一个 event:+data 帧即返回原始缓冲。"""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        s.sendall(("GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                   "Accept: text/event-stream\r\n\r\n" % port).encode("ascii"))
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b"event:" in buf and b"data:" in buf:
                break
        return buf
    finally:
        s.close()


def main():
    parser = argparse.ArgumentParser(description="总控台联通性冒烟验证")
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录（调试用）")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="ops-smoke-")
    srv = core_server.make_server(data_dir=tmp, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("冒烟服务器：http://127.0.0.1:%d  数据目录：%s" % (port, tmp))

    token = None
    unit_id = None
    child = None
    try:
        # ---- 就绪 ----
        check("服务器就绪 (/api/ping)", wait_ready(port))

        # ---- 静态资源 ----
        status, body = req("GET", "/", port)
        check("首页可达", status == 200 and b"<html" in body.lower(), "status=%s" % status)
        status, body = req("GET", "/app.js", port)
        check("app.js 可达", status == 200, "status=%s" % status)
        status, body = req("GET", "/style.css", port)
        check("style.css 可达", status == 200, "status=%s" % status)
        status, _ = req("GET", "/no-such-file.js", port)
        check("未知静态文件 404", status == 404, "status=%s" % status)
        status, _ = req("GET", "/../core/server.py", port)
        check("路径穿越被拦截", status == 404, "status=%s" % status)

        # ---- 会话与安全 ----
        status, data = req_json("GET", "/api/session", port)
        token = (data or {}).get("token")
        check("会话令牌", status == 200 and token, "status=%s" % status)

        status, _ = req("POST", "/api/units", port, body={"name": "x", "command": "x"})
        check("无令牌写请求 401", status == 401, "status=%s" % status)
        status, _ = req("POST", "/api/units", port, token=token,
                        headers={"Origin": "http://evil.example"},
                        body={"name": "x", "command": "x"})
        check("跨源 Origin 403", status == 403, "status=%s" % status)
        status, _ = req("GET", "/api/session", port, headers={"Host": "evil.example:%d" % port})
        check("伪造 Host 400", status == 400, "status=%s" % status)
        status, _ = req("GET", "/api/no-such-route", port)
        check("未知 API 404", status == 404, "status=%s" % status)

        # ---- 快照 ----
        status, snap = req_json("GET", "/api/snapshot", port)
        check("快照结构", status == 200 and isinstance(snap, dict)
              and "cpu" in snap and "mem" in snap and "ports" in snap
              and "procs" in snap and "units" in snap,
              "status=%s keys=%s" % (status, list(snap or {})[:8]))
        check("快照轮询间隔", (snap or {}).get("poll_interval", 0) == 2,
              "poll_interval=%r" % (snap or {}).get("poll_interval"))

        # ---- 设置 ----
        status, settings = req_json("GET", "/api/settings", port)
        check("设置读取", status == 200 and isinstance(settings, dict), "status=%s" % status)
        status, settings = req_json("PUT", "/api/settings", port, token=token,
                                    body={"poll_interval": 3, "theme": "dark"})
        check("设置写入回读", status == 200 and settings
              and settings.get("poll_interval") == 3 and settings.get("theme") == "dark",
              "status=%s body=%r" % (status, settings))
        status, _ = req("PUT", "/api/settings", port, token=token,
                        body={"poll_interval": "abc"})
        check("非法 poll_interval 400", status == 400, "status=%s" % status)

        # ---- 进程上下文 ----
        status, data = req_json("GET", "/api/cmdline?pid=%d" % os.getpid(), port)
        check("cmdline 查询", status == 200 and isinstance((data or {}).get("cmdline"), str),
              "status=%s" % status)
        status, data = req_json("GET", "/api/proc-context?pid=%d" % os.getpid(), port)
        check("proc-context 查询", status == 200 and isinstance(data, dict),
              "status=%s" % status)

        # ---- 单元生命周期 ----
        status, _ = req_json("POST", "/api/units", port, token=token, body={"name": " "})
        check("缺字段建单元 400", status == 400, "status=%s" % status)

        cmd = '"%s" -c "import time; time.sleep(120)"' % sys.executable
        status, unit = req_json("POST", "/api/units", port, token=token,
                                body={"name": "冒烟服务", "command": cmd,
                                      "kind": "service", "workdir": str(ROOT)})
        unit_id = (unit or {}).get("id")
        check("创建单元 201", status == 201 and unit_id, "status=%s" % status)

        status, units = req_json("GET", "/api/units", port)
        check("单元列表含新单元", status == 200 and any(u.get("id") == unit_id for u in units or []),
              "status=%s" % status)

        status, unit = req_json("POST", "/api/units/%s/start" % unit_id, port, token=token)
        check("启动单元", status == 200 and (unit or {}).get("status") == "running",
              "status=%s body=%r" % (status, unit))

        status, health = req_json("GET", "/api/units/%s/health" % unit_id, port)
        check("健康检查", status == 200 and isinstance((health or {}).get("issues"), list),
              "status=%s" % status)
        status, diag = req_json("GET", "/api/units/%s/diagnose" % unit_id, port)
        check("诊断", status == 200 and isinstance(diag, dict) and "issues" in diag,
              "status=%s" % status)

        time.sleep(0.8)  # 等日志落盘
        status, body = req("GET", "/api/logs/%s" % unit_id, port)
        check("日志读取", status == 200 and body, "status=%s len=%s" % (status, len(body)))

        status, unit = req_json("POST", "/api/units/%s/stop" % unit_id, port, token=token)
        check("停止单元", status == 200 and (unit or {}).get("status") == "stopped",
              "status=%s body=%r" % (status, unit))

        status, unit = req_json("PUT", "/api/units/%s" % unit_id, port, token=token,
                                body={"name": "冒烟服务(改)", "command": cmd, "kind": "task"})
        check("编辑单元", status == 200 and (unit or {}).get("name") == "冒烟服务(改)",
              "status=%s" % status)

        status, _ = req("DELETE", "/api/units/%s" % unit_id, port, token=token)
        check("删除单元 204", status == 204, "status=%s" % status)
        unit_id = None

        # ---- 排序 ----
        ids = []
        for i in range(2):
            _, u = req_json("POST", "/api/units", port, token=token,
                            body={"name": "排序%d" % i, "command": "echo hi"})
            ids.append(u["id"])
        status, _ = req_json("POST", "/api/units/reorder", port, token=token,
                             body={"ids": [ids[1], ids[0]]})
        _, units = req_json("GET", "/api/units", port)
        order = [u["id"] for u in units or []]
        check("重排生效", status == 200 and order[:2] == [ids[1], ids[0]],
              "status=%s order=%s" % (status, order[:2]))
        status, _ = req("POST", "/api/units/reorder", port, token=token, body={"ids": "bad"})
        check("重排非法参数 400", status == 400, "status=%s" % status)
        for i in ids:
            req("DELETE", "/api/units/%s" % i, port, token=token)

        # ---- 认领 ----
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                 cwd=str(ROOT))
        time.sleep(0.5)
        status, claimed = req_json("POST", "/api/units/adopt/claim", port, token=token,
                                   body={"pid": child.pid, "name": "冒烟认领",
                                         "kind": "service", "command": "python"})
        check("认领外部进程 201", status == 201 and (claimed or {}).get("id"),
              "status=%s body=%r" % (status, claimed))
        claimed_id = (claimed or {}).get("id")
        if claimed_id:
            status, unit = req_json("POST", "/api/units/%s/stop" % claimed_id, port, token=token)
            check("停止认领单元", status == 200, "status=%s" % status)
            req("DELETE", "/api/units/%s" % claimed_id, port, token=token)

        # ---- 盘点 ----
        status, inv = req_json("GET", "/api/inventory", port, timeout=90)
        check("盘点读取", status == 200 and isinstance(inv, dict)
              and "agents" in inv and "projects" in inv, "status=%s" % status)
        status, _ = req_json("POST", "/api/inventory/refresh", port, token=token)
        check("盘点异步刷新 202", status == 202, "status=%s" % status)

        # ---- SSE ----
        buf = sse_first_event(port)
        check("SSE 首帧", b"event:" in buf and b"data:" in buf,
              buf[:120].decode("utf-8", "replace"))
    finally:
        # 清理：先停掉可能在跑的单元再关服务器，避免 Windows 日志句柄残留
        try:
            if unit_id:
                req("POST", "/api/units/%s/stop" % unit_id, port, token=token, timeout=10)
                req("DELETE", "/api/units/%s" % unit_id, port, token=token, timeout=10)
        except OSError:
            pass
        if child and child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        srv.shutdown()
        srv.server_close()
        if not args.keep:
            for _ in range(3):
                try:
                    shutil.rmtree(tmp)
                    break
                except OSError:
                    time.sleep(0.5)

    fails = [r for r in RESULTS if r[0] == "FAIL"]
    print("\n===== 冒烟结果：%d 通过 / %d 失败 =====" % (
        len(RESULTS) - len(fails), len(fails)))
    if fails:
        for _, name, detail in fails:
            print("  失败项：%s  %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
