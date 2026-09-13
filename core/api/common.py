"""API 处理的请求上下文与响应工具。"""
import json


class ApiContext:
    """一次请求的上下文：包装 handler，供各域处理函数使用。

    字段：h=BaseHTTPRequestHandler、app=_OpsApp、port、params=路径参数、
    query=解析后的查询串。
    """

    def __init__(self, handler, app, port, params=None, query=None):
        self.h = handler
        self.app = app
        self.port = port
        self.params = params or {}
        self.query = query or {}

    # ----- 请求 -----

    def read_json(self):
        """读取 JSON body；缺失或格式错误返回 None。"""
        try:
            length = int(self.h.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0:
            return None
        try:
            return json.loads(self.h.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ----- 响应 -----

    def send_json(self, status, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.h.send_response(status)
        self.h.send_header("Content-Type", "application/json; charset=utf-8")
        self.h.send_header("Content-Length", str(len(payload)))
        self.h.send_header("Cache-Control", "no-store")
        self.h.end_headers()
        self.h.wfile.write(payload)

    def send_text(self, status, text):
        payload = text.encode("utf-8")
        self.h.send_response(status)
        self.h.send_header("Content-Type", "text/plain; charset=utf-8")
        self.h.send_header("Content-Length", str(len(payload)))
        self.h.send_header("Cache-Control", "no-store")
        self.h.end_headers()
        self.h.wfile.write(payload)

    def send_raw(self, status, ctype, payload, cache="no-store"):
        """logs 等非 JSON 文本响应。"""
        self.h.send_response(status)
        self.h.send_header("Content-Type", ctype)
        self.h.send_header("Content-Length", str(len(payload)))
        self.h.send_header("Cache-Control", cache)
        self.h.end_headers()
        self.h.wfile.write(payload)
