"""看板个人会话。密码不保存，论坛 token 只在进程内存中保留。"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


SESSION_TTL = 6 * 60 * 60


@dataclass(repr=False)
class Session:
    principal: str
    token: str
    expires: float
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    personal: dict | None = None
    personal_at: float = 0
    retry_after: float = 0
    lock: object = field(default_factory=threading.Lock)


class Sessions:
    def __init__(self, data_dir, *, clock=time.monotonic, ttl=SESSION_TTL):
        self.clock = clock
        self.ttl = ttl
        self.lock = threading.Lock()
        self.items = {}
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key_path = self.data_dir / "board-identity.key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.key = key_path.read_bytes()
        else:
            self.key = secrets.token_bytes(32)
            with os.fdopen(fd, "wb") as stream:
                stream.write(self.key)
        if len(self.key) != 32:
            raise ValueError("看板会话密钥文件无效")

    def create(self, account, token):
        # 本地个人数据以带站点密钥的摘要区分，不将账号写入文件名或文件。
        principal = hmac.new(self.key, account.strip().encode(), hashlib.sha256).hexdigest()
        sid = secrets.token_urlsafe(32)
        session = Session(principal, token, self.clock() + self.ttl)
        with self.lock:
            self.items = {k: s for k, s in self.items.items() if s.expires > self.clock()}
            if len(self.items) >= 10000:
                raise ValueError("当前登录人数过多，请稍后再试")
            self.items[sid] = session
        return sid, session

    def get(self, sid):
        with self.lock:
            session = self.items.get(sid)
            if session and session.expires <= self.clock():
                del self.items[sid]
                return None
            return session

    def revoke(self, sid):
        with self.lock:
            self.items.pop(sid, None)

    def invalidate_personal(self, principal):
        with self.lock:
            for session in self.items.values():
                if session.principal == principal:
                    session.personal_at = 0
                    session.retry_after = 0

    def login_csrf(self):
        nonce = str(int(time.time())) + "." + secrets.token_urlsafe(24)
        sig = hmac.new(self.key, nonce.encode(), hashlib.sha256).hexdigest()
        return nonce + "." + sig

    def valid_login_csrf(self, value):
        try:
            timestamp, nonce, sig = value.split(".")
            age = time.time() - int(timestamp)
            expected = hmac.new(self.key, (timestamp + "." + nonce).encode(), hashlib.sha256).hexdigest()
            return 0 <= age < 600 and hmac.compare_digest(sig, expected)
        except (ValueError, TypeError):
            return False

    def follows_path(self, session):
        directory = self.data_dir / "board-users"
        directory.mkdir(mode=0o700, exist_ok=True)
        return str(directory / (session.principal + ".json"))
