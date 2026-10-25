"""AI 自主互动入口；默认单轮预演，--watch --execute 才常驻执行。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import threading

from . import auth
from .ai_agent import (AgentError, AuthenticationError, DEFAULT_RULES, Executor, Forum,
                       INTERVAL, Model, ModelConfig, Store, cycle, worker_lock)
from .config import DATA_DIR


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
    return parser


def main(args):
    stop = threading.Event()
    previous = {}
    try:
        if args.interval < 7200:
            raise AgentError("AI 轮次间隔至少两小时；默认约 2.4 小时")
        config = ModelConfig.load(args.model_config)
        # 必须明确指定 AI 账号；不读取 WPY_ACCOUNT、凭据.json 或看板会话。
        account = os.environ.get("WPY_AI_ACCOUNT", "").strip()
        password = os.environ.get("WPY_AI_PASSWORD", "")
        if not account or not password:
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
                failures = 0
                while not stop.is_set():
                    try:
                        # 每轮单独登录，token 只存内存；不重试结果未知的写操作。
                        try:
                            token = auth.login(account, password)
                        except Exception:
                            raise AuthenticationError("AI 本人账号登录失败，请检查私有配置") from None
                        executor = Executor(Forum(token), store, execute=args.execute, stopped=stop.is_set)
                        result = cycle(Model(config), executor, rules)
                        print(json.dumps({"mode": "execute" if args.execute else "preview", **result}), flush=True)
                        failures = 0
                    except AuthenticationError:
                        raise
                    except AgentError as error:
                        if not args.watch:
                            raise
                        failures += 1
                        print(str(error), flush=True)
                    if not args.watch:
                        break
                    stop.wait(min(86400, args.interval * 2 ** min(failures, 3)))
            finally:
                store.close()
        return 0
    except AgentError as error:
        print(str(error), flush=True)
        return 1
    except Exception:
        # 兜底也不输出 traceback、原始接口响应、模型文本或秘密。
        print("AI 任务异常停止，请检查配置与私有状态目录", flush=True)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
