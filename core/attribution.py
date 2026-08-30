"""进程溯源：沿父进程链识别「是谁启动了这个服务」。

移植自 local-ops 的 attribute_origin，Windows 适配：以 psutil 父链 +
进程名/命令行签名替代 macOS 的 ps/lsof 与 .app 包路径识别。结果只是
展示用的尽力判断（{"label", "icon"} 或 None），不影响任何启停逻辑。
"""
import os
import re

RUN_TOKEN_ARG_PREFIX = "console-run:"

# 向上爬时要跳过的包装层（进程基名，小写、无扩展名）：
# 壳、终端宿主、包管理器与任务执行器
ORIGIN_SKIP_NAMES = {
    "cmd", "conhost", "openconsole", "powershell", "pwsh", "wsl", "bash",
    "sh", "dash", "fish", "nu", "winpty",
    "npm", "npx", "pnpm", "yarn", "corepack", "make", "just", "cmake",
    "node", "tsx", "nodemon", "ts-node", "deno", "bun", "bunx",
    "python", "python3", "py", "uv", "uvx", "poetry", "pip", "pipx",
    "ruby", "php", "java", "javaw", "dotnet", "go", "cargo", "rustc",
    "sudo", "env", "command", "xargs", "nohup", "nice", "timeout",
}

# 已知 AI 编程助手签名（在祖先进程名+命令行中做词边界匹配，按顺序取先命中者）
ORIGIN_AGENT_PATTERNS = (
    (re.compile(r"\bcodex\b", re.I), "Codex"),
    (re.compile(r"claude-code|\bclaude\b", re.I), "Claude Code"),
    (re.compile(r"\bkimi\b", re.I), "Kimi"),
    (re.compile(r"\bgemini\b", re.I), "Gemini"),
    (re.compile(r"\baider\b", re.I), "Aider"),
    (re.compile(r"\bopencode\b", re.I), "OpenCode"),
    (re.compile(r"\bgoose\b", re.I), "Goose"),
    (re.compile(r"\bcursor-agent\b", re.I), "Cursor"),
    (re.compile(r"\bcopilot\b", re.I), "Copilot"),
    (re.compile(r"\bqwen\b", re.I), "Qwen"),
    (re.compile(r"\bqoder\b", re.I), "Qoder"),
    (re.compile(r"\bamp\b", re.I), "Amp"),
    (re.compile(r"\bcodebuddy\b", re.I), "CodeBuddy"),
    (re.compile(r"\biflow\b", re.I), "iFlow"),
)

# 进程基名 → (展示名, 图标)。编辑器与独立终端
ORIGIN_APP_NAMES = {
    "code": ("VS Code", "code"),
    "codetunnel": ("VS Code", "code"),
    "cursor": ("Cursor", "code"),
    "trae": ("Trae", "code"),
    "windsurf": ("Windsurf", "code"),
    "zed": ("Zed", "code"),
    "sublime_text": ("Sublime", "code"),
    "webstorm": ("WebStorm", "code"),
    "webstorm64": ("WebStorm", "code"),
    "idea": ("IDEA", "code"),
    "idea64": ("IDEA", "code"),
    "goland": ("GoLand", "code"),
    "goland64": ("GoLand", "code"),
    "pycharm": ("PyCharm", "code"),
    "pycharm64": ("PyCharm", "code"),
    "devenv": ("Visual Studio", "code"),
    "windowsterminal": ("Windows Terminal", "terminal"),
    "wt": ("Windows Terminal", "terminal"),
    "alacritty": ("Alacritty", "terminal"),
    "wezterm": ("WezTerm", "terminal"),
    "wezterm-gui": ("WezTerm", "terminal"),
    "mintty": ("mintty", "terminal"),
    "hyper": ("Hyper", "terminal"),
    "tabby": ("Tabby", "terminal"),
    "terminus": ("Tabby", "terminal"),
    "konsole": ("Konsole", "terminal"),
    "docker": ("Docker", "package"),
    "ollama": ("Ollama", "package"),
    "obsidian": ("Obsidian", "package"),
}

# 终端复用器（直接以进程名命名，不进跳过表）
ORIGIN_MULTIPLEXERS = {"tmux": "tmux", "screen": "screen"}

_MAX_DEPTH = 12


def base_name(name):
    """'C:\\...\\Code.exe' → 'code'（小写、无扩展名）。"""
    if not name:
        return ""
    return os.path.splitext(os.path.basename(name))[0].lower().lstrip("-")


def attribute_origin(pid, table):
    """沿父进程链识别来源应用。

    table: {pid: (ppid, name, cmdline)}，由调用方按需构建/缓存。
    祖先命令行带有总控台 run-token 前缀（console-run:）即判定为
    「总控台启动」。未识别的中间层先记为候选并继续上爬；AI 助手 /
    编辑器 / 终端 / 总控台是更优答案，都没有时才以最近的未识别
    进程命名。最多上爬 12 层，遇到环或缺失即终止。
    """
    cur, seen, candidate = pid, set(), None
    for _ in range(_MAX_DEPTH):
        entry = table.get(cur)
        if not entry:
            break
        ppid, pname, pcmd = entry
        if ppid in seen:
            break
        seen.add(ppid)
        parent = table.get(ppid)
        if not parent:
            return candidate or {"label": "系统", "icon": "server"}
        p_ppid, p_name, p_cmd = parent
        hay = "%s %s" % (p_name or "", p_cmd or "")
        if RUN_TOKEN_ARG_PREFIX in (p_cmd or ""):
            return {"label": "总控台", "icon": "rocket"}
        for pattern, label in ORIGIN_AGENT_PATTERNS:
            if pattern.search(hay):
                return {"label": label, "icon": "bot"}
        base = base_name(p_name)
        if base in ORIGIN_APP_NAMES:
            label, icon = ORIGIN_APP_NAMES[base]
            return {"label": label, "icon": icon}
        if base in ORIGIN_MULTIPLEXERS:
            return {"label": ORIGIN_MULTIPLEXERS[base], "icon": "terminal"}
        if base and base not in ORIGIN_SKIP_NAMES and candidate is None:
            candidate = {"label": base, "icon": "package"}
        cur = ppid
    return candidate


def build_ancestry_table(pid, process_factory=None):
    """从 pid 向上收集 {pid: (ppid, name, cmdline)}，供 attribute_origin 使用。"""
    import psutil

    make = process_factory or (lambda p: psutil.Process(p))
    table = {}
    cur = pid
    for _ in range(_MAX_DEPTH + 1):
        if cur in table:
            break
        try:
            p = make(cur)
            with p.oneshot():
                table[cur] = (p.ppid(), p.name() or "", " ".join(p.cmdline() or []))
                ppid = table[cur][0]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            break
        if not ppid or ppid == cur:
            break
        cur = ppid
    return table
