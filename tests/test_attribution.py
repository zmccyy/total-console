import unittest

from core import attribution


def table(entries):
    """[(pid, ppid, name, cmdline)] → 溯源表。"""
    return {pid: (ppid, name, cmd) for pid, ppid, name, cmd in entries}


class AttributionTest(unittest.TestCase):
    def test_run_token_marker_identifies_console(self):
        t = table([
            (10, 5, "node.exe", "node server.js"),
            (5, 1, "powershell.exe", "powershell -Command npm run dev\n# console-run:abc123"),
        ])
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "总控台", "icon": "rocket"})

    def test_agent_assistant_in_ancestor_cmdline(self):
        t = table([
            (10, 5, "node.exe", "node server.js"),
            (5, 1, "node.exe", "node C:\\Users\\x\\claude\\cli.js --task"),
            (1, 0, "wininit.exe", ""),
        ])
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "Claude Code", "icon": "bot"})

    def test_editor_process_name(self):
        t = table([
            (10, 5, "node.exe", "vite"),
            (5, 1, "Code.exe", "C:\\Program Files\\Microsoft VS Code\\Code.exe"),
            (1, 0, "userinit.exe", ""),
        ])
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "VS Code", "icon": "code"})

    def test_terminal_by_name(self):
        t = table([
            (10, 5, "npm.cmd", "npm run dev"),
            (5, 1, "WindowsTerminal.exe", "wt.exe"),
            (1, 0, "userinit.exe", ""),
        ])
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "Windows Terminal", "icon": "terminal"})

    def test_unknown_wrapper_falls_back_to_nearest_meaningful_ancestor(self):
        t = table([
            (10, 5, "python.exe", "python app.py"),
            (5, 1, "svchost.exe", "svchost"),
            (1, 0, "services.exe", ""),
        ])
        # svchost 不在跳过表也不是已知应用 → 以最近未识别层命名
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "svchost", "icon": "package"})

    def test_skip_chain_reaches_editor(self):
        t = table([
            (10, 5, "node.exe", "vite"),
            (5, 3, "cmd.exe", "cmd /c npm run dev"),
            (3, 1, "Cursor.exe", "Cursor"),
            (1, 0, "userinit.exe", ""),
        ])
        self.assertEqual(attribution.attribute_origin(10, t),
                         {"label": "Cursor", "icon": "code"})

    def test_missing_table_returns_none(self):
        self.assertIsNone(attribution.attribute_origin(999, {}))

    def test_base_name_normalization(self):
        self.assertEqual(attribution.base_name("C:\\x\\Code.exe"), "code")
        self.assertEqual(attribution.base_name(""), "")


if __name__ == "__main__":
    unittest.main()
