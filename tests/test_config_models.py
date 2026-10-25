# -*- coding: utf-8 -*-
"""wpy.config_models 的单元测试。"""
import os
import pytest
from wpy.config_models import (
    ServerConfig,
    PathConfig,
    CrawlerConfig,
    AuthConfig,
    validate_all,
)


class TestServerConfig:
    """测试看板服务配置验证。"""

    def _make(self, host="127.0.0.1", port=8765, token=""):
        return ServerConfig(host=host, port=port, token=token)

    def test_valid_private_host(self):
        cfg = self._make(host="127.0.0.1", port=8765, token="")
        assert cfg.validate() == []

    def test_valid_public_host_with_token(self):
        cfg = self._make(host="0.0.0.0", port=8765, token="secret")
        assert cfg.validate() == []

    def test_public_host_no_token(self):
        cfg = self._make(host="0.0.0.0", port=8765, token="")
        assert cfg.validate() == []

    def test_public_host_ipv6_no_token(self):
        cfg = self._make(host="::", port=8765, token="")
        assert cfg.validate() == []

    def test_port_zero(self):
        cfg = self._make(port=0)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "超出范围" in errors[0]

    def test_port_too_high(self):
        cfg = self._make(port=65536)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "超出范围" in errors[0]

    def test_port_negative(self):
        cfg = self._make(port=-1)
        errors = cfg.validate()
        assert len(errors) == 1
        assert "超出范围" in errors[0]

    def test_port_min(self):
        cfg = self._make(port=1)
        assert cfg.validate() == []

    def test_port_max(self):
        cfg = self._make(port=65535)
        assert cfg.validate() == []

    def test_from_env_defaults(self, monkeypatch):
        # 清除可能干扰的环境变量
        for key in ("WPY_HOST", "WPY_PORT", "WPY_BOARD_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        cfg = ServerConfig.from_env()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8765
        assert cfg.token == ""

    def test_from_env_custom(self, monkeypatch):
        monkeypatch.setenv("WPY_HOST", "0.0.0.0")
        monkeypatch.setenv("WPY_PORT", "9999")
        monkeypatch.setenv("WPY_BOARD_TOKEN", "mytoken")
        cfg = ServerConfig.from_env()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9999
        assert cfg.token == "mytoken"


class TestCrawlerConfig:
    """测试抓取配置验证。"""

    def _make(self, **kwargs):
        defaults = dict(
            page_size=20,
            comment_page_size=20,
            max_retry=3,
            fuse_limit=12,
            empty_streak_stop=3,
            max_gap_worth_report=5,
            max_concurrent=6,
            timeout=30,
            target_qps=10.0,
            min_qps=1.0,
            throttle_backoff=30,
        )
        defaults.update(kwargs)
        return CrawlerConfig(**defaults)

    def test_valid_defaults(self):
        assert self._make().validate() == []

    def test_page_size_too_small(self):
        errors = self._make(page_size=0).validate()
        assert len(errors) == 1
        assert "page_size" in errors[0]

    def test_page_size_too_large(self):
        errors = self._make(page_size=1001).validate()
        assert len(errors) == 1
        assert "page_size" in errors[0]

    def test_page_size_valid_min(self):
        assert self._make(page_size=1).validate() == []

    def test_page_size_valid_max(self):
        assert self._make(page_size=1000).validate() == []

    def test_comment_page_size_too_small(self):
        errors = self._make(comment_page_size=0).validate()
        assert len(errors) == 1
        assert "comment_page_size" in errors[0]

    def test_comment_page_size_too_large(self):
        errors = self._make(comment_page_size=501).validate()
        assert len(errors) == 1
        assert "comment_page_size" in errors[0]

    def test_max_retry_bounds(self):
        assert self._make(max_retry=1).validate() == []
        assert self._make(max_retry=10).validate() == []
        assert self._make(max_retry=0).validate()
        assert self._make(max_retry=11).validate()

    def test_fuse_limit_bounds(self):
        assert self._make(fuse_limit=1).validate() == []
        assert self._make(fuse_limit=100).validate() == []
        assert self._make(fuse_limit=0).validate()
        assert self._make(fuse_limit=101).validate()

    def test_max_concurrent_bounds(self):
        assert self._make(max_concurrent=1).validate() == []
        assert self._make(max_concurrent=32).validate() == []
        assert self._make(max_concurrent=0).validate()
        assert self._make(max_concurrent=33).validate()

    def test_target_qps_negative(self):
        errors = self._make(target_qps=-1.0).validate()
        assert len(errors) == 1
        assert "target_qps" in errors[0]

    def test_min_qps_negative(self):
        errors = self._make(min_qps=-1.0).validate()
        assert len(errors) == 1
        assert "min_qps" in errors[0]

    def test_min_qps_greater_than_target(self):
        errors = self._make(target_qps=5.0, min_qps=10.0).validate()
        assert len(errors) == 1
        assert "min_qps" in errors[0]
        assert "target_qps" in errors[0]

    def test_target_qps_zero_ok(self):
        assert self._make(target_qps=0.0, min_qps=0.0).validate() == []


class TestAuthConfig:
    """测试认证配置验证。"""

    def test_valid_custom_secret(self):
        cfg = AuthConfig(
            app_key="banana",
            app_secret="custom_secret_123",
            main_api="", lake_api="", pic_api="",
        )
        assert cfg.validate() == []

    def test_empty_app_key(self):
        cfg = AuthConfig(app_key="", app_secret="secret", main_api="", lake_api="", pic_api="")
        errors = cfg.validate()
        assert len(errors) == 1
        assert "WPY_APP_KEY" in errors[0]

    def test_empty_app_secret(self):
        cfg = AuthConfig(app_key="banana", app_secret="", main_api="", lake_api="", pic_api="")
        errors = cfg.validate()
        assert len(errors) == 1
        assert "WPY_APP_SECRET" in errors[0]

    def test_missing_secret_has_no_builtin_fallback(self, monkeypatch):
        monkeypatch.delenv("WPY_APP_SECRET", raising=False)
        cfg = AuthConfig.from_env()
        assert cfg.app_secret == ""
        assert any("WPY_APP_SECRET" in error for error in cfg.validate())

    def test_secret_from_environment(self, monkeypatch):
        monkeypatch.setenv("WPY_APP_SECRET", "test-api-secret")
        cfg = AuthConfig.from_env()
        assert cfg.app_secret == "test-api-secret"


class TestValidateAll:
    """测试综合验证。"""

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch):
        for key in ("WPY_APP_KEY", "WPY_APP_SECRET", "WPY_BOARD_TOKEN", "WPY_HOST", "WPY_PORT"):
            monkeypatch.delenv(key, raising=False)

    def test_all_valid(self, monkeypatch):
        monkeypatch.setenv("WPY_APP_SECRET", "custom_secret")
        errors = validate_all(require_auth=True)
        assert errors == []

    def test_local_commands_do_not_require_api_secret(self):
        assert validate_all() == []

    def test_authentication_requires_api_secret(self):
        errors = validate_all(require_auth=True)
        assert any("WPY_APP_SECRET" in error for error in errors)

    def test_public_host_no_token_in_all(self, monkeypatch):
        monkeypatch.setenv("WPY_HOST", "0.0.0.0")
        assert validate_all() == []
