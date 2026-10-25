"""使用合成账号、临时数据库与模拟论坛验证个人会话及权限边界。"""
import hashlib
import hmac
import http.client
from http.cookies import SimpleCookie
import json
import re
import sqlite3
import threading
import urllib.parse
import urllib.error

import pytest

from wpy import auth, lake_actions, server
from wpy.board_api import BoardDB
from wpy.board_auth import Sessions
from wpy.db import open_db
from wpy.ui import build_html


class Client:
    def __init__(self, port):
        self.port = port
        self.cookies = {}
        self.csrf = ""

    def request(self, path, data=None, *, method=None, headers=None):
        headers = dict(headers or {})
        headers.setdefault("Cookie", "; ".join(k + "=" + v for k, v in self.cookies.items()))
        if self.csrf:
            headers.setdefault("X-WPY-CSRF", self.csrf)
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method or ("POST" if data is not None else "GET"), path, body, headers)
            response = conn.getresponse()
            payload = response.read().decode()
            response_headers = response.getheaders()
            for name, value in response_headers:
                if name.lower() == "set-cookie":
                    for key, morsel in SimpleCookie(value).items():
                        if morsel["max-age"] == "0":
                            self.cookies.pop(key, None)
                        else:
                            self.cookies[key] = morsel.value
            if "application/json" in response.getheader("Content-Type", ""):
                payload = json.loads(payload)
            return response.status, payload, response_headers
        finally:
            conn.close()

    def login(self, name="test-alice", password="test-password"):
        status, page, _ = self.request("/login")
        assert status == 200
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        result = self.request("/login", {"account": name, "password": password, "csrf": csrf})
        if result[0] == 302:
            self.csrf = self.request("/api/me")[1]["csrf"]
        return result


@pytest.fixture()
def site(tmp_path, monkeypatch):
    db_path = tmp_path / "sitewide.db"
    conn = open_db(str(db_path))
    for pid in range(1, 4):
        conn.execute('INSERT INTO posts (id, "标题", "板块ID", "是我的帖子", "是我的收藏") '
                     'VALUES (?, ?, 2, ?, ?)', (pid, "test fixture " + str(pid), int(pid == 3), int(pid == 3)))
    conn.commit()
    conn.close()
    html_path = tmp_path / "board.html"
    build_html(str(html_path))
    monkeypatch.setattr(server, "HTML_PATH", str(html_path))
    monkeypatch.setattr(server, "DB_PATH", str(db_path))
    monkeypatch.setattr(server, "GUEST_BLOCK_UIDS", [])
    monkeypatch.setattr(server, "_login_fails", {})
    monkeypatch.setattr(lake_actions, "_pace", lambda: None)
    monkeypatch.setenv("WPY_BOARD_TOKEN", "test_legacy_shared_password")
    monkeypatch.setenv("WPY_ACCOUNT", "test-crawler")
    monkeypatch.setenv("WPY_PASSWORD", "test_crawler_password")
    accounts = {"test-alice": "test_alice_token", "test-bob": "test_bob_token"}
    collections = {"test_alice_token": {"mine": {1}, "fav": {2}},
                   "test_bob_token": {"mine": {2}, "fav": {1}}}
    calls = []

    def login(account, password):
        if account not in accounts or password != "test-password":
            raise RuntimeError("test error must not appear in response")
        return accounts[account]

    def remote(url, token=None, method="GET", headers=None, body=None):
        token = token or (headers or {}).get("token")
        assert token in collections, "must only use a visitor's token"
        calls.append((token, url, method, body))
        path = urllib.parse.urlparse(url).path
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if path.endswith("posts/user") or path.endswith("posts/fav"):
            kind = "mine" if path.endswith("posts/user") else "fav"
            ids = sorted(collections[token][kind])
            page = int(query["page"][0])
            size = int(query["page_size"][0])
            return {"code": 200, "data": {"list": [{"id": i} for i in ids[(page-1)*size:page*size]], "total": len(ids)}}
        if path.endswith("post/fav"):
            form = urllib.parse.parse_qs(body)
            pid = int(form["post_id"][0])
            if form["op"] == ["1"]:
                collections[token]["fav"].add(pid)
            else:
                collections[token]["fav"].discard(pid)
        return {"code": 200, "data": {"list": []}}

    monkeypatch.setattr(auth, "login", login)
    monkeypatch.setattr(lake_actions, "_request_json_safe", remote)
    board = BoardDB(str(db_path))
    sessions = Sessions(tmp_path)
    handler = type("TestHandler", (server._Handler,), {"board": board, "sessions": sessions})
    with server.Server(("127.0.0.1", 0), handler) as httpd:
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            yield httpd.server_address[1], sessions, collections, calls, db_path
        finally:
            httpd.shutdown()
            worker.join(timeout=3)
            board.conn.close()


def test_guest_browses_and_cannot_write_even_with_legacy_credentials(site):
    port, _, _, calls, _ = site
    guest = Client(port)
    assert guest.request("/")[0] == 200
    assert guest.request("/api/me")[1] == {"role": "guest", "csrf": ""}
    assert guest.request("/login")[0] == 200
    for cookie in ({}, {"wpy_token": "test_legacy_shared_password"},
                   {"wpy_session": "admin." + hmac.new(b"test_legacy_shared_password", b"admin", hashlib.sha256).hexdigest()[:32]}):
        guest.cookies = cookie
        assert guest.request("/api/me?token=test_legacy_shared_password")[1]["role"] == "guest"
        for action in ("fav", "like", "floor_like", "reply", "floor_reply", "post"):
            assert guest.request("/api/wpy/" + action, {"post_id": "1"})[0] == 401
        assert guest.request("/api/follow", {"uid": "8"})[0] == 401
    assert not calls


def test_two_accounts_have_distinct_personal_lists_and_follows(site):
    port, _, _, _, db_path = site
    alice, bob, guest = Client(port), Client(port), Client(port)
    assert alice.login()[0] == bob.login("test-bob")[0] == 302
    for client, mine, fav in ((alice, 1, 2), (bob, 2, 1)):
        assert client.request("/api/me")[1]["role"] == "user"
        assert [p["id"] for p in client.request("/api/posts?mine=1")[1]["items"]] == [mine]
        assert [p["id"] for p in client.request("/api/posts?fav=1")[1]["items"]] == [fav]
        assert client.request("/api/post?id=" + str(fav))[1]["fav"] == 1
        assert client.request("/api/post?id=3")[1]["mine"] == 0
        assert client.request("/api/post?id=3")[1]["fav"] == 0
    assert alice.request("/api/follow", {"uid": "8", "name": "test alias"})[0] == 200
    assert bob.request("/api/follow")[1]["follows"] == {}
    again = Client(port)
    assert again.login()[0] == 302
    assert again.request("/api/follow")[1]["follows"] == {"8": "test alias"}
    assert guest.request("/api/posts?fav=1")[1]["items"] == []
    assert guest.request("/api/posts?mine=1")[1]["items"] == []
    assert all(not p["mine"] and not p["fav"] for p in guest.request("/api/posts")[1]["items"])
    assert [p["id"] for p in guest.request("/api/posts?favIds=2")[1]["items"]] == [2]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT id FROM posts WHERE "是我的收藏"=1').fetchall() == [(3,)]


@pytest.mark.parametrize("action,payload", [
    ("fav", {"post_id": "1", "op": "1"}),
    ("like", {"post_id": "1", "op": "1"}),
    ("floor_like", {"floor_id": "1", "op": "1"}),
    ("reply", {"post_id": "1", "content": "test comment", "images": ""}),
    ("floor_reply", {"reply_to_floor": "1", "content": "test reply", "images": ""}),
    ("post", {"type": "2", "title": "test title", "content": "test body"}),
])
def test_forum_writes_use_the_current_account(site, action, payload):
    port, _, _, calls, _ = site
    alice, bob = Client(port), Client(port)
    alice.login()
    bob.login("test-bob")
    for client, token in ((alice, "test_alice_token"), (bob, "test_bob_token")):
        assert client.request("/api/wpy/" + action, payload)[1]["ok"] == 1
        assert calls[-1][0] == token


def test_favorite_write_does_not_change_crawler_or_other_account_flags(site):
    port, _, _, _, db_path = site
    alice, bob = Client(port), Client(port)
    alice.login()
    bob.login("test-bob")
    assert alice.request("/api/wpy/fav", {"post_id": "3", "op": "1"})[0] == 200
    assert [p["id"] for p in alice.request("/api/posts?fav=1")[1]["items"]] == [3, 2]
    assert [p["id"] for p in bob.request("/api/posts?fav=1")[1]["items"]] == [1]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT id FROM posts WHERE "是我的收藏"=1').fetchall() == [(3,)]


def test_csrf_logout_and_session_replay(site):
    port, sessions, _, calls, _ = site
    client = Client(port)
    client.login()
    sid = client.cookies["wpy_user_session"]
    for headers in ({"X-WPY-CSRF": ""}, {"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.request("/api/wpy/like", {"post_id": "1"}, headers=headers)[0] == 403
    assert not calls
    assert client.request("/logout")[0] == 405
    assert client.request("/logout", {})[1]["ok"] == 1
    assert sessions.get(sid) is None
    client.cookies["wpy_user_session"] = sid
    assert client.request("/api/me")[1]["role"] == "guest"
    assert client.request("/api/wpy/like", {"post_id": "1"})[0] == 401


def test_expired_forum_session_never_falls_back_to_crawler(site, monkeypatch):
    port, _, _, _, _ = site
    client = Client(port)
    client.login()
    monkeypatch.setattr(lake_actions, "_request_json_safe", lambda *a, **kw: {"code": 401})
    assert client.request("/api/wpy/like", {"post_id": "1"})[0] == 401
    assert client.request("/api/me")[1]["role"] == "guest"


def test_login_errors_csrf_and_no_password_storage(site):
    port, sessions, _, _, db_path = site
    client = Client(port)
    status, page, _ = client.request("/login", {"account": "test-alice", "password": "test-password"})
    assert status == 403
    assert "test-password" not in page
    status, page, _ = client.login(password="test_wrong_password")
    assert status == 401
    assert "test error" not in page and "test_wrong_password" not in page
    assert client.login()[0] == 302
    assert not hasattr(sessions.get(client.cookies["wpy_user_session"]), "password")
    for path in db_path.parent.rglob("*"):
        if path.is_file():
            assert b"test-password" not in path.read_bytes()
            assert b"test_alice_token" not in path.read_bytes()
            assert b"test-alice" not in path.read_bytes()


def test_cookies_secure_host_only_and_login_needs_https_for_site_host(site):
    port, _, _, _, _ = site
    client = Client(port)
    _, _, headers = client.request("/login", headers={"X-Forwarded-Proto": "https"})
    cookie = dict(headers)["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Domain=" not in cookie
    csrf = client.cookies["wpy_login_csrf"]
    assert client.request("/login", {"account": "test-alice", "password": "test-password", "csrf": csrf},
                          headers={"Host": "board.example"})[0] == 403


def test_personal_collection_paginates_without_truncation(site):
    port, _, collections, _, _ = site
    collections["test_alice_token"]["fav"] = set(range(1, 123))
    client = Client(port)
    client.login()
    assert client.request("/api/posts?fav=1")[1]["total"] == 3


def test_forum_outage_keeps_archive_browsing_but_not_incorrect_personal_filters(site, monkeypatch):
    port, _, _, _, _ = site
    client = Client(port)
    client.login()
    monkeypatch.setattr(lake_actions, "_request_json_safe", lambda *a, **kw: {"code": 503})
    status, data, _ = client.request("/api/posts")
    assert status == 200 and data["total"] == 3 and data["personalPending"]
    assert all(not p["mine"] and not p["fav"] and p["personalPending"] for p in data["items"])
    assert client.request("/api/posts?fav=1")[0] == 502
    assert client.request("/api/posts?mine=1")[0] == 502
    assert client.request("/api/post?id=1")[0] == 200


def test_incomplete_personal_response_is_not_cached_as_a_complete_list(site, monkeypatch):
    port, sessions, _, _, _ = site
    client = Client(port)
    client.login()
    monkeypatch.setattr(lake_actions, "_request_json_safe", lambda *a, **kw:
                        {"code": 200, "data": {"list": [{"id": 1}], "total": 3}})
    assert client.request("/api/posts?fav=1")[0] == 502
    assert sessions.get(client.cookies["wpy_user_session"]).personal is None


def test_bad_login_throttle_survives_failed_attempts(site):
    port, _, _, _, _ = site
    client = Client(port)
    for _ in range(8):
        assert client.login(password="test_wrong_password")[0] == 401
    assert client.login()[0] == 429


def test_same_origin_login_with_null_origin_still_requires_form_csrf(site):
    port, _, _, _, _ = site
    client = Client(port)
    client.request("/login")
    data = {"account": "test-alice", "password": "test-password",
            "csrf": client.cookies["wpy_login_csrf"]}
    assert client.request("/login", data, headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"})[0] == 403
    data["csrf"] = client.cookies["wpy_login_csrf"]
    assert client.request("/login", data, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})[0] == 302


def test_unavailable_login_service_does_not_count_as_bad_password(site, monkeypatch):
    port, _, _, _, _ = site
    def unavailable(*args):
        raise auth.FatalError("test configuration unavailable")
    monkeypatch.setattr(auth, "login", unavailable)
    client = Client(port)
    for _ in range(9):
        assert client.login()[0] == 503
    assert not server._login_fails.get("127.0.0.1")


@pytest.mark.parametrize("upstream,status,failures", [(401, 401, 1), (403, 401, 1), (429, 429, 0), (503, 503, 0)])
def test_login_http_errors_are_classified_without_exposing_upstream_details(site, monkeypatch, upstream, status, failures):
    port, _, _, _, _ = site
    def failed(*args):
        raise urllib.error.HTTPError("https://login.example", upstream, "test private detail", {}, None)
    monkeypatch.setattr(auth, "login", failed)
    code, page, _ = Client(port).login()
    assert code == status
    assert "test private detail" not in page
    assert len(server._login_fails.get("127.0.0.1", [])) == failures


def test_hot_proxy_does_not_inject_shared_credentials(monkeypatch):
    from wpy import hot_server
    requests = []
    class Response:
        headers = {"Content-Type": "application/json"}
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return b'{"role":"guest"}'
    def urlopen(request, **kwargs):
        requests.append(request)
        return Response()
    monkeypatch.setattr(hot_server.urllib.request, "urlopen", urlopen)
    handler = object.__new__(hot_server._Handler)
    handler.headers = {"Cookie": "wpy_user_session=test_session"}
    handler.token = "test_legacy_password"
    handler._send = lambda *args: args
    handler._proxy_api("/api/me", "")
    assert requests[0].get_header("Cookie") == "wpy_user_session=test_session"


def test_sessions_expire_and_do_not_survive_restart(tmp_path):
    now = [100.0]
    sessions = Sessions(tmp_path, clock=lambda: now[0], ttl=10)
    sid, session = sessions.create("test-alice", "test_alice_token")
    assert sessions.get(sid) is session
    other = Sessions(tmp_path)
    assert other.get(sid) is None
    _, again = other.create("test-alice", "test_other_token")
    assert again.principal == session.principal
    now[0] = 111
    assert sessions.get(sid) is None
