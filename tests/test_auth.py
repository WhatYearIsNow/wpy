"""认证配置必须在任何网络请求或账号密码提示之前验证。"""

import base64
from types import SimpleNamespace

import pytest

from wpy import auth
from wpy.http import FatalError


@pytest.mark.parametrize("interactive", [True, False])
def test_missing_api_secret_fails_before_credentials_or_network(monkeypatch, interactive):
    monkeypatch.delenv("WPY_APP_SECRET", raising=False)
    monkeypatch.setattr(auth, "http_json", lambda *a, **kw: pytest.fail("不应发起网络请求"))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("不应先询问账号"))
    monkeypatch.setattr(
        auth,
        "os",
        SimpleNamespace(
            environ=auth.os.environ,
            path=SimpleNamespace(exists=lambda *a: pytest.fail("不应读取本地凭据")),
        ),
    )

    with pytest.raises(FatalError, match="WPY_APP_SECRET"):
        auth.get_token(interactive=interactive)


def test_login_uses_configured_ticket(monkeypatch):
    monkeypatch.setenv("WPY_APP_KEY", "test-app")
    monkeypatch.setenv("WPY_APP_SECRET", "test-secret")
    calls = []

    def fake_http_json(url, *args):
        calls.append((url, args))
        if len(calls) == 1:
            return {"error_code": 0, "result": {"token": "main-token"}}
        return {"code": 200, "data": {"token": "forum-token"}}

    monkeypatch.setattr(auth, "http_json", fake_http_json)
    assert auth.login("test-account", "test-password") == "forum-token"
    assert len(calls) == 2
    headers = calls[0][1][1]
    assert base64.b64decode(headers["ticket"]).decode() == "test-app.test-secret"


def test_direct_login_without_api_secret_never_sends_request(monkeypatch):
    monkeypatch.delenv("WPY_APP_SECRET", raising=False)
    monkeypatch.setattr(auth, "http_json", lambda *a, **kw: pytest.fail("不应发起网络请求"))
    with pytest.raises(FatalError, match="WPY_APP_SECRET"):
        auth.login("test-account", "test-password")
