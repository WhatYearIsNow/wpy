"""个人互动邮件：标准库 SMTP，配置兼容 course 的 user/pass/to JSON。"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate
import html
import json
from pathlib import Path
import re
import smtplib
import ssl
from urllib.parse import urlencode, urlsplit


class NotificationError(RuntimeError):
    """只包含可公开记录的错误信息，不包含账号、响应正文或 SMTP 异常。"""


@dataclass(frozen=True, repr=False)
class MailConfig:
    user: str
    password: str
    recipient: str
    host: str = "smtp.qq.com"
    port: int = 465

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
            config = cls(data["user"], data["pass"], data.get("to") or data["user"],
                         data.get("host", "smtp.qq.com"), int(data.get("port", 465)))
        except (OSError, ValueError, TypeError, KeyError):
            raise NotificationError("无法读取 SMTP 配置，需要 user、pass、to") from None
        for address in (config.user, config.recipient):
            if not isinstance(address, str) or not re.fullmatch(
                    r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address):
                raise NotificationError("SMTP 发件人或收件人格式无效")
        if (not isinstance(config.password, str) or not config.password
                or not isinstance(config.host, str) or not config.host.strip()
                or not 1 <= config.port <= 65535):
            raise NotificationError("SMTP 配置不完整或端口无效")
        return config


def validate_base_url(value):
    if not value:
        return ""
    url = urlsplit(value)
    if (url.scheme not in ("http", "https") or not url.hostname or url.username
            or url.password or url.query or url.fragment
            or any(c.isspace() for c in value)):
        raise NotificationError("看板链接必须是无账号、查询参数或片段的 HTTP(S) 地址")
    return value.rstrip("/") + "/"


LABELS = {
    "post_like": "有人点赞了你的帖子",
    "floor_like": "有人点赞了你的评论",
    "post_comment": "有人回复了你的帖子",
    "comment_reply": "有人回复了你的评论",
}


def notification_subject(events, *, test=False):
    kinds = {event["kind"] for event in events}
    parts = []
    for action, post_kind, comment_kind in (("点赞", "post_like", "floor_like"),
                                           ("回复", "post_comment", "comment_reply")):
        targets = [target for kind, target in ((post_kind, "帖子"), (comment_kind, "评论")) if kind in kinds]
        if targets:
            parts.append(f"有人{action}了你的" + "、".join(targets))
    return "；".join(parts) + ("（测试）" if test else "")


def render_digest(events, base_url="", *, test=False):
    """course 风格的标题、键值表、备注；所有论坛文本必须转义。"""
    base_url = validate_base_url(base_url)
    title = "微北洋邮件提醒测试" if test else "微北洋互动提醒"
    subject = notification_subject(events, test=test)
    intro = ("这是一封使用合成内容的测试邮件，没有发生下面展示的真实互动。" if test
             else "以下是本轮检查发现的新互动，未更改微北洋中的已读状态。")
    text = [title, intro]
    cards = []
    for event in events:
        label = LABELS[event["kind"]]
        rows = [("通知类型", label), ("帖子编号", f"#MP{event['post_id']}"),
                ("帖子标题", event.get("title") or "[标题暂不可用]"),
                ("帖子正文", event.get("post_content") or "[正文暂不可用]"),
                ("帖子点赞", f"{event['post_likes']} 赞" if event.get("post_likes") is not None
                 else "[点赞数暂不可用]")]
        if event["kind"] in ("floor_like", "comment_reply"):
            own_comment = event.get("my_comment") or (
                event.get("content") if event["kind"] == "floor_like" else event.get("original"))
            own_likes = event.get("my_comment_likes")
            if own_likes is None and event["kind"] == "floor_like":
                own_likes = event.get("likes")
            rows.append(("我的评论", own_comment or "[原评论暂不可用]"))
            rows.append(("评论点赞", f"{own_likes} 赞" if own_likes is not None
                         else "[点赞数暂不可用]"))
        if event["kind"] in ("post_like", "floor_like"):
            summary = (f"本轮观察增加 {event['increase']}，当前累计 {event['likes']} 赞"
                       if event.get("increase") is not None else
                       f"当前累计 {event['likes']} 赞（首次观察此目标，新增数量未知）")
            rows.append(("点赞变化", summary))
        else:
            rows.extend([("回复人", event.get("author") or "[昵称暂不可用]"),
                         ("回复时间", event.get("time") or "[时间暂不可用]"),
                         ("回复详情", event.get("content") or "[回复内容暂不可用]")])
        text.extend(f"{key}：{value}" for key, value in rows)
        table = "".join(
            '<tr><td style="padding:3px 12px 3px 0;color:#8a93a5;vertical-align:top;'
            'white-space:nowrap">' + html.escape(key) + '</td><td style="padding:3px 0;'
            'white-space:pre-wrap;overflow-wrap:anywhere">' + html.escape(str(value)) + '</td></tr>'
            for key, value in rows)
        link = ""
        if base_url:
            target = base_url + "?" + urlencode({"postId": event["post_id"]})
            text.append("查看帖子：" + target)
            link = '<p><a href="' + html.escape(target, quote=True) + '">查看帖子</a></p>'
        cards.append('<div style="border-top:1px solid #e5e7eb;padding:14px 0">'
                     '<table style="border-collapse:collapse;font-size:14px">' + table
                     + '</table>' + link + '</div>')
        text.append("")
    note = "默认每五分钟检查一次；点赞是聚合计数，无法还原每位点赞人或精确发生时间。"
    text.append(note)
    body = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>'
            '<div style="font-family:Segoe UI,Microsoft YaHei,sans-serif;line-height:1.6;'
            'max-width:680px;margin:auto;padding:20px">'
            '<h2 style="color:#2A5A90;margin:0 0 10px">' + title + '</h2><p>' + intro + '</p>'
            + "".join(cards) + '<p style="color:#8a93a5;font-size:13px">' + note + '</p>'
            '</div></body></html>')
    return subject, "\n".join(text), body


def send_mail(config, subject, text, body, message_id, *, sender_name="微北洋"):
    message = EmailMessage()
    message["From"] = f"{sender_name} <{config.user}>"
    message["To"] = config.recipient
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = message_id
    message.set_content(text)
    message.add_alternative(body, subtype="html")
    accepted = False
    try:
        with smtplib.SMTP_SSL(config.host, config.port, timeout=30,
                              context=ssl.create_default_context()) as client:
            client.login(config.user, config.password)
            refused = client.send_message(message)
            if refused:
                raise NotificationError("SMTP 未接受收件人")
            accepted = True
    except (OSError, smtplib.SMTPException):
        # SMTP 异常可能包含收件地址或服务器原始响应，不能写进日志。
        if not accepted:
            raise NotificationError("邮件未确认发送，请检查 SMTP 配置或网络；稍后补发") from None


def test_events():
    own_post = {"post_id": 101, "title": "示例：校园活动安排", "post_likes": 8,
                "post_content": "周末准备组织一场校园摄影活动。\n集合时间暂定下午一点，欢迎大家提出建议。"}
    other_post = {"post_id": 202, "title": "示例：周末活动讨论", "post_likes": 12,
                  "post_content": "大家希望周末活动几点开始？\n请在评论区留下你的建议。"}
    own_comment = "我建议下午两点集合，这样能留出午休时间。"
    return [
        dict(own_post, kind="post_like", likes=8, increase=1),
        dict(other_post, kind="floor_like", my_comment=own_comment, my_comment_likes=4,
             likes=4, increase=2),
        dict(own_post, kind="post_comment", author="示例评论者", time="2026-01-01 12:00",
             content="能否改到下午两点？这样大家有时间午休。"),
        dict(other_post, kind="comment_reply", my_comment=own_comment, my_comment_likes=4,
             author="示例回复者", time="2026-01-01 12:05",
             content="这个建议很好，我会把集合时间改到下午两点。"),
    ]
