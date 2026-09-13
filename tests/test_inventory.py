import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import inventory


def write(root: Path, rel: str, entries):
    """entries: {文件名: 内容}，以 "/" 结尾的条目创建目录"""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    for name, content in entries.items():
        p = d / name
        if name.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.write_text(content, encoding="utf-8")


class ProjectScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        pkg = json.dumps({"name": "web", "scripts": {"dev": "vite", "start": "vite preview"}})
        write(self.root, "web", {"package.json": pkg})
        write(self.root, "web-pnpm", {
            "package.json": json.dumps({"name": "wp", "scripts": {"dev": "vite"}}),
            "pnpm-lock.yaml": "",
        })
        write(self.root, "pyapp", {"manage.py": ""})
        write(self.root, "goapp", {"go.mod": "module goapp\n"})
        write(self.root, "rsapp", {"Cargo.toml": ""})
        write(self.root, "dkapp", {"docker-compose.yml": ""})
        write(self.root, "site", {"index.html": "<h1>hi</h1>"})
        write(self.root, "junk", {"readme.txt": "nothing here"})
        write(self.root, "gitonly", {".git/": None})
        write(self.root, "node_modules/pkg", {"package.json": pkg})
        write(self.root, "deep/a/b/c", {"package.json": pkg})
        write(self.root, "big/dist", {"package.json": pkg})

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self):
        projects = inventory.scan_projects([self.root])
        return {p["path"]: p for p in projects}

    def test_node_project_with_candidates(self):
        p = self.scan()[str(self.root / "web")]
        self.assertEqual(p["type"], "node")
        self.assertEqual(p["candidates"], [
            {"label": "dev", "command": "npm run dev"},
            {"label": "start", "command": "npm start"},
        ])

    def test_pnpm_lock_switches_package_manager(self):
        p = self.scan()[str(self.root / "web-pnpm")]
        self.assertEqual(p["candidates"][0]["command"], "pnpm run dev")

    def test_python_django_style(self):
        p = self.scan()[str(self.root / "pyapp")]
        self.assertEqual(p["type"], "python")
        self.assertEqual(p["candidates"][0]["command"], "python manage.py runserver")

    def test_go_rust_docker_static(self):
        s = self.scan()
        self.assertEqual(s[str(self.root / "goapp")]["candidates"][0]["command"], "go run .")
        self.assertEqual(s[str(self.root / "rsapp")]["candidates"][0]["command"], "cargo run")
        self.assertEqual(s[str(self.root / "dkapp")]["candidates"][0]["command"], "docker compose up")
        self.assertEqual(s[str(self.root / "site")]["candidates"][0]["command"], "python -m http.server 8000")

    def test_junk_dir_is_not_a_project(self):
        s = self.scan()
        self.assertNotIn(str(self.root / "junk"), s)

    def test_git_repo_without_markers_is_a_project(self):
        p = self.scan()[str(self.root / "gitonly")]
        self.assertEqual(p["type"], "git")
        self.assertEqual(p["candidates"], [])

    def test_excluded_dirs_never_become_projects(self):
        s = self.scan()
        self.assertNotIn(str(self.root / "node_modules/pkg"), s)
        self.assertNotIn(str(self.root / "big/dist"), s)

    def test_depth_limit_respected(self):
        s = self.scan()
        self.assertNotIn(str(self.root / "deep/a/b/c"), s)

    def test_scan_root_itself_is_never_a_project(self):
        """扫描根是容器：即使根目录本身有特征文件也不算项目（避免主目录误判）。"""
        self.root.joinpath("package.json").write_text('{"scripts": {"dev": "x"}}', encoding="utf-8")
        s = self.scan()
        self.assertNotIn(str(self.root), s)

    def test_multiple_roots(self):
        other = self.root / "other"
        write(other, "foo", {"Cargo.toml": ""})
        projects = inventory.scan_projects([self.root, other])
        paths = {p["path"] for p in projects}
        self.assertIn(str(other / "foo"), paths)

    @unittest.skipUnless(shutil.which("git"), "需要已安装 git")
    def test_git_info_branch_and_dirty(self):
        repo = self.root / "gitrepo"
        write(repo, ".", {"pyproject.toml": ""})
        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True,
                           capture_output=True, text=True, timeout=15)
        git("init", "-b", "main")
        git("config", "user.email", "t@t.local")
        git("config", "user.name", "t")
        git("add", ".")
        git("commit", "-m", "init")
        projects = inventory.scan_projects([self.root])
        p = next(x for x in projects if x["path"] == str(repo))
        self.assertEqual(p["git_branch"], "main")
        self.assertFalse(p["git_dirty"])
        (repo / "extra.txt").write_text("dirty", encoding="utf-8")
        projects = inventory.scan_projects([self.root])
        p = next(x for x in projects if x["path"] == str(repo))
        self.assertEqual(p["git_branch"], "main")
        self.assertTrue(p["git_dirty"])

    def test_git_info_non_repo_returns_none(self):
        d = self.root / "plain"
        d.mkdir()
        if shutil.which("git"):
            branch, dirty = inventory.git_info(d)
            self.assertIsNone(branch)
            self.assertIsNone(dirty)


class MatchProjectTest(unittest.TestCase):
    PROJECTS = [
        {"path": "C:\\proj\\web", "type": "node"},
        {"path": "C:\\proj\\web\\admin", "type": "node"},
        {"path": "D:\\other\\api", "type": "go"},
    ]

    def test_exact_cwd_matches(self):
        p = inventory.match_project("C:\\proj\\web", self.PROJECTS)
        self.assertEqual(p["path"], "C:\\proj\\web")

    def test_subdir_matches_longest_prefix(self):
        p = inventory.match_project("C:\\proj\\web\\admin\\src", self.PROJECTS)
        self.assertEqual(p["path"], "C:\\proj\\web\\admin")

    def test_no_match(self):
        self.assertIsNone(inventory.match_project("C:\\unrelated\\x", self.PROJECTS))
        self.assertIsNone(inventory.match_project(None, self.PROJECTS))


class AgentEvalTest(unittest.TestCase):
    REGISTRY = {"agents": [
        {"id": "claude", "name": "Claude Code", "binary": "claude",
         "version_args": ["claude", "--version"],
         "config_dir": ".claude",
         "credential_files": [".claude/.credentials.json"]},
        {"id": "ghost", "name": "Ghost", "binary": "definitely-not-a-real-binary-xyz",
         "version_args": ["definitely-not-a-real-binary-xyz", "--version"]},
    ]}

    @staticmethod
    def _registry_path():
        return Path(__file__).parent.parent / "registry" / "agent-tools.json"

    def test_evaluate_with_fake_probe(self):
        def probe(entry):
            if entry["id"] == "claude":
                return {"found": True, "path": "C:\\t\\claude.cmd", "version": "2.1.0",
                        "logged_in": True, "config_dir_exists": True, "matched_by": ["binary"]}
            return {"found": False, "path": None, "version": None, "logged_in": False,
                    "config_dir_exists": False, "matched_by": []}

        results = inventory.evaluate_agents(self.REGISTRY, probe)
        self.assertEqual(len(results), 2)
        claude = next(r for r in results if r["id"] == "claude")
        self.assertEqual(claude["name"], "Claude Code")
        self.assertTrue(claude["found"])
        self.assertEqual(claude["version"], "2.1.0")
        self.assertTrue(claude["logged_in"])
        ghost = next(r for r in results if r["id"] == "ghost")
        self.assertFalse(ghost["found"])
        self.assertIsNone(ghost["version"])

    def test_missing_id_entries_skipped_and_probe_errors_tolerated(self):
        def bad_probe(entry):
            raise RuntimeError("probe exploded")

        results = inventory.evaluate_agents({"agents": [{"name": "no-id"}]}, bad_probe)
        self.assertEqual(results, [])

    def test_registry_json_valid_and_unique_ids(self):
        reg = inventory.load_registry(self._registry_path())
        ids = [a["id"] for a in reg["agents"]]
        self.assertEqual(len(ids), len(set(ids)))
        for a in reg["agents"]:
            self.assertIn("binary", a)
            self.assertIn("version_args", a)

    @unittest.skipUnless(shutil.which("claude"), "claude not installed")
    def test_local_probe_on_real_claude(self):
        reg = inventory.load_registry(self._registry_path())
        entry = next(a for a in reg["agents"] if a["id"] == "claude")
        res = inventory.LocalProbe(timeout=5)(entry)
        self.assertTrue(res["found"])
        self.assertTrue(res["version"])
        self.assertTrue(res["config_dir_exists"])


if __name__ == "__main__":
    unittest.main()
