"""AI 自主互动入口；默认单轮预演，--watch --execute 才常驻执行。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import threading
import uuid

from . import auth
from .ai_agent import (AgentError, AuthenticationError, DEFAULT_RULES, Executor, Forum,
                       INTERVAL, Model, ModelConfig, Store, cycle, worker_lock)
from .config import DATA_DIR
from . import ai_agent_mail as mail
from .notification_mail import MailConfig, NotificationError
from .ai_agent_privacy import PrivacyGuard


def add_parser(sub):
    parser = sub.add_parser("agent", help="天大模型自主论坛互动（默认单轮预演）")
    parser.add_argument("--execute", action="store_true", help="实际执行获准的论坛动作")
    parser.add_argument("--watch", action="store_true", help="常驻运行，Ctrl+C 或 SIGTERM 停止")
    parser.add_argument("--interval", type=int, default=INTERVAL,
                        help="每轮结束后等待秒数，默认 8640（约 2.4 小时），至少 7200")
    parser.add_argument("--model-config", default=os.environ.get("WPY_AI_MODEL_CONFIG", ""),
                        help="本机 exam 风格的 JSON 配置路径，只读 tju_llm_* 字段")
    parser.add_argument("--rules-file", default=os.environ.get("WPY_AI_RULES_FILE", ""),
                        help="本机 UTF-8 互动目标文件，不能改变硬性权限与额度")
    parser.add_argument("--state-dir", default=os.environ.get(
        "WPY_AI_STATE_DIR", str(Path(DATA_DIR) / "ai-agent")), help="AI 独立私有状态目录")
    parser.add_argument("--smtp-config", default=os.environ.get("WPY_AI_SMTP_CONFIG", ""),
                        help="启用动作邮件，读取现有 SMTP JSON（发件人显示名为大肥鱼）")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--test-email", action="store_true", help="只发送合成邮件测试，不访问论坛")
    modes.add_argument("--retry-mail", action="store_true", help="只补发动作邮件，不访问论坛")
    return parser


def main(args):
    stop = threading.Event()
    previous = {}
    try:
        config_path = getattr(args, "smtp_config", "")
        mail_config = MailConfig.load(config_path) if config_path else None
        send = mail.sender(mail_config) if mail_config else None
        test_email = getattr(args, "test_email", False)
        retry_mail = getattr(args, "retry_mail", False)
        if (test_email or retry_mail) and (not send or args.watch):
            raise AgentError("邮件测试/补发需要 --smtp-config，不能与 --watch 同时使用")
        if test_email:
            import time
            send(*mail.render({"kind": "test", "at": time.time(),
                               "detail": "这是一封合成测试邮件，没有执行论坛互动。"}),
                 "<ai-test-" + uuid.uuid4().hex + "@wpy.invalid>")
            print("大肥鱼测试邮件已获 SMTP 接受", flush=True)
            return 0
        if args.interval < 7200:
            raise AgentError("AI 轮次间隔至少两小时；默认约 2.4 小时")
        config = ModelConfig.load(args.model_config) if not retry_mail else None
        # 必须明确指定 AI 账号；不读取 WPY_ACCOUNT、凭据.json 或看板会话。
        account = os.environ.get("WPY_AI_ACCOUNT", "").strip()
        password = os.environ.get("WPY_AI_PASSWORD", "")
        if not account or (not password and not retry_mail):
            raise AgentError("请在私有环境中配置 WPY_AI_ACCOUNT / WPY_AI_PASSWORD")
        rules = DEFAULT_RULES
        if args.rules_file:
            try:
                extra_rules = Path(args.rules_file).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                raise AgentError("无法读取 AI 互动目标文件") from None
            if not extra_rules.strip() or len(extra_rules) > 4000:
                raise AgentError("AI 互动目标文件为空或过长")
            rules = DEFAULT_RULES + "\n维护者私有偏好：" + extra_rules
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop.set())
        with worker_lock(args.state_dir):
            store = Store(args.state_dir, account)
            try:
                if send:
                    mail.bind(store, mail_config)
                if retry_mail:
                    print(json.dumps({"mail": mail.deliver(store, send)}), flush=True)
                    return 0
                failures = 0
                due = store.next_cycle() if args.watch and args.execute else 0
                if send and args.watch:
                    mail.enqueue(store, "started")
                while not stop.is_set():
                    if send:
                        delivery = mail.deliver(store, send)
                        if delivery["sent"] or delivery["failed"]:
                            print(json.dumps({"mail": delivery}), flush=True)
                    now = store.now()
                    if now < due:
                        mail_due = mail.next_due(store) if send else None
                        stop.wait(max(0.1, min(due, mail_due if mail_due is not None else due) - now))
                        continue
                    # 先保存下轮时间；进程意外重启不会立刻再跑一轮论坛互动。
                    if args.watch and args.execute:
                        store.next_cycle(now + args.interval)
                    executor = None
                    try:
                        # 每轮单独登录，token 只存内存；不重试结果未知的写操作。
                        try:
                            token = auth.login(account, password)
                        except Exception:
                            raise AuthenticationError("AI 本人账号登录失败，请检查私有配置") from None
                        protected = [account, password, token, config.key, os.environ.get("WPY_APP_SECRET", "")]
                        if mail_config:
                            protected.extend([mail_config.user, mail_config.recipient, mail_config.password])
                        privacy = PrivacyGuard(protected)
                        executor = Executor(Forum(token), store, execute=args.execute, stopped=stop.is_set,
                                            report=bool(send), privacy=privacy)
                        model = Model(config)
                        model.privacy = privacy
                        result = cycle(model, executor, rules)
                        print(json.dumps({"mode": "execute" if args.execute else "preview", **result}), flush=True)
                        if send and args.execute and not result.get("writes") and result.get("reads"):
                            mail.enqueue(store, "browse", reads=result["reads"], denied=result.get("denied", 0),
                                         posts=list(executor.viewed_posts.values()))
                        failures = 0
                    except AuthenticationError as error:
                        if send:
                            mail.enqueue(store, "error", detail=str(error))
                            mail.deliver(store, send)
                        raise
                    except AgentError as error:
                        if send and not stop.is_set() and not (executor and executor.attempted_write):
                            mail.enqueue(store, "error", detail=str(error))
                        if not args.watch:
                            if send:
                                mail.deliver(store, send)
                            raise
                        failures += 1
                        print(str(error), flush=True)
                    due = store.now() + min(86400, args.interval * 2 ** min(failures, 3))
                    if args.watch and args.execute:
                        store.next_cycle(due)
                    if send:
                        print(json.dumps({"mail": mail.deliver(store, send)}), flush=True)
                    if not args.watch:
                        break
                if send and args.watch and stop.is_set():
                    mail.enqueue(store, "stopped")
                    print(json.dumps({"mail": mail.deliver(store, send)}), flush=True)
            finally:
                store.close()
        return 0
    except AuthenticationError as error:
        print(str(error), flush=True)
        return 2
    except (AgentError, NotificationError) as error:
        print(str(error), flush=True)
        return 1
    except Exception:
        # 兜底也不输出 traceback、原始接口响应、模型文本或秘密。
        print("AI 任务异常停止，请检查配置与私有状态目录", flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
