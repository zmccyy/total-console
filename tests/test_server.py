import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from core import server


class ServerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.srv = server.make_server(data_dir=Path(cls._tmp.name), port=0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        # 先停掉测试中残留的单元，避免日志文件句柄锁死临时目录清理
        for u in cls.srv.app.launchpad.units():
            if u["status"] == "running":
                try:
                    cls.srv.app.launchpad.stop(u["id"])
                except Exception:
                    pass
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    # ---------- helpers ----------

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def token(self):
        status, data = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        return json.loads(data)["token"]

    def auth_headers(self, extra=None):
        # 浏览器 fetch 同源请求会自带 Origin；http.client 不会，这里补上
        h = {"X-Ops-Token": self.token(),
             "Origin": "http://127.0.0.1:%d" % self.port}
        if extra:
            h.update(extra)
        return h

    # ---------- security ----------

    def test_ping_returns_marker(self):
        status, data = self.request("GET", "/api/ping")
        self.assertEqual(status, 200)
        self.assertIn(server.APP_MARKER, data.decode("utf-8"))

    def test_write_without_token_401(self):
        status, _ = self.request("POST", "/api/units",
                                 body=json.dumps({"name": "x", "command": "echo 1"}))
        self.assertEqual(status, 401)

    def test_write_with_evil_origin_403(self):
        headers = self.auth_headers({"Origin": "http://evil.example.com"})
        status, _ = self.request("POST", "/api/units",
                                 body=json.dumps({"name": "x", "command": "echo 1"}),
                                 headers=headers)
        self.assertEqual(status, 403)

    def test_bad_host_header_400(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/ping", headers={"Host": "evil.example.com:9600"})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 400)

    def test_good_host_header_ok(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/api/ping", headers={"Host": "localhost:%d" % self.port})
        r = c.getresponse()
        r.read()
        c.close()
        self.assertEqual(r.status, 200)

    # ---------- snapshot ----------

    def test_snapshot_structure(self):
        status, data = self.request("GET", "/api/snapshot")
        self.assertEqual(status, 200)
        snap = json.loads(data)
        for key in ("ts", "cpu", "mem", "proc_count", "procs", "ports", "units"):
            self.assertIn(key, snap)
        self.assertGreater(snap["proc_count"], 0)

    # ---------- units ----------

    def test_create_start_stop_flow(self):
        headers = self.auth_headers()
        status, data = self.request("POST", "/api/units",
                                    body=json.dumps({
                                        "name": "flow-svc", "kind": "service",
                                        "command": "Start-Sleep -Seconds 30", "workdir": ""}),
                                    headers=headers)
        self.assertEqual(status, 201)
        uid = json.loads(data)["id"]

        status, _ = self.request("POST", "/api/units/%s/start" % uid, headers=headers)
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/snapshot")
        units = json.loads(data)["units"]
        u = next(x for x in units if x["id"] == uid)
        self.assertEqual(u["status"], "running")
        self.assertIsNotNone(u["pid"])

        status, data = self.request("GET", "/api/logs/%s" % uid)
        self.assertEqual(status, 200)
        self.assertIn("启动", data.decode("utf-8"))

        status, _ = self.request("POST", "/api/units/%s/stop" % uid, headers=headers)
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/snapshot")
        u = next(x for x in json.loads(data)["units"] if x["id"] == uid)
        self.assertEqual(u["status"], "stopped")

        status, _ = self.request("DELETE", "/api/units/%s" % uid, headers=headers)
        self.assertEqual(status, 204)

    def test_create_unit_without_name_400(self):
        status, _ = self.request("POST", "/api/units",
                                 body=json.dumps({"command": "echo 1"}),
                                 headers=self.auth_headers())
        self.assertEqual(status, 400)

    # ---------- settings & inventory ----------

    def test_settings_roundtrip(self):
        status, _ = self.request("PUT", "/api/settings",
                                 body=json.dumps({"scan_roots": ["E:\\项目"]}),
                                 headers=self.auth_headers())
        self.assertEqual(status, 200)
        status, data = self.request("GET", "/api/settings")
        self.assertIn("E:\\项目", json.loads(data)["scan_roots"])

    def test_inventory_shape(self):
        status, data = self.request("GET", "/api/inventory")
        self.assertEqual(status, 200)
        inv = json.loads(data)
        self.assertIsInstance(inv["agents"], list)
        self.assertIsInstance(inv["projects"], list)
        ids = [a["id"] for a in inv["agents"]]
        self.assertIn("claude", ids)

    def test_inventory_refresh_returns_scanning(self):
        status, _ = self.request("POST", "/api/inventory/refresh",
                                 headers=self.auth_headers())
        self.assertEqual(status, 202)

    # ---------- sse ----------

    def test_sse_first_frame_arrives(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("GET", "/api/stream")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        data = r.read(100)
        c.close()
        self.assertIn(b"event:", data)
        self.assertIn(b"data:", data)

    # ---------- single instance ----------

    def test_existing_instance_detected(self):
        self.assertTrue(server.is_ops_instance(self.port))
        # 找一个确定空闲的端口
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        self.assertFalse(server.is_ops_instance(free_port))


    # ---------- 复刻 local-ops：排序 / 认领 / 诊断 / 端口基线 ----------

    def test_units_reorder_roundtrip(self):
        h = self.auth_headers()
        ids = []
        for name in ("r1", "r2"):
            _, data = self.request("POST", "/api/units",
                                   body=json.dumps({"name": name, "kind": "service",
                                                    "command": "echo 1", "workdir": ""}),
                                   headers=h)
            ids.append(json.loads(data)["id"])
        status, _ = self.request("POST", "/api/units/reorder",
                                 body=json.dumps({"ids": ids[::-1]}), headers=h)
        self.assertEqual(status, 200)
        _, data = self.request("GET", "/api/units")
        names = [u["name"] for u in json.loads(data) if u["id"] in ids]
        self.assertEqual(names, ["r2", "r1"])

    def test_reorder_rejects_non_string_ids(self):
        status, _ = self.request("POST", "/api/units/reorder",
                                 body=json.dumps({"ids": [1, 2]}),
                                 headers=self.auth_headers())
        self.assertEqual(status, 400)

    def test_claim_dead_pid_409(self):
        status, _ = self.request("POST", "/api/units/whatever/claim",
                                 body=json.dumps({"pid": 999999}),
                                 headers=self.auth_headers())
        self.assertEqual(status, 409)

    def test_claim_requires_int_pid(self):
        status, _ = self.request("POST", "/api/units/whatever/claim",
                                 body=json.dumps({"pid": "abc"}),
                                 headers=self.auth_headers())
        self.assertEqual(status, 400)

    def test_unit_diagnose_shape(self):
        h = self.auth_headers()
        _, data = self.request("POST", "/api/units",
                               body=json.dumps({"name": "diag", "kind": "service",
                                                "command": "definitely-missing-xyz run", "workdir": ""}),
                               headers=h)
        uid = json.loads(data)["id"]
        status, data = self.request("GET", "/api/units/%s/diagnose" % uid)
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertIn("issues", result)
        self.assertTrue(any(i["kind"] == "runtime-missing" for i in result["issues"]))

    def test_snapshot_units_carry_health(self):
        _, data = self.request("GET", "/api/snapshot")
        snap = json.loads(data)
        for u in snap["units"]:
            self.assertIn("health", u)
            self.assertIn("status", u["health"])

    def test_ignored_ports_hidden_from_snapshot(self):
        app = self.srv.app
        # 拿一个当前真实监听端口
        _, data = self.request("GET", "/api/snapshot")
        ports = json.loads(data)["ports"]
        if not ports:
            self.skipTest("本机没有监听端口")
        port_num = ports[0]["addr"].rsplit(":", 1)[-1]
        app.settings["ignored_ports"] = [port_num]
        try:
            _, data = self.request("GET", "/api/snapshot")
            addrs = [p["addr"] for p in json.loads(data)["ports"]]
            self.assertNotIn(ports[0]["addr"], addrs)
        finally:
            app.settings["ignored_ports"] = []
            app.save_config()

    def test_new_port_flagged_by_baseline(self):
        app = self.srv.app
        app.config["port_baseline"] = {}
        # 基线为空 → 首帧播种，全部不标新
        _, data = self.request("GET", "/api/snapshot")
        snap = json.loads(data)
        self.assertTrue(app.config["port_baseline"])
        self.assertFalse(any(p.get("isNew") for p in snap["ports"]))
        # 下一帧基线已存在，未管理端口若出现在基线外的地址才会标新；
        # 这里仅验证字段存在且类型正确
        _, data = self.request("GET", "/api/snapshot")
        for p in json.loads(data)["ports"]:
            self.assertIsInstance(p.get("isNew", False), bool)

    def test_settings_notifications_roundtrip(self):
        h = self.auth_headers()
        status, _ = self.request("PUT", "/api/settings",
                                 body=json.dumps({"notifications": False}), headers=h)
        self.assertEqual(status, 200)
        _, data = self.request("GET", "/api/settings")
        self.assertFalse(json.loads(data)["notifications"])

    # ---------- 端口一键打开 ----------

    def test_open_url_calls_default_browser(self):
        import unittest.mock as mock
        opened = []
        with mock.patch("os.startfile", side_effect=opened.append):
            status, _ = self.request("POST", "/api/open-url",
                                     body=json.dumps({"url": "http://127.0.0.1:8123/"}),
                                     headers=self.auth_headers())
        self.assertEqual(status, 200)
        self.assertEqual(opened, ["http://127.0.0.1:8123/"])

    def test_open_url_rejects_non_http_scheme(self):
        for bad in ("file:///C:/Windows", "ms-settings:display", "javascript:alert(1)", ""):
            status, _ = self.request("POST", "/api/open-url",
                                     body=json.dumps({"url": bad}),
                                     headers=self.auth_headers())
            self.assertEqual(status, 400, bad)

    # ---------- 终端 / 编辑器打开 ----------

    def test_open_terminal_on_unit(self):
        import unittest.mock as mock
        h = self.auth_headers()
        _, data = self.request("POST", "/api/units",
                               body=json.dumps({"name": "openterm", "kind": "service",
                                                "command": "echo 1",
                                                "workdir": str(Path(__file__).parent)}),
                               headers=h)
        uid = json.loads(data)["id"]
        with mock.patch("core.integrations.open_terminal", return_value={"tool": "wt"}) as m:
            status, data = self.request("POST", "/api/units/%s/open-terminal" % uid, headers=h)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["tool"], "wt")
        self.assertEqual(m.call_count, 1)

    def test_open_editor_missing_workdir_400(self):
        import unittest.mock as mock
        from core import integrations
        h = self.auth_headers()
        _, data = self.request("POST", "/api/units",
                               body=json.dumps({"name": "badwd", "kind": "service",
                                                "command": "echo 1", "workdir": "Z:\\不存在的目录\\xyz"}),
                               headers=h)
        uid = json.loads(data)["id"]
        status, _ = self.request("POST", "/api/units/%s/open-editor" % uid, headers=h)
        self.assertEqual(status, 400)
        # open_terminal 的参数解析同路径：目录不存在直接 400，不真正开窗
        with mock.patch.object(integrations.subprocess, "Popen") as p:
            with self.assertRaises(integrations.OpenError):
                integrations.open_terminal("Z:\\不存在的目录\\xyz")
            p.assert_not_called()

    def test_open_tool_unknown_unit_404(self):
        status, _ = self.request("POST", "/api/units/zzz/open-terminal",
                                 headers=self.auth_headers())
        self.assertEqual(status, 404)

    # ---------- 环境变量 ----------

    def test_unit_env_roundtrip_and_clear(self):
        h = self.auth_headers()
        status, data = self.request("POST", "/api/units",
                                    body=json.dumps({"name": "envd", "kind": "service",
                                                     "command": "echo 1",
                                                     "env": {"A": "1", "N": 2},
                                                     "env_file": ".env"}),
                                    headers=h)
        self.assertEqual(status, 201)
        unit = json.loads(data)
        self.assertEqual(unit["env"], {"A": "1", "N": "2"})
        self.assertEqual(unit["env_file"], ".env")
        status, data = self.request("PUT", "/api/units/%s" % unit["id"],
                                    body=json.dumps({"name": "envd", "command": "echo 1",
                                                     "env": None, "env_file": ""}),
                                    headers=h)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["env"], {})

    def test_unit_env_invalid_400(self):
        h = self.auth_headers()
        for bad in ("notadict", {"k": {"deep": 1}}, [1, 2]):
            status, _ = self.request("POST", "/api/units",
                                     body=json.dumps({"name": "x", "command": "echo 1",
                                                      "env": bad}),
                                     headers=h)
            self.assertEqual(status, 400, repr(bad))


if __name__ == "__main__":
    unittest.main()
