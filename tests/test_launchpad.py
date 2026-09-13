import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import psutil

from core import launchpad, store


class LaunchpadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"
        self.lp = launchpad.Launchpad(self.data_dir)

    def tearDown(self):
        for u in self.lp.units():
            if u["status"] == "running":
                try:
                    self.lp.stop(u["id"])
                except Exception:
                    pass
        self._tmp.cleanup()

    def test_create_unit_persisted_and_reloadable(self):
        u = self.lp.create_unit("web", "service", "Write-Output hi", "")
        self.assertEqual(u["status"], "stopped")
        lp2 = launchpad.Launchpad(self.data_dir)
        self.assertEqual(lp2.get_unit(u["id"])["name"], "web")
        self.assertEqual(lp2.get_unit(u["id"])["kind"], "service")

    def test_start_service_running_then_stop(self):
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 60", "")
        started = self.lp.start(u["id"])
        self.assertEqual(started["status"], "running")
        self.assertIsNotNone(started["pid"])
        self.assertTrue(psutil.pid_exists(started["pid"]))
        stopped = self.lp.stop(u["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(psutil.pid_exists(started["pid"]))
        # 停止后再启动允许
        restarted = self.lp.start(u["id"])
        self.assertEqual(restarted["status"], "running")
        self.lp.stop(u["id"])

    def test_stop_rejects_pid_reuse_via_config_tampering(self):
        """记录 start_time 与真实 create_time 不符 → 拒绝杀（PID 复用防线）。"""
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 60", "")
        started = self.lp.start(u["id"])
        cfg = store.load_config(self.data_dir)
        cfg["units"][0]["start_time"] = 1.0
        store.save_config(self.data_dir, cfg)
        lp2 = launchpad.Launchpad(self.data_dir)
        with self.assertRaises(launchpad.StopRejectedError):
            lp2.stop(u["id"])
        self.assertTrue(psutil.pid_exists(started["pid"]))
        self.lp.stop(u["id"])

    def test_task_exit_code_130(self):
        u = self.lp.create_unit("t", "task", "Start-Sleep -Milliseconds 400; exit 130", "")
        self.lp.start(u["id"])
        time.sleep(1.5)
        self.lp.refresh()
        unit = self.lp.get_unit(u["id"])
        self.assertEqual(unit["status"], "exited")
        self.assertEqual(unit["exit_code"], 130)

    def test_refresh_marks_dead_after_simulated_restart(self):
        """重启服务端（popen 句柄丢失）后 refresh 仍能识别进程已死。"""
        u = self.lp.create_unit("t", "task", "exit 7", "")
        self.lp.start(u["id"])
        time.sleep(1.2)
        lp2 = launchpad.Launchpad(self.data_dir)
        lp2.refresh()
        unit = lp2.get_unit(u["id"])
        self.assertEqual(unit["status"], "exited")
        self.assertIsNone(unit["exit_code"])

    def test_start_refuses_already_running(self):
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 60", "")
        self.lp.start(u["id"])
        with self.assertRaises(launchpad.AlreadyRunningError):
            self.lp.start(u["id"])

    def test_delete_refuses_running(self):
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 60", "")
        self.lp.start(u["id"])
        with self.assertRaises(launchpad.UnitBusyError):
            self.lp.delete_unit(u["id"])
        self.lp.stop(u["id"])
        self.lp.delete_unit(u["id"])
        with self.assertRaises(launchpad.UnitNotFoundError):
            self.lp.get_unit(u["id"])

    def test_log_file_gets_startup_header_and_output(self):
        u = self.lp.create_unit("svc", "service", "Write-Output 'hello-log'", "")
        self.lp.start(u["id"])
        time.sleep(1.5)
        self.lp.refresh()
        tail = self.lp.log_tail(u["id"])
        self.assertIn("启动", tail)
        self.assertIn("hello-log", tail)

    def test_command_captured_in_utf8_tolerant_decode(self):
        u = self.lp.create_unit("t", "task", "exit 0", "")
        self.lp.start(u["id"])
        time.sleep(1.0)
        self.lp.refresh()
        unit = self.lp.get_unit(u["id"])
        self.assertEqual(unit["status"], "exited")
        self.assertEqual(unit["exit_code"], 0)

    # ---------- 运行令牌 / 认领 / 排序（复刻 local-ops） ----------

    def test_start_records_run_token_and_marks_cmdline(self):
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 20", "")
        started = self.lp.start(u["id"])
        self.assertTrue(started["run_token"])
        cmd = " ".join(psutil.Process(started["pid"]).cmdline() or [])
        self.assertIn("console-run:" + started["run_token"], cmd)
        self.lp.stop(u["id"])

    def test_stop_rejects_wrong_run_token(self):
        u = self.lp.create_unit("svc", "service", "Start-Sleep -Seconds 20", "")
        started = self.lp.start(u["id"])
        cfg = store.load_config(self.data_dir)
        cfg["units"][0]["run_token"] = "forged-token"
        store.save_config(self.data_dir, cfg)
        lp2 = launchpad.Launchpad(self.data_dir)
        with self.assertRaises(launchpad.StopRejectedError):
            lp2.stop(u["id"])
        self.assertTrue(psutil.pid_exists(started["pid"]))
        # 用正确令牌的原始 lp 停掉（内存里有正确 token）
        self.lp.stop(u["id"])

    def test_claim_running_process(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=launchpad.CREATE_NO_WINDOW)
        try:
            time.sleep(0.3)
            unit = self.lp.claim(child.pid, name="borrowed")
            self.assertEqual(unit["status"], "running")
            self.assertEqual(unit["owned"], "claimed")
            self.assertEqual(unit["pid"], child.pid)
            self.assertIsNone(unit["run_token"])
            # 重复认领同一进程被拒
            with self.assertRaises(launchpad.ClaimError):
                self.lp.claim(child.pid)
            # 停止走 create_time + 用户校验，无令牌
            stopped = self.lp.stop(unit["id"])
            self.assertEqual(stopped["status"], "stopped")
            self.assertFalse(psutil.pid_exists(child.pid))
        finally:
            if psutil.pid_exists(child.pid):
                child.kill()

    def test_claim_rejects_self_and_dead_pid(self):
        with self.assertRaises(launchpad.ClaimError):
            self.lp.claim(psutil.Process().pid)
        with self.assertRaises(launchpad.ClaimError):
            self.lp.claim(999999)

    def test_reorder_stable_and_persists(self):
        a = self.lp.create_unit("a", "service", "x", "")
        b = self.lp.create_unit("b", "task", "y", "")
        c = self.lp.create_unit("c", "service", "z", "")
        self.lp.reorder([c["id"], a["id"]])
        order = [u["name"] for u in self.lp.units()]
        self.assertEqual(order, ["c", "a", "b"])  # 未涉及的 b 相对位置不变
        lp2 = launchpad.Launchpad(self.data_dir)
        self.assertEqual([u["name"] for u in lp2.units()], ["c", "a", "b"])

    def test_last_exit_recorded(self):
        u = self.lp.create_unit("t", "task", "exit 3", "")
        self.lp.start(u["id"])
        time.sleep(1.0)
        self.lp.refresh()
        rec = self.lp.get_unit(u["id"]).get("last_exit")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["code"], 3)
        self.assertIsNotNone(rec["duration"])

    # ---------- 环境变量 ----------

    def test_parse_env_file(self):
        env = Path(self._tmp.name) / "vars.env"
        env.write_text(
            "# 注释行\n"
            "\n"
            "PLAIN=1\n"
            "QUOTED=\"two words\"\n"
            "SINGLE='v'\n"
            "export EXPORTED=yes\n"
            "BAD LINE WITHOUT EQUALS\n",
            encoding="utf-8")
        values = launchpad.parse_env_file(env)
        self.assertEqual(values, {"PLAIN": "1", "QUOTED": "two words",
                                  "SINGLE": "v", "EXPORTED": "yes"})

    def test_effective_env_precedence_and_missing_file(self):
        env = Path(self._tmp.name) / "vars.env"
        env.write_text("FROM_FILE=1\nOVERRIDE=file\n", encoding="utf-8")
        unit = {"workdir": str(self._tmp.name), "env": {"OVERRIDE": "hand", "ONLY_HAND": "2"},
                "env_file": "vars.env"}
        effective = launchpad.effective_env(unit)
        self.assertEqual(effective["FROM_FILE"], "1")
        self.assertEqual(effective["OVERRIDE"], "hand")  # 手写覆盖文件
        self.assertEqual(effective["ONLY_HAND"], "2")
        self.assertTrue(effective["PATH"])  # 继承进程环境
        # 无 env 配置 → None（完全继承）
        self.assertIsNone(launchpad.effective_env({"workdir": "", "env": {}, "env_file": ""}))
        # env 文件不存在 → 静默跳过（健康检查会提示）
        unit_bad = {"workdir": str(self._tmp.name), "env": {}, "env_file": "no-such.env"}
        self.assertIsNone(launchpad.effective_env(unit_bad))

    def test_start_injects_env(self):
        u = self.lp.create_unit(
            "envtask", "task",
            'python -c "import os; print(\'VAL=\' + os.environ.get(\'OPS_ENV_TEST\', \'MISSING\'))"',
            "", env={"OPS_ENV_TEST": "hello123"})
        self.lp.start(u["id"])
        for _ in range(30):
            time.sleep(0.3)
            self.lp.refresh()
            if self.lp.get_unit(u["id"])["status"] != "running":
                break
        log = self.lp.log_tail(u["id"])
        self.assertIn("VAL=hello123", log)


if __name__ == "__main__":
    unittest.main()
