"""规则诊断：运行前健康检查 + 退出后诊断 → 可执行的修复建议列表。

移植自 local-ops 的 inspect_app_health / diagnose_app，Windows 适配：
用 PATHEXT 可执行解析、cmd/PowerShell 报错模式与中文系统错误文案
替代 macOS 的 env/command not found 模式。只读文件系统，绝不执行
或展开用户命令。
"""
import json
import os
import re
import shutil
from pathlib import Path

# PowerShell / cmd 常用内建命令，缺可执行文件不算错误
_SHELL_BUILTINS = {
    "echo", "write-output", "write-host", "start-sleep", "sleep", "exit",
    "cd", "set-location", "pwd", "ls", "dir", "gci", "get-childitem",
    "copy", "copy-item", "cp", "move", "move-item", "mv", "del", "erase",
    "remove-item", "rm", "ri", "md", "mkdir", "rmdir", "cat", "type",
    "get-content", "set-content", "out-file", "select", "select-string",
    "foreach", "foreach-object", "where", "where-object", "if", "else",
    "elseif", "switch", "while", "for", "return", "throw", "try", "catch",
    "finally", "function", "param", "begin", "process", "end", "pause",
    "cls", "clear", "clear-host", "get-help", "man", "get-date", "read-host",
    "get-process", "ps", "stop-process", "kill", "start-process", "start",
    "get-service", "net", "sc", "curl", "wget", "invoke-webrequest",
    "invoke-restmethod", "iex", "invoke-expression", "test-path", "true",
    "false", "null", "get-variable", "set-variable", "get-item", "gi",
    "get-itemproperty", "format-table", "format-list", "ft", "fl", "measure",
    "measure-object", "sort", "sort-object", "group", "group-object",
}

_SCRIPT_EXTS = (".ps1", ".py", ".js", ".mjs", ".cjs", ".ts", ".bat", ".cmd", ".sh",
                ".rb", ".php", ".go", ".rs")

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_PWSH_ASSIGN_RE = re.compile(r"^\$[A-Za-z_][A-Za-z0-9_]*\s*=")


def simple_tokens(command):
    """宽松分词（PowerShell/cmd 风格）；含管道/重定向/换行时返回 None 表示
    无法静态判断，调用方跳过命令层检查。"""
    if not command or any(ch in command for ch in "|;&`\n<>"):
        return None
    try:
        import shlex
        return shlex.split(command, posix=False)
    except ValueError:
        return None


def _strip_quotes(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _resolve_runtime(token, cwd):
    """解析首个命令 token：返回 (描述路径, 是否可执行)。"""
    raw = _strip_quotes(token)
    expanded = os.path.expandvars(raw)
    looks_path = bool(re.match(r'^[A-Za-z]:[\\/]', expanded)) or "/" in raw or "\\" in raw
    if looks_path:
        p = Path(expanded)
        if not p.is_absolute() and cwd:
            p = Path(cwd) / p
        if p.is_file():
            return str(p), True
        return str(p), False
    if shutil.which(raw):
        return raw, True
    if cwd:
        for ext in (".exe", ".cmd", ".bat", ".ps1", ""):
            cand = Path(cwd) / (raw + ext)
            if cand.is_file():
                return str(cand), True
    return raw, False


def health(unit):
    """静态检查配置是否可运行；返回 {status, blocking, issues[]}。"""
    issues = []

    def add(kind, title, detail, fix, action="edit-command"):
        issues.append({"kind": kind, "severity": "error", "title": title,
                       "detail": detail, "fix": fix, "action": action})

    workdir = unit.get("workdir") or ""
    if workdir and not os.path.isdir(workdir):
        add("cwd-missing", "工作目录不可用",
            "找不到配置的工作目录：%s" % workdir,
            "编辑这个命令单元，修正工作目录。", "edit-workdir")

    # .env 文件缺失为非阻断提醒（文件可能按需生成）
    env_file = str(unit.get("env_file") or "").strip()
    if env_file:
        p = Path(env_file)
        if not p.is_absolute() and workdir:
            p = Path(workdir) / p
        if not p.is_file():
            issues.append({"kind": "env-file-missing", "severity": "warning",
                           "title": ".env 文件缺失",
                           "detail": "配置的环境变量文件不存在：%s" % p,
                           "fix": "创建该 .env 文件，或编辑单元清空环境变量文件路径。",
                           "action": "edit-command"})

    if workdir and os.path.isdir(workdir):
        pkg = Path(workdir) / "package.json"
        if pkg.is_file() and not (Path(workdir) / "node_modules").is_dir():
            mgr = ("yarn" if (Path(workdir) / "yarn.lock").is_file()
                   else "pnpm" if (Path(workdir) / "pnpm-lock.yaml").is_file()
                   else "npm")
            add("deps-missing", "依赖未安装（node_modules 缺失）",
                "目录里有 package.json，但没有 node_modules。",
                "在项目目录执行：%s install，装完再启动。" % mgr,
                "install-deps")

    tokens = simple_tokens(unit.get("command") or "")
    if tokens is None:
        return {"status": "error" if issues else "unknown",
                "blocking": bool(issues), "issues": issues}

    idx = 0
    while idx < len(tokens) and (_ASSIGN_RE.match(tokens[idx]) or _PWSH_ASSIGN_RE.match(tokens[idx])):
        idx += 1
    if idx < len(tokens):
        first = _strip_quotes(tokens[idx])
        base = os.path.basename(first).lower()
        if first.startswith((".", ".\\")) or first.endswith(_SCRIPT_EXTS):
            # 直接引用脚本：相对 workdir 检查存在性
            p = Path(first)
            if not p.is_absolute():
                p = (Path(workdir) / p) if workdir else p
            if not p.is_file():
                add("script-missing", "脚本不可用",
                    "找不到脚本：%s" % p,
                    "编辑这个命令单元，修正脚本路径。")
        elif base not in _SHELL_BUILTINS and not base.startswith(("get-", "set-", "new-", "remove-", "invoke-", "out-", "test-")):
            _, ok = _resolve_runtime(tokens[idx], workdir)
            if not ok:
                add("runtime-missing", "找不到 %s" % base,
                    "系统 PATH 和工作目录里都找不到命令「%s」。" % first,
                    "确认该运行时已安装（如 node / python / pnpm），或改用完整路径。")

    errors = [i for i in issues if i.get("severity", "error") == "error"]
    return {"status": "error" if errors else ("warn" if issues else "ok"),
            "blocking": bool(errors), "issues": issues}


def diagnose(unit, log_tail=""):
    """规则诊断：健康检查 + 退出码 + 日志模式 → 修复建议列表。"""
    issues = []
    seen_kinds = set()

    def add(kind, title, detail, fix, action="edit-command"):
        if kind in seen_kinds:
            return
        seen_kinds.add(kind)
        issues.append({"kind": kind, "title": title, "detail": detail,
                       "fix": fix, "action": action})

    for h in health(unit).get("issues", []):
        add(h["kind"], h["title"], h["detail"], h["fix"], h.get("action", "edit-command"))

    cwd = unit.get("workdir") or ""
    log_lower = (log_tail or "").lower()
    code = unit.get("exit_code")

    m = re.search(r"cannot find module ['\"]([^'\"]+)", log_lower)
    if m:
        add("deps-missing", "找不到模块 %s" % m.group(1),
            "日志报 Cannot find module，通常是依赖没装或装坏了。",
            "在项目目录执行：npm install（仍报错再删除 node_modules 重装）。",
            "install-deps")

    m = re.search(r"(.{1,60}?) (?:is not recognized as (?:an internal or external command|the name of a cmdlet)|不是内部或外部命令|无法将.{1,40}?识别为 cmdlet)", log_lower)
    if m:
        token = m.group(1).split()[-1].strip("'\"")
        add("runtime-missing", "找不到运行时：%s" % token,
            "系统里找不到「%s」这个命令（cmd / PowerShell 均未识别）。" % token,
            "确认该运行时已安装并加入 PATH；总控台从 PowerShell 启动命令。")

    if "missing script" in log_lower and cwd:
        try:
            scripts = list((json.loads((Path(cwd) / "package.json").read_text(
                encoding="utf-8")).get("scripts") or {}).keys())
        except (OSError, ValueError):
            scripts = []
        hint = ("package.json 里可用的脚本：%s。" % "、".join(scripts)
                if scripts else "package.json 里没有 scripts。")
        add("npm-script", "npm 脚本名写错了", "日志报 missing script。%s" % hint,
            "把启动命令改成上面列出的脚本名，例如 npm run %s。" % (scripts[0] if scripts else "dev"))

    if ("eaddrinuse" in log_lower or "address already in use" in log_lower
            or "只允许使用一次" in (log_tail or "") or "10048" in log_lower
            or "eacces" in log_lower and "port" in log_lower):
        add("port-busy", "端口被占用",
            "日志报地址已占用（EADDRINUSE / WinError 10048）。",
            "在服务监控里查这个端口是谁占用的，停掉它或换个端口。")

    if ("permission denied" in log_lower or "eacces" in log_lower
            or "access is denied" in log_lower or "拒绝访问" in (log_tail or "")):
        add("perm", "权限不足",
            "日志报权限不足（EACCES / Access is denied）。",
            "检查文件/目录权限；不要用管理员账户混跑开发服务。")

    m = re.search(r"modulenotfounderror: no module named '([^']+)'", log_lower)
    if m:
        add("py-deps", "缺少 Python 包 %s" % m.group(1),
            "日志报 ModuleNotFoundError。",
            "在项目目录执行：pip install %s" % m.group(1),
            "install-deps")

    if code == 9009:
        add("runtime-missing", "命令不存在（exit 9009）",
            "cmd 以 9009 退出表示找不到可执行文件。",
            "检查命令拼写与 PATH。")

    if code not in (None, 0) and not issues:
        add("generic", "非零退出（码 %s）" % code,
            "命令以退出码 %s 结束，日志末尾没有匹配到已知错误模式。" % code,
            "打开日志查看最后的报错行。", "open-logs")

    return {"issues": issues}


def install_deps_hint(unit):
    """deps 缺失时前端「一键修复」按钮要展示的终端命令。"""
    cwd = unit.get("workdir") or ""
    if not cwd or not (Path(cwd) / "package.json").is_file():
        return None
    mgr = ("yarn" if (Path(cwd) / "yarn.lock").is_file()
           else "pnpm" if (Path(cwd) / "pnpm-lock.yaml").is_file()
           else "npm")
    # 总控台用 Windows PowerShell 5.x 执行命令，不支持 && 分隔符，用 ;
    return "Set-Location -LiteralPath \"%s\"; %s install" % (cwd, mgr)
