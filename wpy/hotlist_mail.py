"""Render and send one daily, read-only hot-list email."""

from __future__ import annotations

from datetime import datetime
from email.utils import formatdate
from html import escape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo

from .notification_mail import MailConfig, NotificationError, send_mail
from .notifications import worker_lock


BEIYANG_BLUE = "#2A5A90"
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_ITEMS = 10
EXCERPT_LENGTH = 120
MAX_EMAIL_POST_CHARS = 8000
UPDATE_UNIT = "wpy-update.service"


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        elif not self.hidden and tag in ("br", "p", "div", "li", "tr", "blockquote"):
            self.parts.append("\n")
        elif not self.hidden and tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in ("br", "p", "div", "li", "tr", "blockquote"):
            self.parts.append("\n")
        elif not self.hidden and tag in ("td", "th"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def post_text(content):
    """Extract readable text from a post without carrying remote HTML."""
    parser = _TextOnly()
    parser.feed(str(content or ""))
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excerpt(content, limit=EXCERPT_LENGTH):
    """Turn the opening of a post body into a safe, short text summary."""
    text = re.sub(r"\s+", " ", post_text(content)).strip()
    if not text:
        return "帖子暂无可用正文摘要。"
    if len(text) <= limit:
        return text
    boundary = max(text.rfind(mark, 0, limit) for mark in "。！？；，")
    end = boundary + 1 if boundary >= limit // 2 else limit
    return text[:end].rstrip() + "…"


def query_daily_hotlist(db_path, *, limit=MAX_ITEMS):
    """Read today's (last 24 hours) view-ranked posts and their public counters."""
    path = Path(db_path)
    if not path.is_file():
        raise NotificationError("热榜数据库不存在或不可读")
    if not 1 <= int(limit) <= MAX_ITEMS:
        raise NotificationError(f"热榜条数必须在 1 到 {MAX_ITEMS} 之间")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT id, 标题, 作者, 分管部门, 板块ID, 点赞数, 评论数, 收藏数, 浏览量, "
                "发布时间, 正文 "
                "FROM posts WHERE replace(substr(发布时间,1,19),'T',' ') > "
                "datetime('now','localtime','-1 day') "
                "ORDER BY 浏览量 DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error):
        raise NotificationError("读取热榜数据库失败") from None
    posts = []
    for row in rows:
        content = post_text(row["正文"])
        truncated = len(content) > MAX_EMAIL_POST_CHARS
        if truncated:
            content = (
                content[:MAX_EMAIL_POST_CHARS].rstrip() + "\n……（正文较长，邮件仅展示前 8,000 字）"
            )
        posts.append(
            {
                "id": row["id"],
                "title": row["标题"] or "（无标题）",
                "author": row["作者"] or "匿名",
                "board": row["分管部门"] or "",
                "board_id": row["板块ID"] or 0,
                "views": row["浏览量"] or 0,
                "likes": row["点赞数"] or 0,
                "comments": row["评论数"] or 0,
                "favorites": row["收藏数"] or 0,
                "published": (row["发布时间"] or "")[:16].replace("T", " "),
                "summary": excerpt(content),
                "content": content or "帖子暂无可用正文。",
                "truncated": truncated,
            }
        )
    return posts


def render_hotlist(posts, *, report_date=None):
    """Return subject, plain text, and email-client-friendly HTML."""
    report_date = report_date or datetime.now(SHANGHAI).date()
    if isinstance(report_date, datetime):
        report_date = report_date.date()
    date_text = report_date.isoformat()
    subject = f"今日热榜｜{date_text}"
    text = [f"今日热榜 · {date_text}", "统计范围：最近 24 小时发帖，按浏览量排序。", ""]
    cards = []
    for index, post in enumerate(posts, 1):
        content = post_text(post.get("content") or post.get("summary")) or "帖子暂无可用正文。"
        board = post.get("board") or ""
        metrics = (
            f"浏览 {post['views']:,}　点赞 {post['likes']:,}　"
            f"评论 {post['comments']:,}　收藏 {post['favorites']:,}"
        )
        text.extend(
            [
                f"{index:02d}. {post['title']}",
                f"发帖人：{post['author']}" + (f"　板块：{board}" if board else ""),
                metrics,
                "正文：",
                content,
                "",
            ]
        )
        badge = (
            f'<span style="display:inline-block;color:{BEIYANG_BLUE};font-weight:700;'
            'font-size:13px;padding:3px 8px;border:1px solid #d8e3f0;border-radius:12px">'
            + f"{index:02d}</span>"
        )
        board_html = (
            f'<span style="color:#777;font-size:12px;margin-left:8px">{escape(board)}</span>'
            if board
            else ""
        )
        cards.append(
            '<tr><td style="padding:14px 16px;border:1px solid #e5e9ef;'
            'border-bottom:0;background:#fff">'
            f"{badge}{board_html}"
            '<div style="margin-top:9px;font-size:17px;line-height:1.45;font-weight:700">'
            f'<span style="color:#212121">{escape(post["title"])}</span></div>'
            '<div style="margin-top:5px;color:#777;font-size:12px">发帖人：'
            f"{escape(post['author'])}</div>"
            '<div style="margin-top:10px;color:#667085;font-size:12px;line-height:1.8">'
            f"浏览 {post['views']:,}　点赞 {post['likes']:,}　"
            f"评论 {post['comments']:,}　收藏 {post['favorites']:,}</div>"
            '<div style="margin-top:10px;padding-top:10px;border-top:1px solid #eef0f3;'
            'font-size:14px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere">'
            f'<strong style="color:{BEIYANG_BLUE}">正文：</strong>{escape(content)}</div>'
            "</td></tr>"
            '<tr><td style="height:10px;font-size:0;line-height:0">&nbsp;</td></tr>'
        )
    if not posts:
        text.append("最近 24 小时暂无可上榜帖子。")
        cards.append(
            '<tr><td style="padding:22px 16px;color:#667085;border:1px solid #e5e9ef">'
            "最近 24 小时暂无可上榜帖子。</td></tr>"
        )
    text.append("正文直接包含在邮件中；过长帖子会注明节选范围。")
    body = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body '
        'style="margin:0;background:#f4f6f9;color:#212121;font-family:Arial,'
        'Microsoft YaHei,sans-serif"><table role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0" style="background:#f4f6f9"><tr><td '
        'align="center" style="padding:22px 10px"><table role="presentation" width="600" '
        'cellpadding="0" cellspacing="0" style="width:100%;max-width:600px">'
        '<tr><td style="padding:18px 20px;background:#fff;border-top:4px solid '
        f'{BEIYANG_BLUE};border-bottom:1px solid #e5e9ef">'
        f'<div style="color:{BEIYANG_BLUE};font-size:23px;font-weight:700">今日热榜</div>'
        f'<div style="margin-top:5px;color:#777;font-size:13px">{date_text} · '
        "最近 24 小时 · 浏览量排序</div></td></tr>"
        '<tr><td style="padding-top:12px"><table role="presentation" width="100%" '
        'cellpadding="0" cellspacing="0">'
        + "".join(cards)
        + '</table></td></tr><tr><td style="padding:12px 4px;color:#888;font-size:11px;'
        'line-height:1.6">正文直接包含在邮件中；过长帖子会注明节选范围。'
        "</td></tr></table></td></tr></table></body></html>"
    )
    return subject, "\n".join(text), body


def wait_for_update(*, timeout=2700, poll=15, run=subprocess.run, sleep=time.sleep):
    """At the scheduled time, wait for the hourly crawler run to finish."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = run(
                ["systemctl", "show", UPDATE_UNIT, "--property=ActiveState,Result"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            raise NotificationError("无法确认当日热榜更新状态") from None
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode != 0 or not {"ActiveState", "Result"} <= properties.keys():
            raise NotificationError("无法确认当日热榜更新状态")
        active_state = properties["ActiveState"].strip()
        result_state = properties["Result"].strip()
        if active_state not in ("active", "activating", "reloading"):
            if result_state not in ("success", "done"):
                raise NotificationError("当日热榜更新未成功，暂不发送摘要邮件")
            return
        if time.monotonic() >= deadline:
            raise NotificationError("等待当日热榜更新超时，摘要邮件将在下次重试")
        sleep(poll)


def send_daily_hotlist(
    *,
    db_path,
    state_dir,
    smtp_config,
    limit=MAX_ITEMS,
    now=None,
    sender=send_mail,
    before_send=wait_for_update,
):
    """Send at most one hot-list digest per local calendar day."""
    current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    send_date = current.date()
    mail_config = MailConfig.load(smtp_config)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state_dir, 0o700)
    with worker_lock(state_dir):
        state_path = state_dir / "hotlist.sqlite3"
        connection = sqlite3.connect(state_path, timeout=10)
        os.chmod(state_path, 0o600)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sent_days (day TEXT PRIMARY KEY, sent REAL NOT NULL, "
                "item_count INTEGER NOT NULL)"
            )
            connection.commit()
            if connection.execute(
                "SELECT 1 FROM sent_days WHERE day=?", (send_date.isoformat(),)
            ).fetchone():
                return {"sent": 0, "skipped": "already_sent", "items": 0}
            before_send()
            posts = query_daily_hotlist(db_path, limit=limit)
            subject, plain, body = render_hotlist(posts, report_date=send_date)
            message_id = f"<daily-hotlist-{send_date:%Y%m%d}@wpy.local>"
            sender(
                mail_config,
                subject,
                plain,
                body,
                message_id,
                sender_name="微北洋热榜",
                mail_category="daily-hotlist",
            )
            connection.execute(
                "INSERT INTO sent_days(day,sent,item_count) VALUES (?,?,?)",
                (send_date.isoformat(), current.timestamp(), len(posts)),
            )
            connection.commit()
            return {"sent": 1, "items": len(posts), "day": send_date.isoformat()}
        except sqlite3.Error:
            connection.rollback()
            raise NotificationError("保存热榜邮件发送状态失败") from None
        finally:
            connection.close()


def preview_posts():
    """Synthetic content for a safe local preview; never uses production posts."""
    return [
        {
            "id": 987654321,
            "title": "示例：周末校园活动怎么安排更合适？",
            "author": "示例同学",
            "board": "校园活动",
            "views": 1842,
            "likes": 86,
            "comments": 32,
            "favorites": 17,
            "summary": "发帖人整理了几个适合周末的活动方案，并邀请大家讨论集合时间和地点。",
            "content": "发帖人整理了几个适合周末的活动方案。\n\n大家可以讨论集合时间和地点。",
        },
        {
            "id": 987654322,
            "title": "示例：食堂新窗口试吃记录",
            "author": "示例同学乙",
            "board": "美食专区",
            "views": 1260,
            "likes": 54,
            "comments": 21,
            "favorites": 9,
            "summary": "记录新窗口的菜品、价格和排队情况，附上几条同学们的体验反馈。",
            "content": "记录新窗口的菜品、价格和排队情况。\n\n附上几条同学们的体验反馈。",
        },
    ]
