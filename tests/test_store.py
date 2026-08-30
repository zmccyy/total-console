import json
import tempfile
import unittest
from pathlib import Path

from core import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_load_returns_defaults_and_creates_nothing_yet(self):
        cfg = store.load_config(self.data_dir)
        self.assertEqual(cfg["settings"]["scan_roots"], [str(Path.home())])
        self.assertEqual(cfg["units"], [])
        self.assertEqual(cfg["settings"]["poll_interval"], store.DEFAULT_POLL_INTERVAL)
        self.assertEqual(cfg["schemaVersion"], store.CURRENT_SCHEMA_VERSION)
        # 首次加载不应在磁盘上留下任何文件
        self.assertFalse(self.data_dir.exists())

    def test_save_creates_backup_of_previous_config(self):
        cfg = store.load_config(self.data_dir)
        cfg["settings"]["theme"] = "dark"
        store.save_config(self.data_dir, cfg)
        cfg["settings"]["theme"] = "light"
        store.save_config(self.data_dir, cfg)
        bak = json.loads(store.backup_path(self.data_dir).read_text(encoding="utf-8"))
        self.assertEqual(bak["settings"]["theme"], "dark")

    def test_newer_schema_version_preserved(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "config.json").write_text(
            json.dumps({"schemaVersion": 99, "units": [], "future_key": 1}),
            encoding="utf-8")
        cfg = store.load_config(self.data_dir)
        self.assertEqual(cfg["schemaVersion"], 99)
        self.assertEqual(cfg["future_key"], 1)

    def test_save_then_load_roundtrip(self):
        cfg = store.load_config(self.data_dir)
        cfg["settings"]["scan_roots"] = ["E:\\项目"]
        cfg["units"] = [
            {"id": "u1", "kind": "service", "name": "web",
             "command": "npm run dev", "workdir": "E:\\项目\\web"}
        ]
        store.save_config(self.data_dir, cfg)
        cfg2 = store.load_config(self.data_dir)
        self.assertEqual(cfg2["settings"]["scan_roots"], ["E:\\项目"])
        self.assertEqual(cfg2["units"][0]["name"], "web")
        # 缺省值合并：保存过 settings 的部分字段后其余默认仍在
        self.assertEqual(cfg2["settings"]["theme"], "system")

    def test_legacy_top_level_scan_roots_migrated_into_settings(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "config.json").write_text(
            json.dumps({"scan_roots": ["E:\\legacy"], "units": [], "settings": {
                "poll_interval": 5, "theme": "system", "ignored_ports": []}}),
            encoding="utf-8")
        cfg = store.load_config(self.data_dir)
        self.assertEqual(cfg["settings"]["scan_roots"], ["E:\\legacy"])
        self.assertNotIn("scan_roots", cfg)

    def test_corrupt_json_falls_back_to_defaults(self):
        self.data_dir.mkdir(parents=True)
        (self.data_dir / "config.json").write_text("{corrupt", encoding="utf-8")
        cfg = store.load_config(self.data_dir)
        self.assertEqual(cfg["units"], [])

    def test_defaults_not_shared_between_loads(self):
        """首次加载返回的 defaults 是全新结构：一次变更不得污染下一次加载（回归：共享列表 bug）。"""
        a = store.load_config(self.data_dir)
        b = store.load_config(self.data_dir)
        a["units"].append({"id": "leak"})
        self.assertEqual(b["units"], [])

    def test_data_dir_default_resolves_under_appdata(self):
        d = store.data_dir()
        self.assertTrue(str(d).endswith("总控台"))

    def test_logs_dir_is_under_data_dir(self):
        self.assertEqual(store.logs_dir(self.data_dir), self.data_dir / "logs")


if __name__ == "__main__":
    unittest.main()
