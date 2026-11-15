"""Synthetic-only tests for the scheduled hot-list email."""

from datetime import datetime, timedelta
import json
import sqlite3
import subprocess

import pytest

from wpy.hotlist_mail import (
    SHANGHAI,
    NotificationError,
    excerpt,
    preview_posts,
    query_daily_hotlist,
    render_hotlist,
    send_daily_hotlist,
    wait_for_update,
)


def seed_db(path):
    connection = sqlite3.connect(path)
    connection.execute(
        'CREATE TABLE posts (id INTEGER PRIMARY KEY, "标题" TEXT, "作者" TEXT, '
        '"分管部门" TEXT, "板块ID" INTEGER, "点赞数" INTEGER, "评论数" INTEGER, '
        '"收藏数" INTEGER, "浏览量" INTEGER, "发布时间" TEXT, "正文" TEXT)'
    )
    now = datetime.now().astimezone()
    rows = [
        (
            101,
            "近期高浏览",
            "甲",
            "青年湖底",
            2,
            12,
            5,
            3,
            900,
            now.isoformat(),
            "<p>第一条摘要。</p>",
        ),
        (
            102,
            "近期最高热帖",
            "乙",
            "美食专区",
            13,
            80,
            35,
            18,
            1800,
            (now - timedelta(hours=23)).isoformat(),
            "<p>正文<strong>摘要</strong>内容</p>",
        ),
        (
            103,
            "昨日旧帖",
            "丙",
            "青年湖底",
            2,
            999,
            999,
            999,
            9000,
            (now - timedelta(hours=25)).isoformat(),
            "过期内容",
        ),
    ]
    connection.executemany("INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    connection.commit()
    connection.close()


@pytest.fixture
def hot_db(tmp_path):
    path = tmp_path / "sitewide.db"
    seed_db(path)
    return path


def test_daily_query_is_read_only_recent_and_view_ranked(hot_db):
    posts = query_daily_hotlist(hot_db)
    assert [post["id"] for post in posts] == [102, 101]
    assert posts[0]["summary"] == "正文摘要内容"
    assert posts[0]["favorites"] == 18
    assert not list(hot_db.parent.glob("sitewide.db-*"))


def test_excerpt_removes_markup_and_shortens_safely():
    assert excerpt("<p>安全<strong>摘要</strong>。</p>") == "安全摘要。"
    assert "<script>" not in excerpt("<script>secret()</script><p>可见内容</p>")
    assert excerpt("甲" * 300).endswith("…")
    assert len(excerpt("甲" * 300)) <= 121


def test_render_email_contains_metrics_links_and_escaped_content():
    post = {
        "id": 123,
        "title": "<script>标题</script>",
        "author": "示例用户",
        "board": "青年湖底",
        "summary": "合成摘要 & <安全内容>",
        "views": 1200,
        "likes": 42,
        "comments": 17,
        "favorites": 8,
    }
    subject, text, body = render_hotlist(
        [post], "https://board.example.invalid/wpy/", report_date=datetime(2026, 11, 15).date()
    )
    assert subject == "今日热榜｜2026-11-15"
    assert "浏览 1,200　点赞 42　评论 17　收藏 8" in text
    assert "预览全文：https://board.example.invalid/wpy/?postId=123" in text
    assert "<script>" not in body and "&lt;script&gt;标题" in body
    assert "合成摘要 &amp; &lt;安全内容&gt;" in body
    assert "#2A5A90" in body and "600px" in body
    assert "<img" not in body and "https://" in body


def test_empty_hotlist_has_clear_message():
    _, text, body = render_hotlist([], "https://board.example.invalid/wpy/")
    assert "最近 24 小时暂无可上榜帖子" in text
    assert "最近 24 小时暂无可上榜帖子" in body


def test_send_is_idempotent_and_uses_distinct_mail_category(hot_db, tmp_path):
    smtp = tmp_path / "smtp.json"
    smtp.write_text(
        json.dumps(
            {
                "user": "sender@example.invalid",
                "pass": "test-secret",
                "to": "reader@example.invalid",
            }
        ),
        encoding="utf-8",
    )
    sent = []
    now = datetime(2026, 11, 15, 22, 30, tzinfo=SHANGHAI)
    options = {
        "db_path": hot_db,
        "state_dir": tmp_path / "state",
        "smtp_config": smtp,
        "base_url": "https://board.example.invalid/wpy/",
        "now": now,
        "before_send": lambda: None,
        "sender": lambda *args, **kwargs: sent.append((args, kwargs)),
    }
    assert send_daily_hotlist(**options) == {"sent": 1, "items": 2, "day": "2026-11-15"}
    assert sent[0][0][1] == "今日热榜｜2026-11-15"
    assert sent[0][0][4] == "<daily-hotlist-20261115@wpy.local>"
    assert sent[0][1] == {"sender_name": "微北洋热榜", "mail_category": "daily-hotlist"}
    assert send_daily_hotlist(
        **{**options, "before_send": lambda: pytest.fail("已发送后不应等待更新")}
    ) == {"sent": 0, "skipped": "already_sent", "items": 0}
    assert len(sent) == 1


def test_send_failure_does_not_mark_day_sent(hot_db, tmp_path):
    smtp = tmp_path / "smtp.json"
    smtp.write_text(
        json.dumps({"user": "sender@example.invalid", "pass": "test-secret"}), encoding="utf-8"
    )
    options = {
        "db_path": hot_db,
        "state_dir": tmp_path / "state",
        "smtp_config": smtp,
        "base_url": "https://board.example.invalid/wpy/",
        "now": datetime(2026, 11, 15, 22, 30, tzinfo=SHANGHAI),
        "before_send": lambda: None,
    }

    def fail(*args, **kwargs):
        raise NotificationError("test SMTP failure")

    with pytest.raises(NotificationError, match="test SMTP failure"):
        send_daily_hotlist(**options, sender=fail)
    sent = []
    result = send_daily_hotlist(**options, sender=lambda *a, **kw: sent.append(a))
    assert result["sent"] == 1 and len(sent) == 1


def test_wait_for_update_waits_then_continues():
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "Result=success\nActiveState=active\n", ""),
            subprocess.CompletedProcess([], 0, "Result=success\nActiveState=inactive\n", ""),
        ]
    )
    sleeps = []
    wait_for_update(run=lambda *a, **kw: next(results), sleep=sleeps.append, poll=1)
    assert sleeps == [1]


def test_wait_for_update_rejects_failed_crawl():
    result = subprocess.CompletedProcess([], 0, "Result=exit-code\nActiveState=inactive\n", "")
    with pytest.raises(NotificationError, match="当日热榜更新未成功"):
        wait_for_update(run=lambda *a, **kw: result)


def test_preview_uses_only_synthetic_data():
    posts = preview_posts()
    assert len(posts) == 2
    assert all(post["id"] > 900_000_000 for post in posts)
