# -*- coding: utf-8 -*-
"""验证 _ensure_utf8_stdio：先把 stdout/stderr reconfigure 成 cp1252，再调用修复函数。

用法（在 wpy-sitewide 目录）：
    python tools/test_serve_utf8.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _force_cp1252():
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="cp1252", errors="strict")
            else:
                # 理论路径：不支持 reconfigure 时无法伪造，交给后续断言失败
                pass
        except Exception:
            pass


def main():
    from wpy.cli import _ensure_utf8_stdio
    from wpy.server import serve

    _force_cp1252()
    before = (getattr(sys.stdout, "encoding", "") or "").lower()
    print("BEFORE", before, flush=True)
    if "1252" not in before and before not in ("cp1252",):
        # 若平台无法切到 cp1252，至少仍要验证 reconfigure 到 utf-8 的路径
        print("WARN: cannot force cp1252 on this stdio; testing reconfigure path")

    _ensure_utf8_stdio()
    after = (getattr(sys.stdout, "encoding", "") or "").lower()
    print("AFTER", after, flush=True)
    if after not in ("utf-8", "utf8", "cp65001"):
        print("FAIL: encoding not restored to utf-8")
        return 1

    # 中文横幅：旧逻辑在 cp1252 下会 UnicodeEncodeError
    th = threading.Thread(
        target=serve,
        kwargs={"host": "127.0.0.1", "port": 8788, "open_browser": False, "token": ""},
        daemon=True,
    )
    th.start()
    time.sleep(1.5)
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8788/healthz", timeout=5)
        body = r.read()
        print("healthz", r.status, body, flush=True)
        if r.status != 200 or b'"ok"' not in body:
            print("FAIL: healthz")
            return 1
    except Exception as e:
        print("FAIL: serve/thread", e)
        return 1
    print("serve_utf8_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
