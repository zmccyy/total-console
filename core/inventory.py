import json
import os
import subprocess
import shutil
from pathlib import Path

EXCLUDED_DIRS = {
    "node_modules", ".git", "dist", "build", ".next", ".nuxt",
    "__pycache__", ".venv", "venv", ".idea", ".vscode", ".cache",
    "target", ".hg", ".svn", "Local Settings", "Application Data",
}
MAX_DEPTH = 3
GIT_TIMEOUT = 4  # 单个 git 只读查询超时秒数

_NODE_SCRIPT_ORDER = ("dev", "start", "serve")
_TYPE_PRIORITY = ("node", "python", "go", "rust", "docker", "static", "git")


def git_info(path):
    """只读查询项目 git 分支与脏状态 → (branch|None, dirty|None)。

    git 未安装、目录非仓库或查询失败一律返回 (None, None)，绝不写入。
    CREATE_NO_WINDOW 防止后台线程弹控制台窗口。
    """
    if not shutil.which("git"):
        return None, None
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        branch = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL, creationflags=no_window,
        )
        status = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            stdin=subprocess.DEVNULL, creationflags=no_window,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, None
    if branch.returncode != 0:
        return None, None
    return (branch.stdout.strip() or None), bool(status.stdout.strip())


def scan_projects(roots, max_depth=MAX_DEPTH):
    projects = []
    seen = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for dir_path, markers in _walk(root_path, max_depth):
            key = str(dir_path)
            if key in seen:
                continue
            seen.add(key)
            if dir_path == root_path:
                continue  # 扫描根是容器目录，自身不当作项目（避免整个主目录误判）
            type_, markers, has_git = markers
            if type_ is None:
                continue
            project = {
                "path": key,
                "type": type_,
                "markers": markers,
                "has_git": has_git,
                "candidates": _candidates(type_, dir_path),
            }
            if has_git:
                project["git_branch"], project["git_dirty"] = git_info(dir_path)
            projects.append(project)
    projects.sort(key=lambda p: p["path"])
    return projects


def _walk(root: Path, max_depth: int):
    """产出 (dir, (type, markers, has_git))，含 root 自身。"""
    yield from _walk_dir(root, root, 0, max_depth)


def _walk_dir(root: Path, d: Path, depth: int, max_depth: int):
    if depth > max_depth:
        return
    yield d, _detect(d)
    if depth == max_depth:
        return
    try:
        children = sorted(d.iterdir())
    except OSError:
        return
    for child in children:
        if child.name in EXCLUDED_DIRS or child.name.startswith("."):
            continue
        if child.is_dir() and not child.is_symlink():
            yield from _walk_dir(root, child, depth + 1, max_depth)


def _detect(d: Path):
    try:
        files = {e.name for e in d.iterdir() if e.is_file()}
    except OSError:
        files = set()
    has_git = (d / ".git").is_dir()
    if "package.json" in files:
        return "node", ["package.json"], has_git
    if "pyproject.toml" in files:
        return "python", ["pyproject.toml"], has_git
    if "manage.py" in files:
        return "python", ["manage.py"], has_git
    if "go.mod" in files:
        return "go", ["go.mod"], has_git
    if "Cargo.toml" in files:
        return "rust", ["Cargo.toml"], has_git
    if "docker-compose.yml" in files or "docker-compose.yaml" in files:
        return "docker", ["docker-compose.yml"], has_git
    if "index.html" in files:
        return "static", ["index.html"], has_git
    if has_git:
        return "git", [".git"], has_git
    return None, [], has_git


def match_project(cwd, projects):
    """按最长路径前缀把工作目录归属为某个项目（端口 → 项目关联）。"""
    if not cwd:
        return None
    norm = cwd.replace("\\", "/").lower()
    best = None
    for p in projects:
        pn = p["path"].replace("\\", "/").lower()
        if (norm == pn or norm.startswith(pn + "/")) and \
                (best is None or len(pn) > len(best["path"])):
            best = p
    return best


def _candidates(type_, d: Path):
    if type_ == "node":
        return _node_candidates(d)
    if type_ == "python":
        if (d / "manage.py").is_file():
            return [{"label": "dev", "command": "python manage.py runserver"}]
        return []
    if type_ == "go":
        return [{"label": "run", "command": "go run ."}]
    if type_ == "rust":
        return [{"label": "run", "command": "cargo run"}]
    if type_ == "docker":
        return [{"label": "up", "command": "docker compose up"}]
    if type_ == "static":
        return [{"label": "serve", "command": "python -m http.server 8000"}]
    return []


def _node_candidates(d: Path):
    try:
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
        scripts = pkg.get("scripts") or {}
    except (OSError, json.JSONDecodeError):
        scripts = {}
    pm = "npm"
    if (d / "pnpm-lock.yaml").is_file():
        pm = "pnpm"
    elif (d / "yarn.lock").is_file():
        pm = "yarn"
    out = []
    for key in _NODE_SCRIPT_ORDER:
        if key in scripts:
            command = f"{pm} {key}" if key == "start" else f"{pm} run {key}"
            out.append({"label": key, "command": command})
    return out


# ------------- Agent 盘点 -------------

AGENT_PROBE_TIMEOUT = 5


def evaluate_agents(registry, probe):
    """probe(entry) -> {found, path, version, logged_in, config_dir_exists, matched_by}"""
    results = []
    for entry in registry.get("agents", []):
        if not entry.get("id"):
            continue
        info = probe(entry) if callable(probe) else {}
        results.append({"id": entry["id"], "name": entry.get("name", entry["id"]), **info})
    return results


class LocalProbe:
    """真实探测：shutil.which 找二进制、子进程跑版本、按主目录查凭据文件。"""

    def __init__(self, home=None, timeout=AGENT_PROBE_TIMEOUT):
        self.home = Path(home) if home else Path.home()
        self.timeout = timeout

    def __call__(self, entry):
        binary = entry.get("binary")
        path = None
        found = False
        if binary:
            path = shutil.which(binary)
            found = path is not None
        version = None
        if found:
            version_args = entry.get("version_args")
            if version_args:
                version = self._probe_version(version_args)
        config_dir = entry.get("config_dir")
        config_dir_exists = bool(config_dir) and (self.home / config_dir).exists()
        logged_in = False
        for cred in entry.get("credential_files", []):
            if (self.home / cred).exists():
                logged_in = True
                break
        return {
            "found": found,
            "path": str(path) if found else None,
            "version": version,
            "logged_in": logged_in,
            "config_dir_exists": config_dir_exists,
            "matched_by": ["binary"] if found else [],
        }

    def _probe_version(self, version_args):
        try:
            proc = subprocess.run(
                ["cmd", "/c", *version_args],
                capture_output=True, text=True, timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        line = proc.stdout.strip().splitlines()
        return line[0][:80] if line else None


def load_registry(path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
