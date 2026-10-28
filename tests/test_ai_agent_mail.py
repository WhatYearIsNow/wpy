"""动作和邮件必须一致：合成 SMTP/论坛，验证事务、重启与独立补发。"""

import json
from types import SimpleNamespace

import pytest

from wpy import ai_agent as ai, ai_agent_cli as cli, ai_agent_mail as mail
from wpy import notification_mail as smtp
from .test_ai_agent import Remote, proposal, read


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("不能访问真实网络"))
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *a, **kw: pytest.fail("不能发送真实邮件"))
    for name in (
        "WPY_AI_KEY",
        "WPY_AI_MODEL",
        "WPY_AI_BASE",
        "WPY_AI_ACCOUNT",
        "WPY_AI_PASSWORD",
        "WPY_AI_SMTP_CONFIG",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def setup(tmp_path):
    remote = Remote()
    clock = [1000000.0]
    with ai.worker_lock(tmp_path):
        store = ai.Store(tmp_path, "test-personal", now=lambda: clock[0])
        executor = ai.Executor(
            ai.Forum("test_personal_token", request=remote), store, execute=True, report=True
        )
        yield executor, remote, store, clock
        store.close()


@pytest.mark.parametrize(
    "action,args",
    [
        ("create_post", {"board": 2, "title": "合成标题", "content": "合成正文"}),
        ("comment_post", {"post_id": 101, "content": "合成评论"}),
        ("reply_comment", {"post_id": 101, "floor_id": 11, "content": "合成回复"}),
        ("like_post", {"post_id": 101}),
        ("favorite_post", {"post_id": 101}),
        ("like_comment", {"post_id": 101, "floor_id": 11}),
    ],
)
def test_each_action_generates_exactly_one_truthful_report(setup, action, args):
    executor, remote, store, _ = setup
    read(executor)
    executor.run(proposal(action, **args))
    sent = []
    assert mail.deliver(store, lambda *values: sent.append(values)) == {
        "sent": 1,
        "failed": 0,
        "pending": 0,
    }
    assert len(remote.writes) == len(sent) == 1
    subject, text, html, message_id = sent[0]
    assert subject == mail.LABELS[action] and subject.startswith("大肥鱼")
    assert "论坛已接受该操作" in text
    assert "评论编号" not in text and "互动目标评论" not in text
    if action in ai.SPEECH:
        assert args["content"] in text and "AI 助手生成" not in text
    if action == "reply_comment":
        assert "被回复者：合成昵称" in text and "原评论：合成评论" in text
        assert "大肥鱼回复的内容" in text
    if action == "create_post":
        assert "帖子发出者：大肥鱼" in text
        assert text.count("帖子内容：") == 1
        assert text.index("帖子标题：") < text.index("帖子发出者：") < text.index("帖子内容：")
    else:
        assert "#MP101" in text
        assert "帖子发出者：合成发帖者" in text
        assert "帖子内容：合成正文" in text
        assert text.index("帖子标题：") < text.index("帖子发出者：") < text.index("帖子内容：")
        if action in ai.SPEECH:
            assert text.index("帖子内容：") < text.index(args["content"])
    assert "test_personal_token" not in text and "test_remote_secret" not in text
    assert store.db.execute("SELECT payload FROM agent_mail").fetchone()[0] == "{}"
    assert mail.deliver(store, lambda *v: pytest.fail("不应重复发信"))["sent"] == 0


def test_uncertain_forum_result_only_sends_unknown_notice_and_never_reposts(setup):
    executor, remote, store, _ = setup
    read(executor)
    remote.write_error = True
    with pytest.raises(ai.AgentError):
        executor.run(proposal("comment_post", post_id=101, content="合成评论"))
    sent = []
    mail.deliver(store, lambda *args: sent.append(args))
    assert sent[0][0] == "大肥鱼这次操作结果待核实"
    assert "结果未确认" in sent[0][1] and "论坛已接受" not in sent[0][1]
    assert len(remote.writes) == 1
    assert store.db.execute("SELECT status FROM actions").fetchone()[0] == "unknown"


def test_mail_retry_survives_restart_and_reuses_message_id_without_forum_call(setup, tmp_path):
    executor, remote, store, clock = setup
    read(executor)
    executor.run(proposal("comment_post", post_id=101, content="test_private_action_body"))
    attempted = []

    def fail(*args):
        attempted.append(args)
        raise OSError("test_private_smtp_response")

    assert mail.deliver(store, fail) == {"sent": 0, "failed": 1, "pending": 1}
    assert mail.next_due(store) == clock[0] + 300
    assert mail.deliver(store, fail)["failed"] == 0
    clock[0] += 301
    reopened = ai.Store(tmp_path, "test-personal", now=lambda: clock[0])
    try:
        mail.deliver(reopened, lambda *args: attempted.append(args))
        assert attempted[0][3] == attempted[1][3]
        assert len(remote.writes) == 1
        assert "test_private_action_body" not in "\n".join(reopened.db.iterdump())
    finally:
        reopened.close()


def test_reservation_and_report_insert_roll_back_together(setup, monkeypatch):
    executor, remote, store, _ = setup
    read(executor)

    def broken(*args, **kwargs):
        raise RuntimeError("test database failure")

    monkeypatch.setattr(mail, "insert", broken)
    with pytest.raises(RuntimeError):
        executor.run(proposal("comment_post", post_id=101, content="合成评论"))
    assert not remote.writes
    assert store.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 0


def test_accepted_mark_and_report_commit_together(setup, monkeypatch):
    _, _, store, _ = setup
    digest = store.reserve("comment_post", {"post_id": 101}, report={"post_id": 101})
    monkeypatch.setattr(
        mail, "complete", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test disk error"))
    )
    with pytest.raises(RuntimeError):
        store.finish(digest)
    assert store.db.execute("SELECT status FROM actions").fetchone()[0] == "unknown"
    assert (
        json.loads(store.db.execute("SELECT payload FROM agent_mail").fetchone()[0])["outcome"]
        == "unknown"
    )


def test_preview_and_denied_actions_do_not_report_success(setup):
    executor, remote, store, _ = setup
    read(executor)
    executor.execute = False
    executor.run(proposal("comment_post", post_id=101, content="合成评论"))
    executor.execute = True
    remote.board = 1
    with pytest.raises(ai.Denied):
        executor.run(proposal("comment_post", post_id=101, content="合成评论"))
    assert store.db.execute("SELECT count(*) FROM agent_mail").fetchone()[0] == 0


def test_render_escapes_forum_text_and_does_not_put_titles_in_subject():
    subject, text, body = mail.render(
        {
            "kind": "comment_post",
            "at": 1000,
            "post_id": 101,
            "title": "<script>test</script>",
            "content": "<b>合成评论</b>",
            "outcome": "accepted",
        }
    )
    assert "script" not in subject and "<script>" not in body
    assert "&lt;script&gt;" in body and "<b>合成评论</b>" in text


def test_empty_post_body_keeps_the_requested_mail_row():
    _, text, _ = mail.render(
        {
            "kind": "comment_post",
            "at": 1000,
            "post_id": 101,
            "title": "合成标题",
            "post_author": "合成发帖者",
            "post_content": "",
            "content": "合成评论",
            "outcome": "accepted",
        }
    )
    assert "帖子标题：合成标题\n帖子发出者：合成发帖者\n帖子内容：[纯图片帖子或正文为空]" in text


def test_sender_name_is_big_fish_and_existing_mail_default_unchanged(monkeypatch):
    messages = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message):
            messages.append(message)
            return {}

    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", SMTP)
    config = smtp.MailConfig(
        "sender@example.invalid", "test_smtp_password", "reader@example.invalid"
    )
    mail.sender(config)(
        "大肥鱼发了一个帖子", "合成正文", "<p>合成正文</p>", "<test-ai@wpy.invalid>"
    )
    smtp.send_mail(
        config, "有人回复了你的帖子", "合成正文", "<p>合成正文</p>", "<test-notify@wpy.invalid>"
    )
    assert messages[0]["From"].addresses[0].display_name == "大肥鱼"
    assert messages[1]["From"].addresses[0].display_name == "微北洋"
    assert messages[0]["From"].addresses[0].addr_spec == config.user


def test_email_binding_and_legacy_action_state_migration(setup, tmp_path):
    _, _, store, _ = setup
    config = smtp.MailConfig("sender@example.invalid", "test_password", "reader@example.invalid")
    mail.bind(store, config)
    with pytest.raises(smtp.NotificationError):
        mail.bind(store, smtp.MailConfig(config.user, config.password, "other@example.invalid"))
    store.reserve("like_post", {"post_id": 101})
    store.db.execute("DROP TABLE agent_mail")
    store.db.commit()
    reopened = ai.Store(tmp_path, "test-personal")
    try:
        assert reopened.db.execute("SELECT count(*) FROM actions").fetchone()[0] == 1
        assert reopened.db.execute("SELECT count(*) FROM agent_mail").fetchone()[0] == 0
    finally:
        reopened.close()


def configure_cli(monkeypatch, tmp_path):
    path = tmp_path / "smtp.json"
    path.write_text(
        json.dumps(
            {
                "user": "sender@example.invalid",
                "pass": "test_password",
                "to": "reader@example.invalid",
            }
        )
    )
    monkeypatch.setenv("WPY_AI_KEY", "test_tju_key")
    monkeypatch.setenv("WPY_AI_ACCOUNT", "test-personal")
    monkeypatch.setenv("WPY_AI_PASSWORD", "test_personal_password")
    return SimpleNamespace(
        interval=8640,
        model_config="",
        rules_file="",
        state_dir=tmp_path,
        execute=True,
        watch=False,
        smtp_config=str(path),
        test_email=False,
        retry_mail=False,
    )


def test_mail_only_modes_do_not_touch_forum(monkeypatch, tmp_path):
    args = configure_cli(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mail, "sender", lambda c: lambda *v: sent.append(v))
    monkeypatch.setattr(cli.auth, "login", lambda *a: pytest.fail("邮件模式不能登录论坛"))
    args.test_email = True
    assert cli.main(args) == 0 and len(sent) == 1
    assert sent[0][0] == "大肥鱼邮件通知测试"
    args.test_email = False
    args.retry_mail = True
    with ai.worker_lock(tmp_path):
        store = ai.Store(tmp_path, "test-personal")
        mail.enqueue(store, "browse", reads=3, denied=0)
        store.close()
    assert cli.main(args) == 0 and len(sent) == 2


def test_auth_failure_sends_notice_and_exits_without_restart_loop(monkeypatch, tmp_path):
    args = configure_cli(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(mail, "sender", lambda c: lambda *v: sent.append(v))
    monkeypatch.setattr(
        cli.auth, "login", lambda *a: (_ for _ in ()).throw(RuntimeError("test_private_auth_error"))
    )
    assert cli.main(args) == 2
    assert len(sent) == 1 and sent[0][0] == "大肥鱼这轮遇到问题"
    assert "test_private_auth_error" not in sent[0][1]


def test_restart_uses_persisted_forum_schedule_while_mail_retries_can_wake_early(
    monkeypatch, tmp_path
):
    args = configure_cli(monkeypatch, tmp_path)
    args.watch = True
    with ai.worker_lock(tmp_path):
        store = ai.Store(tmp_path, "test-personal")
        store.next_cycle(store.now() + 8000)
        mail.enqueue(store, "browse", reads=3)
        store.close()
    waits = []

    class Stop:
        stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, delay):
            waits.append(delay)
            self.stopped = True

    monkeypatch.setattr(cli.threading, "Event", Stop)
    monkeypatch.setattr(cli.auth, "login", lambda *a: pytest.fail("重启后未到下一轮，不应访问论坛"))
    monkeypatch.setattr(
        mail, "sender", lambda c: lambda *v: (_ for _ in ()).throw(OSError("test smtp down"))
    )
    assert cli.main(args) == 0
    assert len(waits) == 1 and 299 <= waits[0] <= 301


@pytest.mark.parametrize("data", [{"post": {"id": 203}}, {"post_id": 203}, 203])
def test_returned_post_number_is_available_for_mail_without_raw_response(data):
    def request(*args, **kwargs):
        return {"code": 200, "data": data, "token": "test_not_for_mail"}

    forum = ai.Forum("test_personal_token", request=request)
    assert forum.call("post", {"type": 2}, write=True) == {"ok": True, "post_id": 203}


def test_reply_mail_distinguishes_target_and_new_comment(setup):
    _, _, store, _ = setup
    digest = store.reserve(
        "reply_comment",
        {"floor_id": 11},
        report={
            "post_id": 101,
            "target_floor_id": 11,
            "floor_id": 11,
            "content": "合成回复",
            "target_author": "合成昵称",
            "target_content": "合成评论",
        },
    )
    store.finish(digest, {"floor_id": 12})
    event = json.loads(store.db.execute("SELECT payload FROM agent_mail").fetchone()[0])
    assert event["target_floor_id"] == 11 and event["floor_id"] == 12
    subject, text, _ = mail.render(event)
    assert "被回复者：合成昵称" in text and "原评论：合成评论" in text
    assert "大肥鱼回复的内容：合成回复" in text and "互动目标评论" not in text


def test_reply_mail_without_context_does_not_expose_internal_floor_number(setup):
    _, _, store, _ = setup
    digest = store.reserve(
        "reply_comment",
        {"floor_id": 11},
        report={"post_id": 101, "target_floor_id": 11, "floor_id": 11, "content": "合成回复"},
    )
    event = json.loads(store.db.execute("SELECT payload FROM agent_mail").fetchone()[0])
    text = mail.render(event)[1]
    assert "互动目标评论" not in text and "评论编号" not in text and "被回复者" not in text


def test_cli_reports_sent_text_by_email_without_logging_it(monkeypatch, tmp_path, capsys):
    args = configure_cli(monkeypatch, tmp_path)
    remote = Remote()
    sent = []
    actions = iter(
        [
            proposal("read_post", post_id=101),
            proposal("read_comments", post_id=101, page=1),
            proposal("comment_post", post_id=101, content="test_private_sent_body"),
        ]
    )
    monkeypatch.setattr(cli.auth, "login", lambda *a: "test_personal_token")
    monkeypatch.setattr(cli, "Forum", lambda token: ai.Forum(token, request=remote))
    monkeypatch.setattr(
        cli, "Model", lambda config: SimpleNamespace(next=lambda messages: next(actions))
    )
    monkeypatch.setattr(mail, "sender", lambda c: lambda *values: sent.append(values))
    assert cli.main(args) == 0
    assert len(remote.writes) == len(sent) == 1
    assert "test_private_sent_body" in sent[0][1]
    assert "test_private_sent_body" not in capsys.readouterr().out


def test_browse_only_round_does_not_send_an_action_email(monkeypatch, tmp_path):
    args = configure_cli(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(cli.auth, "login", lambda *a: "test_personal_token")
    monkeypatch.setattr(mail, "sender", lambda c: lambda *values: sent.append(values))

    def browse(model, executor, rules):
        executor.viewed_posts[101] = {"id": 101, "title": "合成闲聊"}
        return {"reads": 3, "writes": 0, "denied": 1}

    monkeypatch.setattr(cli, "cycle", browse)
    assert cli.main(args) == 0
    assert sent == []
