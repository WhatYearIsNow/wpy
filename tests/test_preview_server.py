"""The standalone preview process exposes only post-reading routes."""

import sqlite3
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from wpy.board_api import BoardDB
from wpy.preview_server import allowed_preview_path, _PostPreviewHandler
from wpy.server import Server


@pytest.mark.parametrize(
    "path",
    [
        "/post/1",
        "/post/123456789/",
        "/api/me",
        "/api/post",
        "/api/user",
        "/api/users",
        "/img/thumb/example.jpg",
        "/healthz",
    ],
)
def test_preview_allows_detail_support_paths(path):
    assert allowed_preview_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/hot",
        "/api/posts",
        "/api/wpy/post",
        "/post/0",
        "/post/nope",
        "/post/123/extra",
    ],
)
def test_preview_rejects_board_list_and_write_paths(path):
    assert not allowed_preview_path(path)


def test_preview_database_connection_is_read_only(tmp_path):
    path = tmp_path / "sitewide.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    board = BoardDB(path, read_only=True)
    try:
        assert board.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            board.conn.execute("CREATE TABLE should_not_exist (id INTEGER)")
    finally:
        board.conn.close()


def test_independent_preview_http_serves_full_post_and_rejects_writes(tmp_path):
    from wpy.db import open_db

    path = tmp_path / "archive.db"
    connection = open_db(str(path))
    content = "Synthetic full post body. " * 600
    connection.execute(
        'INSERT INTO posts (id, "标题", "正文", "板块ID") VALUES (1, ?, ?, 2)',
        ("Synthetic preview", content),
    )
    connection.execute(
        'INSERT INTO floors (id, "帖子ID", "昵称", "内容") VALUES (10, 1, ?, ?)',
        ("Synthetic reader", "Synthetic reply"),
    )
    connection.commit()
    connection.close()
    board = BoardDB(path, read_only=True)
    handler = type("PreviewTestHandler", (_PostPreviewHandler,), {"board": board})
    server = Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/post/1", timeout=2) as response:
            assert '<base href="../">' in response.read().decode()
        with urlopen(base + "/post/1/", timeout=2) as response:
            assert '<base href="../../">' in response.read().decode()
        with urlopen(base + "/api/post?id=1", timeout=2) as response:
            post = json.load(response)
        assert post["c"] == content
        assert post["floors"][0]["c"] == "Synthetic reply"
        with urlopen(base + "/api/me", timeout=2) as response:
            assert json.load(response) == {"role": "guest"}
        for path in ("/", "/hot", "/api/posts", "/api/board-ranking"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + path, timeout=2)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/wpy/post", data=b"title=test"), timeout=2)
        assert error.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        board.conn.close()
