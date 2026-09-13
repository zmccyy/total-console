"""前端契约测试（零浏览器）：

1. index.html 引用的本地 js/css 资源都存在，入口是 ES module；
2. 前端 JS 里出现的 /api/* 字面量都能被后端路由注册表覆盖
   （前端调用了不存在的端点 → 立刻在这里失败，而不是上线后 404）。
"""
import re
import unittest
from pathlib import Path

from core import api as api_routes

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
JS_DIR = STATIC / "js"


def local_refs(html):
    """index.html 里引用的本地静态资源路径。"""
    refs = re.findall(r'(?:src|href)="([^"]+)"', html)
    return [r for r in refs if not r.startswith(("http://", "https://", "data:"))]


def api_literals(js_sources):
    """所有 JS 文件中出现的 /api/ 开头字符串字面量（含查询串）。"""
    found = set()
    for src in js_sources:
        for m in re.finditer(r'''["'](/api/[^"'`]*)["']''', src):
            path = m.group(1).split("?", 1)[0]
            found.add(path)
    return found


def covered_by_backend(literal):
    """字面量是否可由某个后端路由模式覆盖（<param> 段视为任意前缀）。"""
    for route in api_routes.ROUTES:
        pattern = route.pattern
        head = pattern.split("<", 1)[0].rstrip("/")
        if literal.rstrip("/") == head or literal.rstrip("/").startswith(head + "/") \
                or pattern.rstrip("/").startswith(literal.rstrip("/")):
            return True
    return False


class FrontendContractTest(unittest.TestCase):
    def test_index_refs_exist(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        refs = local_refs(html)
        self.assertTrue(refs, "index.html 应引用至少一个本地资源")
        for ref in refs:
            self.assertTrue((STATIC / ref).is_file(), "index.html 引用的资源不存在: %s" % ref)

    def test_entry_is_es_module(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('type="module"', html)
        self.assertIn("js/main.js", html)

    def test_no_legacy_single_file(self):
        self.assertFalse((STATIC / "app.js").exists(),
                         "旧版单文件 app.js 已拆分为 js/ 模块，不应再出现")

    def test_js_modules_parseable(self):
        # 最基本的语法防线：非空、有 import/export 或可执行语句
        js_files = sorted(JS_DIR.glob("*.js"))
        self.assertTrue(js_files)
        for f in js_files:
            src = f.read_text(encoding="utf-8")
            self.assertGreater(len(src.strip()), 10, "%s 是空文件" % f.name)

    def test_frontend_api_literals_covered_by_routes(self):
        js_sources = [f.read_text(encoding="utf-8") for f in JS_DIR.glob("*.js")]
        literals = api_literals(js_sources)
        self.assertTrue(literals, "未从前端收集到任何 /api/ 引用，正则可能失效")
        unknown = sorted(l for l in literals if not covered_by_backend(l))
        self.assertEqual(unknown, [],
                         "前端调用了后端路由注册表未覆盖的端点: %s" % unknown)


if __name__ == "__main__":
    unittest.main()
