"""用合成消息验证个人通知，不访问论坛或真实邮箱。"""
from email.message import Message
from io import BytesIO
import json
import os
import smtplib
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from wpy import http, notification_cli as cli
from wpy import notification_mail as mail
from wpy.notifications import (
    AuthenticationError, ForumClient, NotificationStore, RateLimited, normalize, run_cycle, worker_lock,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("不应访问网络"))
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *a, **kw: pytest.fail("不应发送真实邮件"))
    for key in ("WPY_ACCOUNT", "WPY_PASSWORD", "WPY_SMTP_CONFIG", "WPY_NOTIFY_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def message(source="floors", kind_type=0, target=11, likes=2, **extra):
    row = {"type": kind_type,
           "post": {"id": 101, "title": "示例帖子", "like_count": likes},
           "floor": {"id": target, "post_id": 101, "nickname": "示例用户", "content": "示例评论",
                     "like_count": likes, "created_at": "2026-01-01T12:00:00"},
           "to_floor": {"id": 10, "content": "示例原评论"}, "is_read": False}
    row.update(extra)
    return row


def event(source="floors", kind_type=0, target=11, likes=2):
    return normalize(source, message(source, kind_type, target, likes))


@pytest.fixture
def store(tmp_path):
    with worker_lock(tmp_path / "state"):
        instance = NotificationStore(tmp_path / "state", "test-account", "reader@example.invalid")
        yield instance
        instance.close()


@pytest.mark.parametrize("source,kind_type,expected", [
    ("likes", 0, "post_like"), ("likes", 1, "floor_like"),
    ("floors", 0, "post_comment"), ("floors", 1, "comment_reply"),
])
def test_official_message_type_mapping(source, kind_type, expected):
    result = event(source, kind_type)
    assert result["kind"] == expected
    assert result["post_id"] == 101
    if expected == "comment_reply":
        assert result["original"] == "示例原评论"


@pytest.mark.parametrize("patch", [{"type": 3}, {"type": True}, {"type": 1.2}, {"floor": {}}])
def test_malformed_messages_do_not_silently_disappear(patch):
    with pytest.raises(mail.NotificationError, match="字段"):
        normalize("floors", message(**patch))


def test_deleted_original_and_image_only_reply_are_renderable():
    row = message(kind_type=1, to_floor=None)
    row["floor"].update(content="", image_url="test-image")
    result = normalize("floors", row)
    assert result["content"] == "[图片评论]"
    assert result["original"] == ""


def test_first_run_baselines_without_sending_and_later_changes_are_grouped(store):
    snapshot = [event("likes", 0, likes=2), event("likes", 1, likes=2), event()]
    sent = []
    assert store.ingest(snapshot) == {"baseline": True, "new": 0}
    assert store.deliver(lambda *args: sent.append(args))["sent"] == 0
    changed = [event("likes", 0, likes=3), event("likes", 1, likes=1), event(),
               event(kind_type=1, target=12)]
    assert store.ingest(changed)["new"] == 2
    assert store.deliver(lambda *args: sent.append(args)) == {"sent": 1, "failed": 0, "pending": 0}
    assert "评论" in sent[0][1] and "本轮观察增加 1" in sent[0][1]
    assert store.ingest(changed)["new"] == 0
    assert store.deliver(lambda *args: pytest.fail("不应重复发信"))["sent"] == 0


def test_like_decrease_then_increase_is_a_new_observation(store):
    store.ingest([event("likes", likes=3)])
    assert store.ingest([event("likes", likes=2)])["new"] == 0
    assert store.ingest([event("likes", likes=3)])["new"] == 1


def test_first_observation_of_like_target_does_not_claim_cumulative_count_is_new(store):
    store.ingest([])
    store.ingest([event("likes", likes=17)])
    sent = []
    assert store.deliver(lambda *args: sent.append(args))["sent"] == 1
    assert "当前累计 17" in sent[0][1]
    assert "新增数量未知" in sent[0][1]
    assert "增加 17" not in sent[0][1]


def test_state_persists_and_cannot_be_reused_for_another_identity(tmp_path):
    directory = tmp_path / "state"
    with worker_lock(directory):
        first = NotificationStore(directory, "test-account", "reader@example.invalid")
        first.ingest([])
        first.ingest([event()])
        first.close()
        second = NotificationStore(directory, "test-account", "reader@example.invalid")
        assert second.ingest([event()])["new"] == 0
        assert second.deliver(lambda *args: True)["sent"] == 1
        second.close()
        for account, recipient in [("other-test-account", "reader@example.invalid"),
                                   ("test-account", "other@example.invalid")]:
            with pytest.raises(mail.NotificationError, match="另一账号或收件人"):
                NotificationStore(directory, account, recipient)
    if os.name != "nt":
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "notifications.sqlite3").stat().st_mode & 0o777 == 0o600


def test_ingest_and_queue_are_atomic_on_render_failure(store):
    store.ingest([])
    with pytest.raises(mail.NotificationError):
        store.ingest([event()], "javascript:alert(1)")
    assert store.ingest([event()])["new"] == 1


def test_failures_remain_pending_and_retry_without_refetch_or_duplicate_queue(store):
    clock = [1000]
    store.now = lambda: clock[0]
    store.ingest([])
    store.ingest([event()])
    attempts = []

    def failing(*args):
        attempts.append(args)
        raise RuntimeError("test private SMTP response")

    assert store.deliver(failing) == {"sent": 0, "failed": 1, "pending": 1}
    assert store.ingest([event()])["new"] == 0
    assert store.deliver(failing)["failed"] == 0  # 未到退避时间，不再次发送。
    clock[0] += 300
    assert store.deliver(lambda *args: attempts.append(args))["sent"] == 1
    assert attempts[0][3] == attempts[1][3]  # 补发保持同一 Message-ID。
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


def test_false_sender_result_is_a_failure(store):
    store.ingest([])
    store.ingest([event()])
    assert store.deliver(lambda *a: False)["pending"] == 1


def test_two_workers_cannot_send_from_same_state_directory(tmp_path):
    with worker_lock(tmp_path):
        with pytest.raises(mail.NotificationError, match="重复启动"):
            with worker_lock(tmp_path):
                pytest.fail("锁未生效")


def test_pagination_reads_all_pages_and_only_gets_message_endpoints():
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        query = parse_qs(urlsplit(url).query)
        page = int(query["page"][0])
        if "/floors?" in url:
            rows = [message(target=i) for i in range(1, 21)] if page == 1 else [message(target=21)]
        else:
            rows = []
        return {"code": 200, "data": {"list": rows}}

    result = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None).snapshot()
    assert len(result) == 21
    assert len(calls) == 3
    assert all("/message/" in url and "/read" not in url for url, _ in calls)
    assert all(kwargs["token"] == "test-token" and kwargs["max_bytes"] > 0 for _, kwargs in calls)


def test_partial_snapshot_never_initializes_baseline(store):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if "/likes?" in url:
            return {"code": 200, "data": {"list": []}}
        raise TimeoutError("test private network detail")

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    with pytest.raises(mail.NotificationError, match="基线"):
        run_cycle(client, store, lambda *a: True)
    assert not store.db.execute("SELECT * FROM meta WHERE key='initialized'").fetchone()


@pytest.mark.parametrize("max_pages,repeat", [(1, False), (3, True)])
def test_truncated_or_repeated_pagination_is_not_committed(store, max_pages, repeat):
    def request(url, **kwargs):
        return {"code": 200, "data": {"list": [message("likes", 1, i) for i in range(1, 21)]}}

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None, max_pages=max_pages)
    with pytest.raises(mail.NotificationError, match="重复|上限"):
        run_cycle(client, store, lambda *a: True)
    assert not store.db.execute("SELECT * FROM meta WHERE key='initialized'").fetchone()


@pytest.mark.parametrize("as_http", [False, True])
def test_token_expiry_reauthenticates_once_and_reuses_new_token(as_http):
    tokens = iter(["test-old-token", "test-new-token"])
    calls = []

    def request(url, **kwargs):
        calls.append(kwargs["token"])
        if len(calls) == 1:
            if as_http:
                raise HTTPError(url, 401, "test", {}, BytesIO())
            return {"code": 401}
        return {"code": 200, "data": {"list": []}}

    client = ForumClient(lambda: next(tokens), request=request, sleep=lambda _: None)
    assert client.snapshot() == []
    assert calls == ["test-old-token", "test-new-token", "test-new-token"]


@pytest.mark.parametrize("code,expected_calls", [(401, 2), (403, 1)])
def test_auth_failure_does_not_retry_forever(code, expected_calls):
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return {"code": code}

    client = ForumClient(lambda: "test-token", request=request)
    with pytest.raises(AuthenticationError):
        client.snapshot()
    assert len(calls) == expected_calls


def test_rate_limit_preserves_server_wait():
    def request(*args, **kwargs):
        raise http.Throttled("test", retry_after=1800)

    with pytest.raises(RateLimited) as caught:
        ForumClient(lambda: "test-token", request=request).snapshot()
    assert caught.value.delay == 1800


def test_pending_email_can_send_while_forum_is_unavailable(store):
    store.ingest([])
    store.ingest([event()])
    sent = []

    class BrokenClient:
        def snapshot(self):
            raise mail.NotificationError("test forum unavailable")

    with pytest.raises(mail.NotificationError):
        run_cycle(BrokenClient(), store, lambda *a: sent.append(a))
    assert len(sent) == 1
    assert store.deliver(lambda *a: pytest.fail("不应重复"))["pending"] == 0


def test_html_is_escaped_and_mail_has_no_remote_images():
    item = dict(event(kind_type=1), title='<script>alert("test")</script>', content='<img src="test">',
                original="<b>原评论</b>", increase=0)
    subject, text, body = mail.render_digest([item], "https://board.example.invalid/", test=True)
    assert "测试" in subject and "合成内容" in text
    assert "<script>" not in body and "<img " not in body
    assert "&lt;script&gt;" in body and "&lt;b&gt;原评论" in body
    assert 'href="https://board.example.invalid/?postId=101"' in body
    assert "wpytoken" not in body


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://test:secret@example.invalid/",
                                 "https://example.invalid/?token=test", "https://example.invalid/#test"])
def test_unsafe_mail_links_are_rejected(url):
    with pytest.raises(mail.NotificationError):
        mail.validate_base_url(url)


def config_file(tmp_path):
    path = tmp_path / "smtp.json"
    path.write_text(json.dumps({"user": "sender@example.invalid", "pass": "test-code",
                                "to": "reader@example.invalid"}))
    return path


def test_course_config_loads_without_exposing_credentials_in_repr(tmp_path):
    config = mail.MailConfig.load(config_file(tmp_path))
    assert config.host == "smtp.qq.com" and config.port == 465
    assert "test-code" not in repr(config) and "sender@" not in repr(config)


@pytest.mark.parametrize("value", [[], None, {"user": "bad\r\nheader", "pass": "test"}])
def test_invalid_smtp_config_is_reported_without_its_content(tmp_path, value):
    path = tmp_path / "smtp.json"
    path.write_text(json.dumps(value))
    with pytest.raises(mail.NotificationError) as caught:
        mail.MailConfig.load(path)
    assert "bad" not in str(caught.value)


def test_real_send_constructs_multipart_and_treats_quit_failure_as_accepted(tmp_path, monkeypatch):
    captured = []

    class FakeSMTP:
        def __init__(self, host, port, **kwargs):
            assert kwargs["timeout"] == 30 and kwargs["context"].check_hostname
        def __enter__(self):
            return self
        def login(self, user, password):
            assert password == "test-code"
        def send_message(self, message):
            captured.append(message)
            return {}
        def __exit__(self, *args):
            raise smtplib.SMTPServerDisconnected("test quit failure")

    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    config = mail.MailConfig.load(config_file(tmp_path))
    mail.send_mail(config, *mail.render_digest(mail.test_events(), test=True), "<test-id@wpy.local>")
    assert captured[0]["Message-ID"] == "<test-id@wpy.local>"
    assert captured[0].get_body(preferencelist=("html",)) is not None
    assert captured[0].get_body(preferencelist=("plain",)) is not None


def test_smtp_errors_do_not_expose_recipient_or_response(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"test private detail")

    monkeypatch.setattr("smtplib.SMTP_SSL", fail)
    with pytest.raises(mail.NotificationError) as caught:
        mail.send_mail(mail.MailConfig.load(config_file(tmp_path)), "test", "text", "html", "<test@wpy.local>")
    assert "private detail" not in str(caught.value)


def parse(*argv):
    from wpy.crawler.cli_app import build_parser
    return build_parser()[0].parse_args(["notify", *argv])


def test_cli_test_email_needs_no_forum_login_or_state(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "send_mail", lambda *args: calls.append(args))
    directory = tmp_path / "unused-state"
    args = parse("--test-email", "--smtp-config", str(config_file(tmp_path)), "--state-dir", str(directory))
    assert cli.main(args) == 0
    assert len(calls) == 1 and "测试" in calls[0][1]
    assert not directory.exists()
    assert "收件箱" in capsys.readouterr().out


def test_cli_requires_explicit_personal_identity(tmp_path, capsys):
    assert cli.main(parse("--smtp-config", str(config_file(tmp_path)))) == 1
    assert "WPY_ACCOUNT" in capsys.readouterr().out
    assert parse().interval == 300
    assert cli.main(parse("--interval", "299")) == 1


def test_watch_default_interval_and_backoff():
    waits = []
    failures = iter([mail.NotificationError("test unavailable"), RateLimited(1800), None])

    class Client:
        def snapshot(self):
            error = next(failures)
            if error:
                raise error
            return []

    class Store:
        def deliver(self, sender):
            return {"sent": 0, "failed": 0, "pending": 0}
        def ingest(self, snapshot, base_url):
            return {"baseline": False, "new": 0}

    class Stop:
        def is_set(self):
            return len(waits) == 3
        def wait(self, value):
            waits.append(value)

    cli.watch(Client(), Store(), None, Stop(), report=lambda _: None)
    assert waits == [300, 1800, 300]


def test_json_response_limit_and_retry_after(monkeypatch):
    class Response(BytesIO):
        status = 200

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(b'{}' * 10))
    with pytest.raises(ValueError, match="too large"):
        http.request_json("https://example.invalid/", throttle=False, max_bytes=5)
    headers = Message()
    headers["Retry-After"] = "1800"

    def limited(*args, **kwargs):
        raise HTTPError("https://example.invalid/", 429, "test", headers, BytesIO())

    monkeypatch.setattr("urllib.request.urlopen", limited)
    with pytest.raises(http.Throttled) as caught:
        http.request_json("https://example.invalid/", throttle=False)
    assert caught.value.retry_after == 1800
