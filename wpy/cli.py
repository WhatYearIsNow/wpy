# -*- coding: utf-8 -*-
"""命令行入口：python -m wpy <cmd> 或 python crawl.py <cmd>。"""

import sys


def _ensure_utf8_stdio():
    """Windows 控制台可能是 cp1252/gbk，打印中文日志前尽量切到 UTF-8。

    已是 UTF-8/CP65001 则不动；不支持 reconfigure 的流忽略失败，
    避免把编码问题变成启动崩溃。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = (getattr(stream, "encoding", None) or "").lower()
            if enc in ("utf-8", "utf8", "cp65001"):
                continue
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv=None):
    _ensure_utf8_stdio()
    # 启动时配置验证（不阻塞，只打印警告）
    from .config_models import validate_all, log_warnings

    log_warnings(validate_all())
    # 延迟导入，--help 更快
    from . import crawler

    return crawler.main(argv)


if __name__ == "__main__":
    sys.exit(main() or 0)
