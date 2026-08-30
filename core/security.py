ALLOWED_HOST_NAMES = {"127.0.0.1", "localhost"}


def _strip_port(value):
    if value.endswith("]"):  # [::1] 形式不支持，直接拒绝
        return None, None
    name, sep, port = value.rpartition(":")
    return (name, port) if sep else (value, None)


def is_host_allowed(host_header, port):
    """Host 头必须为 127.0.0.1:port 或 localhost:port（DNS rebinding 防线）。"""
    if not host_header:
        return False
    name, port_part = _strip_port(host_header.strip())
    if port_part is None:
        return False
    return name in ALLOWED_HOST_NAMES and port_part == str(port)


def is_origin_allowed(origin, port):
    """写请求的 Origin 必须与站点同源（浏览器同源策略校验的补齐）。"""
    if not origin:
        return False
    if origin == "null":
        return False
    if not (origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")):
        return False
    return origin.endswith(":%d" % port)
