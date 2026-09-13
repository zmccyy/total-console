"""外部工具集成：在终端 / 编辑器中打开单元工作目录。

只「打开」不执行命令，程序均按白名单探测（wt / cmd / code / explorer），
找不到高级工具时降级到资源管理器，不让功能静默失效。
"""
import shutil
import subprocess
from pathlib import Path

# 不显示新窗口本身的控制台闪屏（子工具自会开窗）
CREATE_NO_WINDOW = 0x08000000


class OpenError(Exception):
    pass


def open_terminal(workdir):
    """在 workdir 打开终端：优先 Windows Terminal，回退 cmd。"""
    wd = _resolve(workdir)
    wt = shutil.which("wt")
    if wt:
        args = ["cmd", "/c", wt, "-d", wd] if wd else ["cmd", "/c", wt]
        subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
        return {"tool": "wt"}
    if wd:
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/K", "cd", "/d", wd],
                         creationflags=CREATE_NO_WINDOW)
    else:
        subprocess.Popen(["cmd", "/c", "start", "", "cmd"],
                         creationflags=CREATE_NO_WINDOW)
    return {"tool": "cmd"}


def open_editor(workdir):
    """用编辑器打开 workdir：优先 VS Code，回退资源管理器。"""
    wd = _resolve(workdir)
    code = shutil.which("code")
    tool = "code"
    if not code:
        code = shutil.which("code-insiders")
        tool = "code-insiders"
    if code:
        subprocess.Popen(["cmd", "/c", code, wd], creationflags=CREATE_NO_WINDOW)
        return {"tool": tool}
    subprocess.Popen(["cmd", "/c", "start", "", wd], creationflags=CREATE_NO_WINDOW)
    return {"tool": "explorer"}


def _resolve(workdir):
    """把单元工作目录解析为存在的绝对路径；为空时回退用户主目录，
    不存在时明确报错（前端提示修正 workdir）。"""
    wd = str(workdir or "").strip() or str(Path.home())
    path = Path(wd)
    if not path.is_dir():
        raise OpenError("工作目录不存在: %s" % wd)
    return str(path.resolve())
