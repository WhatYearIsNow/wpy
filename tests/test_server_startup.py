"""验证新安装启动与已有数据浏览，全程使用临时数据库和本地 HTTP。"""

import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


@pytest.mark.parametrize("database_state", ["missing", "empty_file", "populated"])
def test_serve_with_local_database(tmp_path, database_state):
    existing_post = database_state == "populated"
    env = {key: value for key, value in os.environ.items() if not key.startswith("WPY_")}
    for name in ("DATA", "BUILD", "LOG", "PAGES"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[f"WPY_{name}_DIR"] = str(directory)
    db_path = tmp_path / "data" / "sitewide.db"
    if database_state == "empty_file":
        db_path.touch()
    if existing_post:
        from wpy.db import open_db

        conn = open_db(str(db_path))
        conn.execute(
            'INSERT INTO posts (id, "标题", "板块ID") VALUES (?, ?, ?)', (1, "temporary fixture", 2)
        )
        conn.commit()
        conn.close()

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "wpy",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-open",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with opener.open(base + path, timeout=1) as response:
            return response.read().decode("utf-8")

    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail("server exited: " + proc.communicate()[0])
            try:
                assert json.loads(get("/healthz"))["ok"]
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.05)
        else:
            pytest.fail("local server did not become ready")

        assert "青年湖底" in get("/")
        assert json.loads(get("/api/init"))["total"] == int(existing_post)
        posts = json.loads(get("/api/posts?size=5"))["items"]
        assert len(posts) == int(existing_post)
        if existing_post:
            assert posts[0]["id"] == 1
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == int(existing_post)
