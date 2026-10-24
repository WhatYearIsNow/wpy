# -*- coding: utf-8 -*-
"""路径与全局常量。代码与 data/ 分离，升级脚本不碰数据。"""
import os
import sys


def _default_base_dir():
    """源码运行使用项目目录；安装运行使用用户可写的数据目录。"""
    source_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if (os.path.exists(os.path.join(source_dir, ".git"))
            or os.path.isfile(os.path.join(source_dir, "pyproject.toml"))):
        return source_dir
    if sys.platform == "win32":
        user_dir = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    elif sys.platform == "darwin":
        user_dir = os.path.expanduser("~/Library/Application Support")
    else:
        user_dir = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(user_dir, "wpy")


BASE_DIR = _default_base_dir()

# data/ 只放「不可重建」的东西：数据库、图片、关注列表、运行状态。
DATA_DIR = os.environ.get("WPY_DATA_DIR") or os.path.join(BASE_DIR, "data")
# 生成物（看板页、热榜、导出）单独放 build/，随时可重建，清理时不会误删数据。
BUILD_DIR = os.environ.get("WPY_BUILD_DIR") or os.path.join(BASE_DIR, "build")
# 抓取日志单独放 logs/，它会长到几十 MB，不该和数据库混在一起。
LOG_DIR = os.environ.get("WPY_LOG_DIR") or os.path.join(BASE_DIR, "logs")
# lab.html 的源数据（人工整理的实验手册内容）。
PAGES_DIR = os.environ.get("WPY_PAGES_DIR") or os.path.join(BASE_DIR, "pages")

for _d in (DATA_DIR, BUILD_DIR, LOG_DIR, PAGES_DIR):
    os.makedirs(_d, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "sitewide.db")
LOG_PATH = os.path.join(LOG_DIR, "抓取日志.txt")
CRED_PATH = os.path.join(DATA_DIR, "凭据.json")
IMG_DIR = os.path.join(DATA_DIR, "img")
RUN_STATE_PATH = os.path.join(DATA_DIR, "运行状态.txt")

CSV_PATH = os.path.join(BUILD_DIR, "全站帖子.csv")
JSON_PATH = os.path.join(BUILD_DIR, "全站帖子_raw.json")
HTML_PATH = os.path.join(BUILD_DIR, "全站看板.html")
HOT_PATH = os.path.join(BUILD_DIR, "hot.html")
LAB_PATH = os.path.join(BUILD_DIR, "lab.html")
# 图标等静态资源与生成物同目录，由看板服务直接读
ASSET_DIR = BUILD_DIR

# 结构化日志：JSON lines 格式，供日志系统解析
METRICS_JSON_PATH = os.path.join(LOG_DIR, "结构化日志.jsonl")
ASSET_DIR = BUILD_DIR

MAIN_API = "https://api.twt.edu.cn/api/"
LAKE_API = "https://qnhd.twt.edu.cn/api/v1/f/"
PIC_API = "https://qnhdpic.twt.edu.cn/download/"
APP_KEY = os.environ.get("WPY_APP_KEY", "banana")
# 仅联网登录需要 API 密钥；浏览已有数据和生成页面无需配置。
APP_SECRET = os.environ.get("WPY_APP_SECRET", "")

PAGE_SIZE = 20
COMMENT_PAGE_SIZE = 20
MAX_RETRY = 3
FUSE_LIMIT = 12
EMPTY_STREAK_STOP = 3
MAX_GAP_WORTH_REPORT = 5
MAX_CONCURRENT = 6
TIMEOUT = 30

TARGET_QPS = 10.0
MIN_QPS = 1.0
THROTTLE_BACKOFF = 30
REQUEST_INTERVAL = 0.0

DEFAULT_TYPE_NAMES = {
    1: "校务专区", 2: "青年湖底", 3: "学习新知", 4: "交友恋爱",
    5: "二次元", 6: "摆摊", 10: "Bugs反馈", 13: "美食专区",
}
RECOMMEND_TYPE = 0

# 看板服务
DEFAULT_PORT = int(os.environ.get("WPY_PORT", "8765"))
DEFAULT_HOST = os.environ.get("WPY_HOST", "127.0.0.1")
# 设置后，打开网页需带 ?token= 或 Cookie；公网部署建议设置
BOARD_TOKEN = os.environ.get("WPY_BOARD_TOKEN", "")
