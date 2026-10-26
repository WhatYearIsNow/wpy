"""个人邮件通知 CLI。只有显式运行 notify 才访问消息或发送邮件。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import threading
import uuid

from .config import DATA_DIR
from .notification_mail import (
    MailConfig,
    NotificationError,
    render_digest,
    send_mail,
    test_events,
    validate_base_url,
)
from .notifications import (
    AuthenticationError,
    ForumClient,
    INTERVAL,
    NotificationStore,
    RateLimited,
    run_cycle,
    worker_lock,
)


def add_parser(sub):
    parser = sub.add_parser("notify", help="个人邮件提醒（默认单次检查，常驻需 --watch）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="持续检查，默认间隔五分钟")
    mode.add_argument(
        "--test-email", action="store_true", help="实际发送一封合成测试邮件，不访问论坛"
    )
    mode.add_argument("--retry-mail", action="store_true", help="只补发到期邮件，不登录或读取论坛")
    parser.add_argument(
        "--smtp-config",
        default=os.environ.get("WPY_SMTP_CONFIG", ""),
        help="SMTP JSON 文件路径，兼容 course 的 user/pass/to",
    )
    parser.add_argument(
        "--state-dir",
        default=os.environ.get("WPY_NOTIFY_STATE_DIR", str(Path(DATA_DIR) / "notifications")),
        help="独立的私有通知状态目录",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("WPY_NOTIFY_BASE_URL", ""),
        help="邮件中查看帖子的看板根地址，可留空",
    )
    parser.add_argument(
        "--agent-state",
        default=os.environ.get("WPY_NOTIFY_AI_STATE", ""),
        help="可选的大肥鱼状态目录或数据库；其互动不重复发送“微北洋”邮件",
    )
    parser.add_argument("--interval", type=int, default=INTERVAL, help="检查间隔秒数，至少 300 秒")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="每类消息最多检查页数，每页 20 条，默认 50；达到上限则不提交基线",
    )
    return parser


def watch(
    client, store, sender, stop, *, interval=INTERVAL, base_url="", agent_state="", report=print
):
    failures = 0
    while not stop.is_set():
        delay = interval
        try:
            result = run_cycle(client, store, sender, base_url, agent_state=agent_state)
            failures = 0
            report(json.dumps(result, ensure_ascii=False))
        except AuthenticationError:
            # 续登也失败时退出，不能每五分钟无限尝试错误密码。
            raise
        except RateLimited as error:
            delay = max(interval, error.delay)
            report(str(error))
        except NotificationError as error:
            failures += 1
            delay = min(3600, interval * 2 ** min(failures - 1, 4))
            report(str(error))
        stop.wait(delay)


def main(args):
    try:
        if args.interval < INTERVAL or not 1 <= args.max_pages <= 200:
            raise NotificationError("检查间隔至少 300 秒，--max-pages 必须在 1 到 200 之间")
        base_url = validate_base_url(args.base_url)
        config = MailConfig.load(args.smtp_config)
        sender = lambda subject, text, body, message_id: send_mail(
            config, subject, text, body, message_id
        )
        if args.test_email:
            sender(
                *render_digest(test_events(), base_url, test=True),
                f"<{uuid.uuid4().hex}@wpy.local>",
            )
            print("测试邮件已被 SMTP 接受；收件箱到达情况需由收件人确认。")
            return 0
        account = os.environ.get("WPY_ACCOUNT", "").strip()
        password = os.environ.get("WPY_PASSWORD", "")
        if not account or (not args.retry_mail and not password):
            raise NotificationError(
                "请显式配置本人 WPY_ACCOUNT / WPY_PASSWORD；不会读取抓取凭据文件"
            )
        from .auth import login

        client = ForumClient(lambda: login(account, password), max_pages=args.max_pages)
        with worker_lock(args.state_dir):
            store = NotificationStore(args.state_dir, account, config.recipient)
            try:
                if args.retry_mail:
                    result = store.deliver(sender)
                    print(json.dumps(result, ensure_ascii=False))
                    return int(result["failed"] > 0)
                if not args.watch:
                    result = run_cycle(
                        client,
                        store,
                        sender,
                        base_url,
                        agent_state=getattr(args, "agent_state", ""),
                    )
                    print(json.dumps(result, ensure_ascii=False))
                    return int(result["failed"] > 0)
                stop = threading.Event()
                old_handlers = {}
                for sig in (signal.SIGTERM, signal.SIGINT):
                    old_handlers[sig] = signal.signal(sig, lambda *_: stop.set())
                try:
                    watch(
                        client,
                        store,
                        sender,
                        stop,
                        interval=args.interval,
                        base_url=base_url,
                        agent_state=getattr(args, "agent_state", ""),
                    )
                finally:
                    for sig, handler in old_handlers.items():
                        signal.signal(sig, handler)
                return 0
            finally:
                store.close()
    except AuthenticationError as error:
        print(str(error))
        return 2
    except NotificationError as error:
        print(str(error))
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception:
        # 文件路径、数据库异常、远端返回等可能包含私人信息，CLI 不输出原始异常。
        print("通知任务失败；请检查配置、网络及私有状态目录，未输出原始异常。")
        return 1
