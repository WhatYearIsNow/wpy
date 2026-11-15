"""The standalone preview process exposes only post-reading routes."""

import sqlite3

import pytest

from wpy.board_api import BoardDB
from wpy.preview_server import allowed_preview_path


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
