import tempfile
import unittest
from pathlib import Path

from core import diagnose


class HealthTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_workdir_flagged(self):
        r = diagnose.health({"command": "npm run dev", "workdir": str(self.dir / "nope"), "kind": "service"})
        kinds = [i["kind"] for i in r["issues"]]
        self.assertIn("cwd-missing", kinds)
        self.assertTrue(r["blocking"])

    def test_missing_runtime_flagged(self):
        r = diagnose.health({"command": "definitely-not-a-real-cmd-xyz run", "workdir": "", "kind": "service"})
        self.assertIn("runtime-missing", [i["kind"] for i in r["issues"]])

    def test_builtin_commands_pass(self):
        r = diagnose.health({"command": "Start-Sleep -Seconds 5", "workdir": "", "kind": "service"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["issues"], [])

    def test_relative_script_missing_flagged(self):
        r = diagnose.health({"command": ".\\scripts\\run.ps1", "workdir": str(self.dir), "kind": "task"})
        self.assertIn("script-missing", [i["kind"] for i in r["issues"]])

    def test_node_modules_hint(self):
        (self.dir / "package.json").write_text("{}", encoding="utf-8")
        r = diagnose.health({"command": "npm run dev", "workdir": str(self.dir), "kind": "service"})
        self.assertIn("deps-missing", [i["kind"] for i in r["issues"]])

    def test_which_resolvable_runtime_passes(self):
        r = diagnose.health({"command": "python -m http.server", "workdir": "", "kind": "service"})
        self.assertEqual(r["status"], "ok")


class DiagnoseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_npm_missing_script_lists_available(self):
        (self.dir / "package.json").write_text(
            '{"scripts": {"dev": "vite", "build": "vite build"}}', encoding="utf-8")
        r = diagnose.diagnose(
            {"command": "npm run serve", "workdir": str(self.dir), "kind": "service", "exit_code": 1},
            "npm error Missing script: \"serve\"")
        kinds = [i["kind"] for i in r["issues"]]
        self.assertIn("npm-script", kinds)
        issue = next(i for i in r["issues"] if i["kind"] == "npm-script")
        self.assertIn("dev", issue["detail"])

    def test_cannot_find_module(self):
        r = diagnose.diagnose({"command": "node x.js", "workdir": "", "kind": "service", "exit_code": 1},
                              "Error: Cannot find module 'express'")
        self.assertIn("deps-missing", [i["kind"] for i in r["issues"]])

    def test_cmd_not_recognized(self):
        r = diagnose.diagnose({"command": "node x.js", "workdir": "", "kind": "service", "exit_code": 1},
                              "node : 无法将「node」识别为 cmdlet")
        self.assertIn("runtime-missing", [i["kind"] for i in r["issues"]])

    def test_port_busy_patterns(self):
        for log in ("Error: listen EADDRINUSE: address already in use 0.0.0.0:3000",
                    "OSError: [WinError 10048] 通常每个套接字地址(协议/网络地址/端口)只允许使用一次"):
            r = diagnose.diagnose({"command": "node x.js", "workdir": "", "kind": "service", "exit_code": 1}, log)
            self.assertIn("port-busy", [i["kind"] for i in r["issues"]])

    def test_python_module_missing(self):
        r = diagnose.diagnose({"command": "python app.py", "workdir": "", "kind": "service", "exit_code": 1},
                              "ModuleNotFoundError: No module named 'fastapi'")
        self.assertIn("py-deps", [i["kind"] for i in r["issues"]])

    def test_generic_nonzero_exit(self):
        r = diagnose.diagnose({"command": "node x.js", "workdir": "", "kind": "service", "exit_code": 7}, "some noise")
        kinds = [i["kind"] for i in r["issues"]]
        self.assertIn("generic", kinds)

    def test_exit_zero_no_issues(self):
        r = diagnose.diagnose({"command": "Start-Sleep 1", "workdir": "", "kind": "service", "exit_code": 0}, "")
        self.assertEqual(r["issues"], [])

    def test_install_deps_hint(self):
        (self.dir / "package.json").write_text("{}", encoding="utf-8")
        hint = diagnose.install_deps_hint({"workdir": str(self.dir)})
        self.assertIsNotNone(hint)
        self.assertIn("npm install", hint)
        self.assertIsNone(diagnose.install_deps_hint({"workdir": ""}))


if __name__ == "__main__":
    unittest.main()
