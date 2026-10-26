"""大肥鱼动作邮件：与论坛动作同事务预留，发送失败只补邮件。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import hmac
import json
import uuid

from .notification_mail import NotificationError, send_mail


LABELS = {
    "create_post": "大肥鱼发了一个帖子",
    "comment_post": "大肥鱼评论了帖子",
    "reply_comment": "大肥鱼回复了评论",
    "like_post": "大肥鱼点赞了帖子",
    "like_comment": "大肥鱼点赞了评论",
    "favorite_post": "大肥鱼收藏了帖子",
    "browse": "大肥鱼逛了逛湖底",
    "started": "大肥鱼开始上班了",
    "stopped": "大肥鱼下班了",
    "error": "大肥鱼这轮遇到问题",
    "test": "大肥鱼邮件通知测试",
}
CONTENT_LABELS = {
    "create_post": "帖子内容",
    "comment_post": "大肥鱼评论的内容",
    "reply_comment": "大肥鱼回复的内容",
}
TZ = timezone(timedelta(hours=8))


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS agent_mail (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        due REAL NOT NULL, sent REAL)""")


def insert(db, event_id, event, now):
    """调用方持有动作事务；不独立提交。"""
    db.execute(
        "INSERT INTO agent_mail (id,payload,due) VALUES (?,?,?)",
        (event_id, json.dumps({**event, "at": now}, ensure_ascii=False), now),
    )


def complete(db, digest, identifiers=None):
    row = db.execute("SELECT payload FROM agent_mail WHERE id=?", ("action:" + digest,)).fetchone()
    if row:
        event = json.loads(row[0])
        event.update(outcome="accepted")
        for name in ("post_id", "floor_id"):
            value = (identifiers or {}).get(name)
            if type(value) is int and value > 0:
                event[name] = value
        db.execute(
            "UPDATE agent_mail SET payload=? WHERE id=?",
            (json.dumps(event, ensure_ascii=False), "action:" + digest),
        )


def enqueue(store, kind, **details):
    with store.db:
        insert(store.db, uuid.uuid4().hex, {"kind": kind, **details}, store.now())


def bind(store, config):
    identity = store.digest(json.dumps([config.user, config.recipient]))
    with store.db:
        row = store.db.execute("SELECT value FROM meta WHERE key='mail_identity'").fetchone()
        if row and not hmac.compare_digest(row[0], identity):
            raise NotificationError("动作邮件状态绑定了另一发件或收件配置，请先核对私有配置")
        store.db.execute("INSERT OR IGNORE INTO meta VALUES ('mail_identity',?)", (identity,))


def render(event):
    kind = event["kind"]
    unknown = event.get("outcome") == "unknown"
    subject = "大肥鱼这次操作结果待核实" if unknown else LABELS[kind]
    rows = [
        ("动作", LABELS[kind]),
        ("时间", datetime.fromtimestamp(event["at"], TZ).strftime("%Y-%m-%d %H:%M:%S +08:00")),
    ]
    if "outcome" in event:
        rows.append(
            ("结果", "请求结果未确认，不会自动重发论坛动作" if unknown else "论坛已接受该操作")
        )
    if event.get("post_id"):
        rows.append(("帖子", "#MP" + str(event["post_id"])))
    if event.get("title"):
        rows.append(("帖子标题", event["title"]))
    if "post_content" in event:
        rows.append(("帖子内容", event.get("post_content") or "[纯图片帖子或正文为空]"))
    if event.get("target_author"):
        rows.append(("被回复者", event["target_author"]))
    if event.get("target_content"):
        rows.append(("原评论", event["target_content"]))
    if event.get("floor_id") and kind != "reply_comment":
        rows.append(("评论编号", str(event["floor_id"])))
    if event.get("target_floor_id") and not event.get("target_content"):
        rows.append(("互动目标评论", str(event["target_floor_id"])))
    if event.get("content"):
        rows.append((CONTENT_LABELS.get(kind, "大肥鱼发出的内容"), event["content"]))
    if "reads" in event:
        rows.append(("浏览", f"本轮读取 {event['reads']} 次；没有新增论坛互动"))
    if event.get("posts"):
        rows.append(
            (
                "看过的帖子",
                "\n".join("#MP" + str(post["id"]) + " " + post["title"] for post in event["posts"]),
            )
        )
    if "denied" in event:
        rows.append(("执行层拦截", str(event["denied"])))
    if event.get("detail"):
        rows.append(("说明", event["detail"]))
    if kind == "started":
        rows.append(
            (
                "节奏",
                "约 1 小时一轮；每天发帖 1 条、主动评论 10 条、主动回复 10 条，"
                "自己内容下的会话回复不限条数；校务区禁止发帖、评论和回复",
            )
        )
    text = "\n".join(f"{key}：{value}" for key, value in rows)
    table = "".join(
        '<tr><th style="text-align:left;vertical-align:top;padding:5px 12px 5px 0">'
        + html.escape(key)
        + '</th><td style="white-space:pre-wrap;padding:5px 0">'
        + html.escape(str(value))
        + "</td></tr>"
        for key, value in rows
    )
    body = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>'
        '<div style="font-family:Microsoft YaHei,sans-serif;max-width:680px;margin:auto;padding:20px">'
        "<h2>" + html.escape(subject) + "</h2><table>" + table + "</table></div></body></html>"
    )
    return subject, text, body


def sender(config):
    return lambda subject, text, body, message_id: send_mail(
        config, subject, text, body, message_id, sender_name="大肥鱼"
    )


def deliver(store, send, *, limit=30):
    """SMTP 是至少一次投递；复用 Message-ID，接受后立即清空正文。"""
    sent = failed = 0
    rows = store.db.execute(
        "SELECT id,payload,attempts FROM agent_mail WHERE sent IS NULL AND due<=? ORDER BY due,id LIMIT ?",
        (store.now(), limit),
    ).fetchall()
    for event_id, payload, attempts in rows:
        event = json.loads(payload)
        # 本机生成的随机事件编号/动作摘要用于稳定 Message-ID，不含论坛正文或账号。
        message_id = "<ai-" + event_id.replace(":", "-") + "@wpy.invalid>"
        try:
            send(*render(event), message_id)
        except Exception:
            failed += 1
            delay = min(86400, 300 * 2 ** min(attempts, 8))
            with store.db:
                store.db.execute(
                    "UPDATE agent_mail SET attempts=attempts+1,due=? WHERE id=?",
                    (store.now() + delay, event_id),
                )
        else:
            with store.db:
                store.db.execute(
                    "UPDATE agent_mail SET sent=?,payload='{}' WHERE id=?", (store.now(), event_id)
                )
            sent += 1
    pending = store.db.execute("SELECT count(*) FROM agent_mail WHERE sent IS NULL").fetchone()[0]
    return {"sent": sent, "failed": failed, "pending": pending}


def next_due(store):
    return store.db.execute("SELECT min(due) FROM agent_mail WHERE sent IS NULL").fetchone()[0]
