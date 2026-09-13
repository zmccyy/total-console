import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

APP_DIR_NAME = "总控台"
CONFIG_FILENAME = "config.json"

# 配置 schema 版本：v1 = 早期（units + settings，无版本号）；
# v2 = 复刻 local-ops：补 run_token/排序/端口基线/通知等字段与备份机制。
CURRENT_SCHEMA_VERSION = 2

DEFAULT_SCAN_ROOT = str(Path.home())
DEFAULT_POLL_INTERVAL = 2  # 对齐上游「每 2 秒」节奏


def _defaults() -> dict:
    """每次调用返回全新结构，避免默认值的共享可变对象被调用方污染。"""
    return {
        "schemaVersion": CURRENT_SCHEMA_VERSION,
        "units": [],
        "port_baseline": {},
        "settings": {
            "scan_roots": [DEFAULT_SCAN_ROOT],
            "poll_interval": DEFAULT_POLL_INTERVAL,
            "theme": "system",
            "ignored_ports": [],
            "notifications": True,
            "watch_keywords": [],
            "hidden_procs": [],
        },
    }


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def config_path(data_dir_path=None) -> Path:
    d = Path(data_dir_path) if data_dir_path else data_dir()
    return d / CONFIG_FILENAME


def backup_path(data_dir_path=None) -> Path:
    return config_path(data_dir_path).with_suffix(".json.bak")


def logs_dir(data_dir_path=None) -> Path:
    d = Path(data_dir_path) if data_dir_path else data_dir()
    return d / "logs"


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _migrate(raw: dict) -> dict:
    """把磁盘上的旧配置补齐到当前 schema。未知键原样保留，不丢数据。"""
    try:
        version = int(raw.get("schemaVersion", 0))
    except (TypeError, ValueError):
        version = 0
    if version > CURRENT_SCHEMA_VERSION:
        # 程序被降级：仍按当前规则读取，但保留原版本号，避免写坏新数据
        print("警告：配置 schemaVersion=%d 新于当前程序支持的 %d"
              % (version, CURRENT_SCHEMA_VERSION))
        return raw
    return raw


def load_config(data_dir_path=None) -> dict:
    path = config_path(data_dir_path)
    if not path.exists():
        return _defaults()
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _defaults()
    if not isinstance(raw, dict):
        return _defaults()
    raw = _migrate(raw)
    cfg = _merge(_defaults(), raw)
    # 迁移：早期 schema 把 scan_roots 放在顶层，现在统一收进 settings（新 schema 不再产生顶层键）
    if "scan_roots" in cfg:
        cfg.setdefault("settings", {})["scan_roots"] = cfg.pop("scan_roots")
    return cfg


# 配置读写锁：进程内所有「改配置 + 落盘」的临界区共用（RLock 允许嵌套，
# 如 Launchpad.start持锁后调用 save_config）。快照循环、请求线程、
# 盘点线程都可能触碰 config，避免 json.dump 序列化时结构被并发修改。
CONFIG_LOCK = threading.RLock()


def save_config(data_dir_path=None, config: dict = None):
    if config is None:
        config = {}
    d = Path(data_dir_path) if data_dir_path else data_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / CONFIG_FILENAME
    with CONFIG_LOCK:
        # 写前留一份上一版配置，误操作可手动回滚
        if path.exists():
            try:
                shutil.copy2(path, backup_path(data_dir_path))
            except OSError:
                pass
        fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            _replace_with_retry(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def _replace_with_retry(src, dst, attempts=3):
    """Windows 上目标文件可能被并发读者/杀软短暂占用，重试几次。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))
