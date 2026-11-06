"""用合成消息验证个人通知，不访问论坛或真实邮箱。"""

from email.message import Message
from io import BytesIO
import json
import os
import smtplib
import sqlite3
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from wpy import http, notification_cli as cli
from wpy import notification_mail as mail
from wpy.notifications import (
    AuthenticationError,
    ForumClient,
    NotificationStore,
    RateLimited,
    agent_event_keys,
    agent_owned_content,
    crawler_floor_chain,
    normalize,
    non_interactive_event_keys,
    run_cycle,
    worker_lock,
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("不应访问网络"))
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *a, **kw: pytest.fail("不应发送真实邮件"))
    for key in ("WPY_ACCOUNT", "WPY_PASSWORD", "WPY_SMTP_CONFIG", "WPY_NOTIFY_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


def message(source="floors", kind_type=0, target=11, likes=2, **extra):
    row = {
        "type": kind_type,
        "post": {"id": 101, "title": "示例帖子", "content": "示例帖子正文", "like_count": likes},
        "floor": {
            "id": target,
            "post_id": 101,
            "nickname": "示例用户",
            "content": "示例评论",
            "like_count": likes,
            "created_at": "2026-01-01T12:00:00",
        },
        "to_floor": {"id": 10, "content": "示例原评论", "like_count": 3},
        "is_read": False,
    }
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


@pytest.mark.parametrize(
    "source,kind_type,expected",
    [
        ("likes", 0, "post_like"),
        ("likes", 1, "floor_like"),
        ("floors", 0, "post_comment"),
        ("floors", 1, "comment_reply"),
    ],
)
def test_official_message_type_mapping(source, kind_type, expected):
    result = event(source, kind_type)
    assert result["kind"] == expected
    assert result["post_id"] == 101
    assert result["post_content"] == "示例帖子正文"
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
    changed = [
        event("likes", 0, likes=3),
        event("likes", 1, likes=1),
        event(),
        event(kind_type=1, target=12),
    ]
    assert store.ingest(changed)["new"] == 2
    assert store.deliver(lambda *args: sent.append(args)) == {"sent": 1, "failed": 0, "pending": 0}
    assert "评论" in sent[0][1] and "本轮观察增加 1" in sent[0][1]
    assert store.ingest(changed)["new"] == 0
    assert store.deliver(lambda *args: pytest.fail("不应重复发信"))["sent"] == 0


def test_big_fish_owned_content_is_seen_but_not_sent_as_personal_notification(store, tmp_path):
    agent_db = tmp_path / "agent.sqlite3"
    connection = sqlite3.connect(agent_db)
    connection.execute(
        "CREATE TABLE own_content (entity TEXT, remote_id INTEGER, at REAL, "
        "PRIMARY KEY (entity,remote_id))"
    )
    connection.executemany("INSERT INTO own_content VALUES (?,?,0)", [("post", 101), ("floor", 10)])
    connection.commit()
    connection.close()
    posts, floors = agent_owned_content(agent_db)
    ai_reply = event(kind_type=1, target=12)
    human_reply = event(kind_type=1, target=13)
    human_reply["my_comment_id"] = 999
    excluded = agent_event_keys([ai_reply, human_reply], posts, floors)
    store.ingest([])
    assert store.ingest([ai_reply, human_reply], excluded=excluded)["new"] == 1
    sent = []
    assert store.deliver(lambda *args: sent.append(args))["sent"] == 1
    assert sent[0][1].count("通知类型：") == 1
    assert store.ingest([ai_reply, human_reply], excluded=excluded)["new"] == 0


def test_all_big_fish_post_and_comment_interactions_are_excluded():
    events = [
        event("likes", 0, target=20),
        event("floors", 0, target=21),
        event("likes", 1, target=10),
        event("floors", 1, target=22),
    ]
    excluded = agent_event_keys(events, {101}, {10})
    assert excluded == {(item["kind"], item["target_id"]) for item in events}


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


def test_more_than_one_hundred_notifications_in_one_window_are_one_email(store):
    store.ingest([])
    events = [event(target=i) for i in range(1, 104)]
    events += [event("likes", 1, target=200, likes=3), event(kind_type=1, target=201)]
    assert store.ingest(events)["new"] == 105
    captured = []
    assert store.deliver(lambda *args: captured.append(args))["sent"] == 1
    assert len(captured) == 1
    assert captured[0][0] == "微北洋通知｜有人点赞了你的评论；有人回复了你的帖子、评论"
    assert captured[0][1].count("通知类型：") == 105
    assert store.ingest(events)["new"] == 0


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
        for account, recipient in [
            ("other-test-account", "reader@example.invalid"),
            ("test-account", "other@example.invalid"),
        ]:
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

    client = ForumClient(
        lambda: "test-token", request=request, sleep=lambda _: None, max_pages=max_pages
    )
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
        def snapshot(self, **kwargs):
            raise mail.NotificationError("test forum unavailable")

    with pytest.raises(mail.NotificationError):
        run_cycle(BrokenClient(), store, lambda *a: sent.append(a))
    assert len(sent) == 1
    assert store.deliver(lambda *a: pytest.fail("不应重复"))["pending"] == 0


def test_html_is_escaped_and_mail_has_no_remote_images():
    item = dict(
        event(kind_type=1),
        title='<script>alert("test")</script>',
        content='<img src="test">',
        original="<b>原评论</b>",
        my_comment="<b>原评论</b>",
        increase=0,
    )
    subject, text, body = mail.render_digest([item], "https://board.example.invalid/", test=True)
    assert "测试" in subject and "合成内容" in text
    assert "<script>" not in body and "<img " not in body
    assert "&lt;script&gt;" in body and "&lt;b&gt;原评论" in body
    assert 'href="https://board.example.invalid/?postId=101"' in body
    assert "wpytoken" not in body


@pytest.mark.parametrize("index", range(4))
def test_standard_template_contains_post_context_and_specific_interaction(index):
    item = mail.test_events()[index]
    subject, text, body = mail.render_digest([item])
    for label in ("帖子编号", "帖子标题", "帖子正文", "帖子点赞"):
        assert label in text and label in body
    assert "邮件类型：微北洋正常互动通知" in text
    assert subject == "微北洋通知｜" + mail.LABELS[item["kind"]]
    assert item["title"] in text and item["post_content"] in text
    if item["kind"] in ("floor_like", "comment_reply"):
        assert "我的评论" in text and item["my_comment"] in text
        assert "评论点赞：4 赞" in text
    if item["kind"] in ("post_comment", "comment_reply"):
        assert f"回复人：{item['author']}" in text
        assert f"回复详情：{item['content']}" in text
        assert f"回复时间：{item['time']}" in text


def test_long_context_is_explicitly_marked_as_excerpt():
    row = message()
    row["post"]["content"] = "示例正文" * 3000
    item = normalize("floors", row)
    assert "已节选" in item["post_content"]
    assert len(item["post_content"]) < 8100


def test_mixed_subject_lists_notification_types_without_project_prefix():
    subject = mail.notification_subject(mail.test_events())
    assert subject == "微北洋通知｜有人点赞了你的帖子、评论；有人回复了你的帖子、评论"
    assert "WPY" not in subject and "#MP" not in subject
    assert mail.notification_subject(mail.test_events(), test=True) == subject + "（测试）"


def test_enrichment_only_reads_new_context_once_per_post_and_floor(store):
    store.ingest([])
    first = event(kind_type=1, target=21)
    second = event(kind_type=1, target=22)
    for item in (first, second):
        item.update(post_content="", my_comment="", my_comment_likes=None)
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if "/post?" in url:
            return {
                "code": 200,
                "data": {
                    "post": {
                        "id": 101,
                        "title": "完整标题",
                        "content": "完整帖子正文",
                        "like_count": 9,
                    }
                },
            }
        assert "/floor?" in url
        return {
            "code": 200,
            "data": {"floor": {"id": 10, "content": "我的完整评论", "like_count": 5}},
        }

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    assert client.enrich(store.new_events([first, second])) == [first, second]
    assert len(calls) == 2
    assert first["post_content"] == "完整帖子正文"
    assert second["my_comment"] == "我的完整评论" and second["my_comment_likes"] == 5
    store.ingest([first, second])
    assert client.enrich(store.new_events([first, second])) == []
    assert len(calls) == 2


def test_missing_context_is_not_fetched_during_first_baseline(store):
    item = event()
    item["post_content"] = ""
    client = ForumClient(lambda: pytest.fail("首次基线不应补读上下文"))
    assert client.enrich(store.new_events([item])) == []
    assert store.ingest([item])["baseline"]


def test_deleted_post_context_is_reported_without_dropping_notification(store):
    store.ingest([])
    item = event()
    item["post_content"] = ""
    client = ForumClient(
        lambda: "test-token", request=lambda *a, **kw: {"code": 404}, sleep=lambda _: None
    )
    client.enrich(store.new_events([item]))
    assert "已删除" in item["post_content"]
    assert store.ingest([item])["new"] == 1


def test_failed_context_fetch_does_not_advance_message_baseline(store):
    store.ingest([])
    item = event()
    item["post_content"] = ""

    def request(*args, **kwargs):
        raise TimeoutError("test timeout")

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    with pytest.raises(mail.NotificationError):
        client.enrich(store.new_events([item]))
    assert store.new_events([item]) == [item]
    assert not store.db.execute("SELECT 1 FROM outbox").fetchone()


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://test:secret@example.invalid/",
        "https://example.invalid/?token=test",
        "https://example.invalid/#test",
    ],
)
def test_unsafe_mail_links_are_rejected(url):
    with pytest.raises(mail.NotificationError):
        mail.validate_base_url(url)


def config_file(tmp_path):
    path = tmp_path / "smtp.json"
    path.write_text(
        json.dumps(
            {"user": "sender@example.invalid", "pass": "test-code", "to": "reader@example.invalid"}
        )
    )
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
    mail.send_mail(
        config, *mail.render_digest(mail.test_events(), test=True), "<test-id@wpy.local>"
    )
    assert captured[0]["Message-ID"] == "<test-id@wpy.local>"
    assert captured[0]["From"].addresses[0].display_name == "微北洋"
    assert captured[0]["X-WPY-Mail-Category"] == "standard-notification"
    assert captured[0].get_body(preferencelist=("html",)) is not None
    assert captured[0].get_body(preferencelist=("plain",)) is not None


def test_smtp_errors_do_not_expose_recipient_or_response(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"test private detail")

    monkeypatch.setattr("smtplib.SMTP_SSL", fail)
    with pytest.raises(mail.NotificationError) as caught:
        mail.send_mail(
            mail.MailConfig.load(config_file(tmp_path)), "test", "text", "html", "<test@wpy.local>"
        )
    assert "private detail" not in str(caught.value)


def parse(*argv):
    from wpy.crawler.cli_app import build_parser

    return build_parser()[0].parse_args(["notify", *argv])


def test_cli_test_email_needs_no_forum_login_or_state(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "send_mail", lambda *args: calls.append(args))
    directory = tmp_path / "unused-state"
    args = parse(
        "--test-email", "--smtp-config", str(config_file(tmp_path)), "--state-dir", str(directory)
    )
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

        def enrich(self, events, *, learn_uid=False):
            return events

    class Store:
        def scan_options(self):
            return {}

        def self_uid(self):
            return None

        def new_events(self, events, *, excluded=frozenset()):
            return []

        def deliver(self, sender):
            return {"sent": 0, "failed": 0, "pending": 0}

        def ingest(self, snapshot, base_url, *, excluded=frozenset()):
            return {"baseline": False, "new": 0}

    class Stop:
        def is_set(self):
            return len(waits) == 3

        def wait(self, value):
            waits.append(value)

    cli.watch(Client(), Store(), None, Stop(), report=lambda _: None)
    assert waits == [300, 1800, 300]


def test_initial_baseline_uses_latest_floor_page_then_polls_only_new_ids(store):
    current = [[message(target=i) for i in range(100, 80, -1)]]
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if "/likes?" in url:
            return {"code": 200, "data": {"list": []}}
        if "/post?" in url:
            return {
                "code": 200,
                "data": {
                    "post": {
                        "id": 101,
                        "title": "示例帖子",
                        "content": "示例帖子正文",
                        "like_count": 2,
                        "uid": 74670,
                    }
                },
            }
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        assert page == 1, "不应扫描旧评论历史"
        return {"code": 200, "data": {"list": current[0], "total": 20}}

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    sent = []
    assert run_cycle(client, store, lambda *args: sent.append(args))["baseline"]
    assert len(calls) == 2 and not sent
    assert store.scan_options()["floor_cursor"] == 100
    current[0] = [message(target=101)] + current[0][:-1]
    result = run_cycle(client, store, lambda *args: sent.append(args))
    assert result["new"] == 1 and result["sent"] == 1
    assert store.scan_options()["floor_cursor"] == 101
    # 官方客户端读消息后，最新记录消失也不能让游标回退或补发旧消息。
    current[0] = [message(target=i) for i in range(100, 80, -1)]
    assert run_cycle(client, store, lambda *args: sent.append(args))["new"] == 0
    assert store.scan_options()["floor_cursor"] == 101 and len(sent) == 1


def test_incremental_floor_scan_loads_multiple_new_pages_then_stops_at_cursor(store):
    store.ingest([event(target=100)])
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if "/likes?" in url:
            return {"code": 200, "data": {"list": []}}
        if "/post?" in url:
            return {
                "code": 200,
                "data": {
                    "post": {
                        "id": 101,
                        "title": "示例帖子",
                        "content": "示例帖子正文",
                        "like_count": 2,
                        "uid": 74670,
                    }
                },
            }
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        top = 125 - (page - 1) * 20
        return {
            "code": 200,
            "data": {"list": [message(target=i) for i in range(top, top - 20, -1)]},
        }

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    result = run_cycle(client, store, lambda *args: True)
    # 3 次消息分页 + 1 次 UID 学习的帖子详情（同帖只读一次）。
    assert len(calls) == 4 and result["new"] == 25 and result["sent"] == 1
    assert any("/post?" in url for url in calls)
    assert store.scan_options()["floor_cursor"] == 125


def test_incomplete_incremental_scan_does_not_advance_cursor(store):
    store.ingest([event(target=100)])

    def request(url, **kwargs):
        if "/likes?" in url:
            return {"code": 200, "data": {"list": []}}
        if parse_qs(urlsplit(url).query)["page"] == ["1"]:
            return {"code": 200, "data": {"list": [message(target=i) for i in range(130, 110, -1)]}}
        raise TimeoutError("test timeout")

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    with pytest.raises(mail.NotificationError):
        run_cycle(client, store, lambda *args: True)
    assert store.scan_options()["floor_cursor"] == 100
    assert not store.db.execute("SELECT 1 FROM outbox").fetchone()


def test_changed_floor_sort_order_pauses_instead_of_skipping_records(store):
    store.ingest([event(target=100)])

    def request(url, **kwargs):
        rows = [] if "/likes?" in url else [message(target=101), message(target=103)]
        return {"code": 200, "data": {"list": rows}}

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    with pytest.raises(mail.NotificationError, match="顺序"):
        run_cycle(client, store, lambda *args: True)
    assert store.scan_options()["floor_cursor"] == 100


def test_json_response_limit_and_retry_after(monkeypatch):
    class Response(BytesIO):
        status = 200

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(b"{}" * 10))
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


def reply_row(target, parent_id):
    return message(
        kind_type=1,
        target=target,
        to_floor={
            "id": parent_id,
            "content": "原评论%d" % parent_id,
            "like_count": 1,
        },
    )


def test_reply_to_other_person_comment_is_recorded_but_not_emailed(store):
    # 学到的本人 UID=74670：回复自己评论的（父楼 10）发信，回复他人评论的（父楼 20）只记基线。
    store.remember_self_uid(74670)
    rounds = iter(
        [
            [],
            [reply_row(13, 20), reply_row(12, 10)],  # 接口按评论 ID 降序返回。
            [reply_row(13, 20), reply_row(12, 10)],
        ]
    )

    def request(url, **kwargs):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path.endswith("/message/likes"):
            return {"code": 200, "data": {"list": []}}
        if path.endswith("/message/floors"):
            rows = next(rounds)
            return {"code": 200, "data": {"list": rows}}
        if path.endswith("/floor"):
            floor_id = int(query["floor_id"][0])
            uid = 74670 if floor_id == 10 else 999
            return {
                "code": 200,
                "data": {
                    "floor": {
                        "id": floor_id,
                        "content": "原评论%d" % floor_id,
                        "like_count": 1,
                        "uid": uid,
                    }
                },
            }
        raise AssertionError("本轮不应请求 " + url)

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    assert run_cycle(client, store, lambda *args: True)["baseline"]
    sent = []
    result = run_cycle(client, store, lambda *args: sent.append(args))
    assert result["new"] == 1 and result["sent"] == 1
    assert "有人回复了你的评论" in sent[0][1]
    assert sent[0][1].count("通知类型：") == 1
    # 被过滤的回复仍进入去重基线，下轮不重复出现，游标正常推进。
    assert store.db.execute(
        "SELECT 1 FROM seen WHERE kind='comment_reply' AND target_id=13"
    ).fetchone()
    assert store.scan_options()["floor_cursor"] >= 13
    assert run_cycle(client, store, lambda *args: sent.append(args))["new"] == 0


def test_self_uid_is_learned_from_own_post_comment(store):
    def request(url, **kwargs):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if path.endswith("/message/likes"):
            return {"code": 200, "data": {"list": []}}
        if path.endswith("/message/floors"):
            return {"code": 200, "data": {"list": [message(target=31)]}}
        if path.endswith("/post"):
            assert int(query["id"][0]) == 101
            return {
                "code": 200,
                "data": {
                    "post": {
                        "id": 101,
                        "title": "示例帖子",
                        "content": "示例帖子正文",
                        "like_count": 2,
                        "uid": 74670,
                    }
                },
            }
        raise AssertionError("本轮不应请求 " + url)

    store.ingest([])  # 先建立基线，让下一条评论作为新互动出现。
    sent = []
    result = run_cycle(
        ForumClient(lambda: "test-token", request=request, sleep=lambda _: None),
        store,
        lambda *args: sent.append(args),
    )
    assert store.self_uid() == 74670
    assert result["new"] == 1 and result["sent"] == 1  # 自己帖子下的评论仍照常通知。


def test_before_uid_learning_replies_are_not_silently_dropped(store):
    rounds = iter(
        [
            [],
            [reply_row(13, 20), reply_row(12, 10)],
        ]
    )

    def request(url, **kwargs):
        path = urlsplit(url).path
        if path.endswith("/message/likes"):
            return {"code": 200, "data": {"list": []}}
        if path.endswith("/message/floors"):
            return {"code": 200, "data": {"list": next(rounds)}}
        if path.endswith("/floor"):
            floor_id = int(parse_qs(urlsplit(url).query)["floor_id"][0])
            return {
                "code": 200,
                "data": {
                    "floor": {
                        "id": floor_id,
                        "content": "原评论%d" % floor_id,
                        "like_count": 1,
                        "uid": 74670 if floor_id == 10 else 999,
                    }
                },
            }
        if path.endswith("/post"):
            raise AssertionError("未学到 UID 前不应请求帖子详情：" + url)
        raise AssertionError("本轮不应请求 " + url)

    client = ForumClient(lambda: "test-token", request=request, sleep=lambda _: None)
    assert run_cycle(client, store, lambda *args: True)["baseline"]
    sent = []
    result = run_cycle(client, store, lambda *args: sent.append(args))
    assert result["new"] == 2 and result["sent"] == 1  # 学到 UID 前保持旧行为，不误杀。
    assert sent[0][1].count("通知类型：") == 2
    assert store.self_uid() is None


def _crawler_db(tmp_path, rows):
    """rows: (id, 父楼ID, 用户ID) 楼层表最小子集。"""
    import sqlite3

    path = tmp_path / "sitewide.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE floors (id INTEGER PRIMARY KEY, 父楼ID INTEGER, 用户ID INTEGER)"
    )
    connection.executemany("INSERT INTO floors VALUES (?,?,?)", rows)
    connection.commit()
    connection.close()
    return path


def test_crawler_chain_walks_to_thread_root(tmp_path):
    # 抓取库把楼中楼扁平化：2882590 的父楼直接是顶层评论 10（作者 74670，即串根）。
    rows = [(10, 0, 74670), (2882107, 10, 66192), (2882590, 10, 92537)]
    path = _crawler_db(tmp_path, rows)
    chain = crawler_floor_chain(path, 2882590)
    assert [(fid, parent, uid) for fid, parent, uid in chain] == [
        (2882590, 10, 92537),
        (10, 0, 74670),
    ]


def test_thread_rooted_at_my_comment_is_included(tmp_path):
    me, other_a, other_b = 74670, 66192, 92537
    path = _crawler_db(tmp_path, [(10, 0, me), (2882107, 10, other_a), (2882590, 10, other_b)])
    events = [
        dict(event(kind_type=1, target=2882590), parent_uid=other_b, my_comment_id=10),
        dict(event(kind_type=1, target=2882107), parent_uid=other_a, my_comment_id=10),
    ]
    assert (
        non_interactive_event_keys(events, me, crawler_db=path) == set()
    )  # 串根=本人：他人互聊也纳入。


def test_others_thread_under_own_post_is_still_excluded(tmp_path):
    me, other_a, other_b = 74670, 66192, 92537
    # 串根 30 的作者是 555（不是本人）：即使在本人帖子下也不通知。
    path = _crawler_db(tmp_path, [(30, 0, 555), (2882107, 30, other_a), (2882590, 30, other_b)])
    events = [dict(event(kind_type=1, target=2882590), parent_uid=other_b, my_comment_id=30)]
    assert non_interactive_event_keys(events, me, crawler_db=path) == {("comment_reply", 2882590)}


def test_unresolvable_thread_defaults_to_excluded(tmp_path):
    me, other = 74670, 92537
    path = _crawler_db(tmp_path, [(10, 0, me)])  # 只有串根楼层在库里。
    events = [dict(event(kind_type=1, target=2882590), parent_uid=other, my_comment_id=404)]
    assert non_interactive_event_keys(events, me, crawler_db=path) == {
        ("comment_reply", 2882590)
    }  # 解析失败默认不通知。
    # 父楼作者未知但爬取库能定位串根=本人 → 纳入。
    events2 = [dict(event(kind_type=1, target=2882107), my_comment_id=10)]
    assert non_interactive_event_keys(events2, me, crawler_db=path) == set()


def test_direct_reply_to_own_comment_needs_no_crawler(tmp_path):
    me, other = 74670, 92537
    events = [dict(event(kind_type=1, target=2882107), parent_uid=me, my_comment_id=10)]
    assert non_interactive_event_keys(events, me, crawler_db=None) == set()


def test_agent_owned_interactions_win_over_thread_root_rules(tmp_path):
    # 大肥鱼自有内容上的互动：无论串根是谁，一律走动作邮件，不进微北洋通知。
    me, fish_post, fish_floor = 74670, 900, 800
    path = _crawler_db(tmp_path, [(fish_floor, 0, me), (2882590, fish_floor, 92537)])
    fish_post_row = {"id": fish_post, "title": "合成帖子", "content": "正文", "like_count": 2}
    events = [
        # 回复发生在"以本人评论为根"的串里（本应通知），但它在大肥鱼自己的帖子上。
        dict(
            event(kind_type=1, target=2882590),
            parent_uid=92537,
            my_comment_id=fish_floor,
            post_id=fish_post,
        ),
        normalize("likes", message("likes", 0, target=fish_post, post=fish_post_row)),
        normalize("floors", message("floors", 0, target=2882107, post=fish_post_row)),
    ]
    posts, floors = {fish_post}, {fish_floor}
    excluded = agent_event_keys(events, posts, floors)
    assert {
        ("comment_reply", 2882590),
        ("post_like", fish_post),
        ("post_comment", 2882107),
    } <= excluded
    # 串根规则不会把已排除的事件重新纳入。
    excluded |= non_interactive_event_keys(events, me, crawler_db=path)
    assert ("comment_reply", 2882590) in excluded
