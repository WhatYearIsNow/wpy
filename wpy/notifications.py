"""单账号消息轮询及持久化发件箱；与看板会话、抓取数据库相互独立。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
import uuid

from .config import LAKE_API
from .http import request_json, Throttled
from .notification_mail import NotificationError, render_digest


INTERVAL = 300
PAGE_SIZE = 20
MAX_PAGES = 50
MAX_EVENTS_PER_MAIL = 100


class AuthenticationError(NotificationError):
    pass


class RateLimited(NotificationError):
    def __init__(self, delay=900):
        super().__init__("论坛接口限流，已延后检查")
        self.delay = max(INTERVAL, delay)


class ForumClient:
    def __init__(self, token_factory, *, request=request_json, sleep=time.sleep,
                 max_pages=MAX_PAGES):
        self.token_factory = token_factory
        self.request = request
        self.sleep = sleep
        self.max_pages = max_pages
        self.token = None

    def _page(self, kind, page):
        return self._request_page(kind, page, renew=True)

    def _request_page(self, kind, page, *, renew):
        if not self.token:
            try:
                self.token = self.token_factory()
                if not self.token:
                    raise ValueError("missing token")
            except Exception:
                raise AuthenticationError("论坛登录失败，请检查个人账号和 API 登录配置") from None
        url = LAKE_API + "message/" + kind + "?" + urlencode(
            {"page": page, "page_size": PAGE_SIZE})
        try:
            response = self.request(url, token=self.token, timeout=30, throttle=False,
                                    max_bytes=2 * 1024 * 1024)
        except Throttled as error:
            raise RateLimited(getattr(error, "retry_after", 0) or 900) from None
        except HTTPError as error:
            error.close()
            if error.code == 401 and renew:
                self.token = None
                return self._request_page(kind, page, renew=False)
            if error.code in (401, 403):
                self.token = None
                raise AuthenticationError("论坛登录或访问权限已失效，请重新配置认证") from None
            raise NotificationError("论坛请求失败，本轮未更新消息基线") from None
        except (OSError, ValueError, RuntimeError):
            raise NotificationError("论坛请求失败，本轮未更新消息基线") from None
        if not isinstance(response, dict):
            raise NotificationError("论坛响应格式异常，本轮未更新消息基线")
        code = response.get("code")
        if code == 401 and renew:
            self.token = None
            return self._request_page(kind, page, renew=False)
        if code in (401, 403):
            self.token = None
            raise AuthenticationError("论坛登录或访问权限已失效，请重新配置认证")
        if code == 429:
            raise RateLimited()
        data = response.get("data")
        if code != 200 or not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise NotificationError("论坛消息响应异常，本轮未更新消息基线")
        return data["list"]

    def snapshot(self):
        # 不用未读数作唯一依据；官方客户端读消息后，未读数可能不变而内容已变。
        result = []
        observed = set()
        for kind in ("likes", "floors"):
            previous_pages = set()
            for page in range(1, self.max_pages + 1):
                if result or page > 1 or kind == "floors":
                    self.sleep(1)
                rows = self._page(kind, page)
                signature = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).digest()
                if rows and signature in previous_pages:
                    raise NotificationError("论坛重复返回同一消息页，本轮未更新消息基线")
                previous_pages.add(signature)
                for row in rows:
                    event = normalize(kind, row)
                    key = (event["kind"], event["target_id"])
                    if key in observed:
                        # 页码在抓取途中移动，下一轮重新取，不能提交不完整的基线。
                        raise NotificationError("消息分页在检查期间发生变化，将在下轮重试")
                    observed.add(key)
                    result.append(event)
                if len(rows) < PAGE_SIZE:
                    break
            else:
                raise NotificationError("消息分页达到上限，请提高 --max-pages 后重试；基线未更新")
        return result


def _number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("invalid integer")
    result = int(value)
    if result < (1 if positive else 0):
        raise ValueError("invalid integer")
    return result


def _text(value, limit=1000):
    return str(value or "")[:limit]


def normalize(source, row):
    try:
        if not isinstance(row, dict):
            raise ValueError("invalid message")
        kind_type = _number(row["type"])
        if kind_type not in (0, 1):
            raise ValueError("unknown message type")
        post = row.get("post") or {}
        floor = row.get("floor") or {}
        event = {"post_id": _number(post.get("id") or floor.get("post_id"), positive=True),
                 "title": _text(post.get("title"), 240)}
        if source == "likes":
            target = post if kind_type == 0 else floor
            event.update(kind="post_like" if kind_type == 0 else "floor_like",
                         target_id=_number(target["id"], positive=True),
                         likes=_number(target["like_count"]),
                         content=_text(target.get("content")) if kind_type == 1 else "")
        else:
            original = row.get("to_floor") or {}
            event.update(kind="post_comment" if kind_type == 0 else "comment_reply",
                         target_id=_number(floor["id"], positive=True), likes=0,
                         author=_text(floor.get("nickname"), 80),
                         time=_text(floor.get("created_at"), 80),
                         content=_text(floor.get("content")) or
                         ("[图片评论]" if floor.get("image_url") else "[内容已不可用]"),
                         original=_text(original.get("content"), 400))
        return event
    except (KeyError, TypeError, ValueError, AttributeError):
        raise NotificationError("消息字段异常，本轮未更新消息基线") from None


@contextmanager
def worker_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    path = directory / "worker.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.chmod(path, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise NotificationError("已有通知任务使用此状态目录，请勿重复启动") from None
        yield
    finally:
        os.close(fd)


class NotificationStore:
    def __init__(self, directory, account, recipient, *, now=time.time):
        self.now = now
        self.path = Path(directory) / "notifications.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS seen (
                kind TEXT NOT NULL, target_id INTEGER NOT NULL, likes INTEGER NOT NULL,
                PRIMARY KEY (kind, target_id));
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, text TEXT NOT NULL, html TEXT NOT NULL,
                message_id TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                due REAL NOT NULL, sent REAL);
        """)
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute("SELECT value FROM meta WHERE key='key'").fetchone()
                key = row[0] if row else secrets.token_hex(32)
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('key', ?)", (key,))
                identity = hmac.new(bytes.fromhex(key), json.dumps(
                    [account.strip(), recipient], ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
                row = self.db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
                if row and not hmac.compare_digest(row[0], identity):
                    raise NotificationError("此通知状态属于另一账号或收件人，请使用独立状态目录")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES ('identity', ?)", (identity,))
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def ingest(self, events, base_url=""):
        """去重基线与发件箱在同一个事务中落盘；首次启动仅建立基线。"""
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            initialized = self.db.execute("SELECT 1 FROM meta WHERE key='initialized'").fetchone()
            changes = []
            for event in events:
                row = self.db.execute("SELECT likes FROM seen WHERE kind=? AND target_id=?",
                                      (event["kind"], event["target_id"])).fetchone()
                liked = event["kind"] in ("post_like", "floor_like")
                increase = max(0, event["likes"] - row[0]) if liked and row else None
                if initialized and ((liked and (increase or (row is None and event["likes"])))
                                    or (not liked and row is None)):
                    changes.append(dict(event, increase=increase))
                self.db.execute("INSERT INTO seen VALUES (?, ?, ?) ON CONFLICT(kind, target_id) "
                                "DO UPDATE SET likes=excluded.likes",
                                (event["kind"], event["target_id"], event["likes"]))
            pending = self.db.execute("SELECT count(*) FROM outbox WHERE sent IS NULL").fetchone()[0]
            if pending + (len(changes) + MAX_EVENTS_PER_MAIL - 1) // MAX_EVENTS_PER_MAIL > 1000:
                raise NotificationError("待发邮件积压过多，暂停更新消息基线")
            for start in range(0, len(changes), MAX_EVENTS_PER_MAIL):
                subject, text, body = render_digest(changes[start:start + MAX_EVENTS_PER_MAIL], base_url)
                key = uuid.uuid4().hex
                self.db.execute("INSERT INTO outbox (id,subject,text,html,message_id,due) VALUES (?,?,?,?,?,?)",
                                (key, subject, text, body, f"<{key}@wpy.local>", self.now()))
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('initialized', '1')")
            self.db.execute("DELETE FROM outbox WHERE sent IS NOT NULL AND sent < ?",
                            (self.now() - 30 * 86400,))
            return {"baseline": not bool(initialized), "new": len(changes)}

    def deliver(self, sender, *, limit=10):
        sent = failed = 0
        rows = self.db.execute("SELECT * FROM outbox WHERE sent IS NULL AND due <= ? "
                               "ORDER BY due,id LIMIT ?", (self.now(), limit)).fetchall()
        for row in rows:
            try:
                result = sender(row["subject"], row["text"], row["html"], row["message_id"])
                if result is False:
                    raise NotificationError("邮件发送失败")
            except Exception:
                attempts = row["attempts"] + 1
                delay = min(3600, INTERVAL * 2 ** min(attempts - 1, 4))
                with self.db:
                    self.db.execute("UPDATE outbox SET attempts=?,due=? WHERE id=?",
                                    (attempts, self.now() + delay, row["id"]))
                failed += 1
                break  # SMTP 不通时，本轮不继续逐封重试。
            else:
                with self.db:
                    self.db.execute("UPDATE outbox SET sent=? WHERE id=?", (self.now(), row["id"]))
                sent += 1
        pending = self.db.execute("SELECT count(*) FROM outbox WHERE sent IS NULL").fetchone()[0]
        return {"sent": sent, "failed": failed, "pending": pending}


def run_cycle(client, store, sender, base_url=""):
    # 先补发队列；论坛接口不可用时也不能阻塞已持久化的邮件。
    delivery = store.deliver(sender)
    snapshot = client.snapshot()
    result = store.ingest(snapshot, base_url)
    if not delivery["failed"]:
        extra = store.deliver(sender)
        delivery = {"sent": delivery["sent"] + extra["sent"],
                    "failed": extra["failed"], "pending": extra["pending"]}
    return dict(result, **delivery)
