"""应用元信息：版本与身份标识（供服务端、API 层共同引用，避免循环导入）。"""
from pathlib import Path

VERSION = "0.3.0"
APP_MARKER = "total-console"
DEFAULT_PORT = 9600
PORT_TRIES = 10
ROOT_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT_DIR / "registry" / "agent-tools.json"
STATIC_DIR = ROOT_DIR / "static"
