# -*- coding: utf-8 -*-
"""配置数据模型与验证。

提供结构化的配置访问和启动时校验，避免隐式错误。
所有配置项均可通过环境变量覆盖，保留与 config.py 的完全兼容。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ServerConfig:
    """看板服务配置。"""
    host: str
    port: int
    token: str

    @classmethod
    def from_env(cls) -> "ServerConfig":
        host = os.environ.get("WPY_HOST", "127.0.0.1")
        port = int(os.environ.get("WPY_PORT", "8765"))
        token = os.environ.get("WPY_BOARD_TOKEN", "")
        return cls(host=host, port=port, token=token)

    def validate(self) -> list[str]:
        """返回验证错误列表（空列表表示通过）。"""
        errors = []
        try:
            port = int(self.port)
            if port < 1 or port > 65535:
                errors.append(f"端口 {port} 超出范围 (1-65535)")
        except ValueError:
            errors.append(f"端口 '{self.port}' 不是有效数字")
        if self.host not in ("0.0.0.0", "::"):
            # 非公网绑定：不需要 token
            pass
        else:
            if not self.token:
                errors.append(
                    "公网绑定 (0.0.0.0/::) 时必须设置 WPY_BOARD_TOKEN 环境变量"
                )
        return errors


@dataclass(frozen=True)
class PathConfig:
    """数据/构建/日志路径配置。"""
    data_dir: str
    build_dir: str
    log_dir: str
    pages_dir: str
    db_path: str
    log_path: str
    cred_path: str
    img_dir: str
    run_state_path: str
    csv_path: str
    json_path: str
    html_path: str
    hot_path: str
    lab_path: str
    asset_dir: str

    @classmethod
    def from_env(cls, base_dir: str) -> "PathConfig":
        data_dir = os.environ.get("WPY_DATA_DIR") or os.path.join(base_dir, "data")
        build_dir = os.environ.get("WPY_BUILD_DIR") or os.path.join(base_dir, "build")
        log_dir = os.environ.get("WPY_LOG_DIR") or os.path.join(base_dir, "logs")
        pages_dir = os.environ.get("WPY_PAGES_DIR") or os.path.join(base_dir, "pages")

        for d in (data_dir, build_dir, log_dir, pages_dir):
            os.makedirs(d, exist_ok=True)

        return cls(
            data_dir=data_dir,
            build_dir=build_dir,
            log_dir=log_dir,
            pages_dir=pages_dir,
            db_path=os.path.join(data_dir, "sitewide.db"),
            log_path=os.path.join(log_dir, "抓取日志.txt"),
            cred_path=os.path.join(data_dir, "凭据.json"),
            img_dir=os.path.join(data_dir, "img"),
            run_state_path=os.path.join(data_dir, "运行状态.txt"),
            csv_path=os.path.join(build_dir, "全站帖子.csv"),
            json_path=os.path.join(build_dir, "全站帖子_raw.json"),
            html_path=os.path.join(build_dir, "全站看板.html"),
            hot_path=os.path.join(build_dir, "hot.html"),
            lab_path=os.path.join(build_dir, "lab.html"),
            asset_dir=build_dir,
        )

    def validate(self) -> list[str]:
        errors = []
        if not os.path.isdir(self.data_dir):
            errors.append(f"数据目录不存在: {self.data_dir}")
        return errors


@dataclass(frozen=True)
class CrawlerConfig:
    """抓取流水线配置。"""
    page_size: int
    comment_page_size: int
    max_retry: int
    fuse_limit: int
    empty_streak_stop: int
    max_gap_worth_report: int
    max_concurrent: int
    timeout: int
    target_qps: float
    min_qps: float
    throttle_backoff: int

    @classmethod
    def from_env(cls) -> "CrawlerConfig":
        return cls(
            page_size=int(os.environ.get("WPY_PAGE_SIZE", "20")),
            comment_page_size=int(os.environ.get("WPY_COMMENT_PAGE_SIZE", "20")),
            max_retry=int(os.environ.get("WPY_MAX_RETRY", "3")),
            fuse_limit=int(os.environ.get("WPY_FUSE_LIMIT", "12")),
            empty_streak_stop=int(os.environ.get("WPY_EMPTY_STREAK_STOP", "3")),
            max_gap_worth_report=int(os.environ.get("WPY_MAX_GAP_WORTH_REPORT", "5")),
            max_concurrent=int(os.environ.get("WPY_MAX_CONCURRENT", "6")),
            timeout=int(os.environ.get("WPY_TIMEOUT", "30")),
            target_qps=float(os.environ.get("WPY_TARGET_QPS", "10.0")),
            min_qps=float(os.environ.get("WPY_MIN_QPS", "1.0")),
            throttle_backoff=int(os.environ.get("WPY_THROTTLE_BACKOFF", "30")),
        )

    def validate(self) -> list[str]:
        errors = []
        if self.page_size < 1 or self.page_size > 1000:
            errors.append(f"page_size {self.page_size} 超出范围 (1-1000)")
        if self.comment_page_size < 1 or self.comment_page_size > 500:
            errors.append(f"comment_page_size {self.comment_page_size} 超出范围 (1-500)")
        if self.max_retry < 1 or self.max_retry > 10:
            errors.append(f"max_retry {self.max_retry} 超出范围 (1-10)")
        if self.fuse_limit < 1 or self.fuse_limit > 100:
            errors.append(f"fuse_limit {self.fuse_limit} 超出范围 (1-100)")
        if self.max_concurrent < 1 or self.max_concurrent > 32:
            errors.append(f"max_concurrent {self.max_concurrent} 超出范围 (1-32)")
        if self.target_qps < 0:
            errors.append(f"target_qps {self.target_qps} 不能为负数")
        if self.min_qps < 0:
            errors.append(f"min_qps {self.min_qps} 不能为负数")
        if self.target_qps > 0 and self.min_qps > self.target_qps:
            errors.append(f"min_qps ({self.min_qps}) 不能大于 target_qps ({self.target_qps})")
        return errors


@dataclass(frozen=True)
class AuthConfig:
    """认证配置。"""
    app_key: str
    app_secret: str
    main_api: str
    lake_api: str
    pic_api: str

    @classmethod
    def from_env(cls) -> "AuthConfig":
        # APP_KEY 也支持环境变量覆盖（默认 "banana"）
        app_key = os.environ.get("WPY_APP_KEY", "banana")
        app_secret = os.environ.get("WPY_APP_SECRET", "")
        return cls(
            app_key=app_key,
            app_secret=app_secret,
            main_api="https://api.twt.edu.cn/api/",
            lake_api="https://qnhd.twt.edu.cn/api/v1/f/",
            pic_api="https://qnhdpic.twt.edu.cn/download/",
        )

    def validate(self) -> list[str]:
        errors = []
        if not self.app_key:
            errors.append("WPY_APP_KEY 不能为空")
        if not self.app_secret:
            errors.append("WPY_APP_SECRET 不能为空（或设置环境变量）")
        return errors


def validate_all(require_auth: bool = False) -> list[str]:
    """验证本地配置；需要联网认证时再检查 API 密钥。"""
    errors = []
    configs = [ServerConfig.from_env(), CrawlerConfig.from_env()]
    if require_auth:
        configs.append(AuthConfig.from_env())
    for cfg in configs:
        errors.extend(cfg.validate())
    return errors


def log_warnings(errors: list[str]) -> None:
    """打印验证警告（非致命错误不阻止运行）。"""
    for err in errors:
        if err.startswith("警告:"):
            print(f"  ⚠ 配置警告: {err}")
        elif err.startswith("公网绑定"):
            # 这是致命错误
            print(f"  ✗ 配置错误: {err}", flush=True)
        # 其他非致命警告也打印
