# -*- coding: utf-8 -*-
"""登录与凭据。

HTTP 请求统一走 wpy.http；本模块只负责账号/token/本地凭据。
"""

import base64
import getpass
import json
import os
import urllib.parse

from .config import (
    CRED_PATH,
    DATA_DIR,
    LAKE_API,
    MAIN_API,
)
from .config_models import AuthConfig
from .http import FatalError, request_json  # noqa: F401  FatalError 兼容再导出


def _ticket():
    """联网登录前验证配置，避免以空密钥发起请求或先询问账号密码。"""
    config = AuthConfig.from_env()
    errors = config.validate()
    if errors:
        raise FatalError("API 登录配置错误：" + "；".join(errors))
    return base64.b64encode((config.app_key + "." + config.app_secret).encode("utf-8")).decode(
        "ascii"
    )


def http_json(url, method="GET", headers=None, body=None, timeout=30):
    """兼容入口：认证请求不走全局限速，避免登录被抓取限速拖累。"""
    return request_json(
        url, token=None, timeout=timeout, throttle=False, method=method, headers=headers, body=body
    )


def login(account, password):
    """主账号登录拿主 token，再换成湖底 token。"""
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "DOMAIN": "weipeiyang.twt.edu.cn",
        "ticket": _ticket(),
    }
    body = urllib.parse.urlencode({"account": account, "password": password})
    r = http_json(MAIN_API + "auth/common", "POST", headers, body)
    if r.get("error_code") != 0:
        raise RuntimeError("主账号登录失败：" + str(r.get("message") or r))
    main_token = r["result"]["token"]
    r2 = http_json(LAKE_API + "auth/token?token=" + urllib.parse.quote(main_token))
    if r2.get("code") != 200:
        raise RuntimeError("获取论坛 token 失败：" + str(r2))
    return r2["data"]["token"]


def harden_file_permissions(path):
    """Windows 下收紧 ACL；其他平台忽略。"""
    if os.name != "nt":
        return
    try:
        os.system(
            'icacls "%s" /inheritance:r /grant:r "%s:(R)" >nul 2>&1'
            % (path, os.environ.get("USERNAME", ""))
        )
    except Exception:
        pass


def _save_cred(account, password):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CRED_PATH, "w", encoding="utf-8") as f:
        json.dump({"account": account, "password": password}, f, ensure_ascii=False, indent=2)
    harden_file_permissions(CRED_PATH)


def get_token(interactive=True):
    """优先级：环境变量 > 本地凭据文件 >（可选）交互输入并保存。"""
    _ticket()
    account = os.environ.get("WPY_ACCOUNT")
    password = os.environ.get("WPY_PASSWORD")
    if account and password:
        return login(account, password)

    if os.path.exists(CRED_PATH):
        try:
            with open(CRED_PATH, encoding="utf-8") as f:
                cred = json.load(f)
            return login(cred["account"], cred["password"])
        except Exception as e:
            print("读取凭据失败：%s" % e)

    if not interactive:
        raise FatalError(
            "缺少凭据：请设置 WPY_ACCOUNT / WPY_PASSWORD，"
            "或提供 data/凭据.json（服务器定时任务请用环境变量）"
        )

    print("首次使用需要登录微北洋（只读，不会点赞/发帖）。")
    account = input("学号：").strip()
    password = getpass.getpass("密码（输入不显示）：")
    if not account or not password:
        raise FatalError("账号或密码为空")
    try:
        token = login(account, password)
    except Exception as e:
        raise FatalError("登录失败：%s" % e)
    if interactive:
        save = input("是否保存凭据到本地以免下次再输？(y/N)：").strip().lower()
        if save == "y":
            _save_cred(account, password)
            print("已保存到 %s（已 gitignore，勿外传）" % CRED_PATH)
    return token


def do_logout():
    if os.path.exists(CRED_PATH):
        os.remove(CRED_PATH)
        print("已清除本地凭据。")
    else:
        print("没有已保存的凭据。")


def do_whoami():
    account = os.environ.get("WPY_ACCOUNT")
    if account:
        print("当前账号来源：环境变量 WPY_ACCOUNT = %s" % account)
        return
    if os.path.exists(CRED_PATH):
        try:
            with open(CRED_PATH, encoding="utf-8") as f:
                cred = json.load(f)
            print("当前账号来源：data/凭据.json，账号 = %s" % cred.get("account"))
        except Exception as e:
            print("凭据文件损坏：" + str(e))
        return
    print("尚未配置账号。")
