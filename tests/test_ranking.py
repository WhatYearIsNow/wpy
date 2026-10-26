"""验证热榜排行查询（含全站榜 board=all）与 /api/board-ranking 端点。

全程使用临时数据库与本地 HTTP，不触碰部署数据。
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from wpy.ranking import RankingError, query_ranking
from wpy import ranking as ranking_mod


def _seed(db_path, rows):
    from wpy.db import open_db

    conn = open_db(str(db_path))
    for r in rows:
        conn.execute(
            'INSERT INTO posts (id, "标题", "板块ID", "作者", "发布时间",'
            ' "浏览量", "点赞数", "评论数", "收藏数")'
            " VALUES (:id, :标题, :板块ID, :作者, :发布时间,"
            " :浏览量, :点赞数, :评论数, :收藏数)",
            r,
        )
    conn.commit()
    conn.close()


def _posts():
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    days7 = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 40 * 86400))
    ancient = "2021-11-05T12:40:00"
    return [
        dict(
            id=1,
            标题="今天热帖",
            板块ID=2,
            作者="甲",
            发布时间=now,
            浏览量=900,
            点赞数=10,
            评论数=1,
            收藏数=2,
        ),
        dict(
            id=2,
            标题="三天前热帖",
            板块ID=3,
            作者="乙",
            发布时间=days7,
            浏览量=5000,
            点赞数=99,
            评论数=5,
            收藏数=6,
        ),
        dict(
            id=3,
            标题="四十天前帖子",
            板块ID=2,
            作者="丙",
            发布时间=old,
            浏览量=500,
            点赞数=7,
            评论数=0,
            收藏数=0,
        ),
        dict(
            id=4,
            标题="远古名帖",
            板块ID=4,
            作者="丁",
            发布时间=ancient,
            浏览量=47000,
            点赞数=800,
            评论数=300,
            收藏数=100,
        ),
    ]


@pytest.fixture()
def seeded_db(tmp_path):
    db_path = tmp_path / "sitewide.db"
    _seed(db_path, _posts())
    return db_path


def test_query_all_boards_orders_by_dim(seeded_db):
    name, rows = query_ranking(seeded_db, board="all", dim="view", window="all")
    assert name == "全站"
    assert [r["id"] for r in rows] == [4, 2, 1, 3]


def test_query_board_filter(seeded_db):
    _, rows = query_ranking(seeded_db, board=2, dim="view", window="all")
    assert [r["id"] for r in rows] == [1, 3]
    assert all(r["板块ID"] == 2 for r in rows)


def test_query_window_day_excludes_old(seeded_db):
    _, rows = query_ranking(seeded_db, board="all", dim="view", window="day")
    assert [r["id"] for r in rows] == [1]


def test_query_dim_like(seeded_db):
    _, rows = query_ranking(seeded_db, board="all", dim="like", window="all")
    assert [r["id"] for r in rows] == [4, 2, 1, 3]


def test_query_limit(seeded_db):
    _, rows = query_ranking(seeded_db, board="all", dim="view", window="all", limit=2)
    assert len(rows) == 2
    # 超大 limit 钳到 200
    _, rows = query_ranking(seeded_db, board="all", dim="view", window="all", limit=10**9)
    assert len(rows) == 4


def test_query_invalid_params(seeded_db):
    with pytest.raises(RankingError):
        query_ranking(seeded_db, board="all", dim="bogus")
    with pytest.raises(RankingError):
        query_ranking(seeded_db, board="abc")
    # 缺省 board 等价于全站
    name, rows = query_ranking(seeded_db)
    assert name == "全站" and len(rows) == 4


def test_board_map_names_kept():
    # 原有分区名不变，热榜页分区展示不受影响
    assert ranking_mod.BOARD_MAP[2] == "青年湖底"
    assert ranking_mod.BOARD_MAP[13] == "美食专区"


@pytest.mark.parametrize(
    "qs,want",
    [
        (
            "board=all&dim=view&window=all&limit=2",
            {"board": "all", "boardName": "全站", "ids": [4, 2]},
        ),
        ("board=2", {"board": "2", "boardName": "青年湖底", "ids": [1, 3]}),
        ("", {"board": "all", "boardName": "全站", "ids": [4, 2, 1, 3]}),
    ],
)
def test_board_ranking_endpoint(tmp_path, qs, want):
    env = {key: value for key, value in os.environ.items() if not key.startswith("WPY_")}
    for name in ("DATA", "BUILD", "LOG", "PAGES"):
        directory = tmp_path / name.lower()
        directory.mkdir()
        env[f"WPY_{name}_DIR"] = str(directory)
    db_path = tmp_path / "data" / "sitewide.db"
    _seed(db_path, _posts())

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
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail("server exited: " + proc.communicate()[0])
            try:
                assert json.loads(opener.open(base + "/healthz", timeout=1).read())["ok"]
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.05)
        else:
            pytest.fail("local server did not become ready")

        url = base + "/api/board-ranking" + (("?" + qs) if qs else "")
        with opener.open(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["board"] == want["board"]
        assert payload["boardName"] == want["boardName"]
        assert [r["id"] for r in payload["data"]] == want["ids"]
        assert all("标题" in r and "浏览量" in r for r in payload["data"])

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            opener.open(base + "/api/board-ranking?board=2&dim=bogus", timeout=3)
        assert exc_info.value.code == 400
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 4
