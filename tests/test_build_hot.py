"""热榜生成使用临时数据库，未配置时不包含默认访问令牌。"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


@pytest.mark.parametrize("token", [None, 'test-"token</script>'])
def test_hot_page_has_only_explicit_token(tmp_path, token):
    data_dir = tmp_path / "data"
    build_dir = tmp_path / "build"
    data_dir.mkdir()
    build_dir.mkdir()
    with sqlite3.connect(data_dir / "sitewide.db") as conn:
        conn.execute('''CREATE TABLE posts (
            id INTEGER PRIMARY KEY, 标题 TEXT, 作者 TEXT, 分管部门 TEXT,
            板块ID INTEGER, 点赞数 INTEGER, 评论数 INTEGER, 收藏数 INTEGER,
            浏览量 INTEGER, 发布时间 TEXT, 正文 TEXT
        )''')
    (build_dir / "全站看板.html").write_text("<script></script>\n</body>", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("WPY_")}
    env.update({
        "WPY_DATA_DIR": str(data_dir),
        "WPY_BUILD_DIR": str(build_dir),
        "WPY_LOG_DIR": str(tmp_path / "logs"),
        "WPY_PAGES_DIR": str(tmp_path / "pages"),
    })
    if token is not None:
        env["WPY_HOT_TOKEN"] = token
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_hot.py"
    subprocess.run([sys.executable, str(script)], env=env, check=True, capture_output=True, text=True)

    html = (build_dir / "hot.html").read_text(encoding="utf-8")
    token_line = next(line for line in html.splitlines() if line.startswith("const TOKEN = "))
    assert json.loads(token_line[len("const TOKEN = "):-1]) == (token or "")
    assert "</script>" not in token_line
