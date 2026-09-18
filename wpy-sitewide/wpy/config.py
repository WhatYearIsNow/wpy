# -*- coding: utf-8 -*-
"""路径与全局常量。代码与 data/ 分离，升级脚本不碰数据。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # wpy-sitewide/
DATA_DIR = os.environ.get("WPY_DATA_DIR") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "sitewide.db")
LOG_PATH = os.path.join(DATA_DIR, "抓取日志.txt")
CRED_PATH = os.path.join(DATA_DIR, "凭据.json")
IMG_DIR = os.path.join(DATA_DIR, "img")
CSV_PATH = os.path.join(DATA_DIR, "全站帖子.csv")
JSON_PATH = os.path.join(DATA_DIR, "全站帖子_raw.json")
HTML_PATH = os.path.join(DATA_DIR, "全站看板.html")
RUN_STATE_PATH = os.path.join(DATA_DIR, "运行状态.txt")

MAIN_API = "https://api.twt.edu.cn/api/"
LAKE_API = "https://qnhd.twt.edu.cn/api/v1/f/"
PIC_API = "https://qnhdpic.twt.edu.cn/download/"
APP_KEY = "banana"
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
