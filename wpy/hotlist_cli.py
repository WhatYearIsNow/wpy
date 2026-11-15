"""CLI for synthetic previews and scheduled daily hot-list email delivery."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

from .hotlist_mail import (
    MAX_ITEMS,
    NotificationError,
    preview_posts,
    render_hotlist,
    send_daily_hotlist,
    wait_for_update,
    SHANGHAI,
)


def add_parser(sub):
    parser = sub.add_parser("hotlist-mail", help="发送今日热榜邮件或生成合成预览")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--send", action="store_true", help="读取热榜数据库并发送今日邮件")
    mode.add_argument("--preview", metavar="PATH", help="写入合成 HTML 预览，不访问论坛或邮箱")
    parser.add_argument(
        "--db",
        default=os.environ.get("WPY_NOTIFY_CRAWLER_DB", ""),
        help="抓取数据库路径（只读）",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("WPY_HOTLIST_STATE_DIR", ""),
        help="邮件幂等状态目录",
    )
    parser.add_argument(
        "--smtp-config",
        default=os.environ.get("WPY_SMTP_CONFIG", ""),
        help="SMTP JSON 配置路径",
    )
    parser.add_argument("--limit", type=int, default=MAX_ITEMS, help=f"最多列出 {MAX_ITEMS} 条")
    return parser


def main(args):
    try:
        if args.preview:
            subject, _, body = render_hotlist(
                preview_posts(),
                report_date=datetime.now(SHANGHAI).date(),
            )
            target = Path(args.preview)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                '<!doctype html><meta charset="utf-8"><title>' + subject + "</title>" + body,
                encoding="utf-8",
            )
            print(f"合成热榜预览已写入：{target}")
            return 0
        missing = [
            name
            for name, value in (
                ("WPY_NOTIFY_CRAWLER_DB", args.db),
                ("WPY_HOTLIST_STATE_DIR", args.state_dir),
                ("WPY_SMTP_CONFIG", args.smtp_config),
            )
            if not value
        ]
        if missing:
            raise NotificationError("缺少热榜邮件配置：" + ", ".join(missing))
        if not 1 <= args.limit <= MAX_ITEMS:
            raise NotificationError(f"--limit 必须在 1 到 {MAX_ITEMS} 之间")
        result = send_daily_hotlist(
            db_path=args.db,
            state_dir=args.state_dir,
            smtp_config=args.smtp_config,
            limit=args.limit,
            before_send=wait_for_update,
        )
        print(result)
        return 0
    except NotificationError as error:
        print(str(error))
        return 1
