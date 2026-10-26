# -*- coding: utf-8 -*-
"""统一 HTTP / 限速 / 重试 / 熔断层。

业务层不要再直接写 urllib / ssl / json.loads：
- 湖底 JSON API：api_get()
- 通用 JSON：request_json()
- 原始字节（图片等）：get_bytes()
- 认证 POST 表单：auth.http_json 走 request_json(method=..., body=...)

全局限速器单例 LIMITER：所有阶段共享，禁止改回 per-thread sleep。
"""

import json
from email.utils import parsedate_to_datetime
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import (
    FUSE_LIMIT,
    LAKE_API,
    MAX_RETRY,
    MIN_QPS,
    REQUEST_INTERVAL,
    TARGET_QPS,
    THROTTLE_BACKOFF,
    TIMEOUT,
)
from .runlog import log
from .metrics import metrics


class FatalError(RuntimeError):
    """不应重试的错误（token 失效等）。"""


class BadRequest(RuntimeError):
    """接口返回 code=400 参数错误：对同批请求是系统性的，应立刻熔断。"""


class Throttled(Exception):
    """服务端限流（429 / 返回体提示请求过多）。触发全局降速。"""

    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


class FuseTripped(RuntimeError):
    """熔断信号：不是致命错误，只是让工作线程干净退出。"""


class Fuse:
    """熔断器：连续失败达上限时置位停止标志，让所有线程都能干净退出。

    设计要点（踩过坑）：
    - 计数必须是「跨线程累计的连续失败」而不是每个线程各自算，
      否则 N 个线程各失败几次就会谎报「连续失败 N×k 次」。
    - 熔断不应该在异常里抛错（那会让计数继续叠加），而是置一个标志位，
      调用方看到标志位就主动停下。
    - 参数类错误（400）通常是「所有请求都会失败」的系统性问题，
      此时立刻熔断，不要白跑几百次。
    """

    def __init__(self, limit=FUSE_LIMIT):
        self.limit = limit
        self.count = 0
        self.stopped = False
        self.reason = None
        self.lock = threading.Lock()

    def ok(self):
        with self.lock:
            self.count = 0

    def fail(self, err):
        with self.lock:
            # 已熔断后不再累加计数：以前会一路涨到 88，日志里看着像
            # 「连续失败了 88 次」，实际上 12 次就停了，容易误导排查。
            if self.stopped:
                return True
            self.count += 1
            if self.count >= self.limit:
                self.stopped = True
                self.reason = "连续失败 %d 次，已熔断停止（最后错误：%s）" % (
                    self.count,
                    str(err)[:160],
                )
            return self.stopped

    def check(self):
        """供工作线程在每轮开始前调用；已熔断则抛出以结束该线程。"""
        if self.stopped:
            raise FuseTripped(self.reason or "已熔断")

    def force_stop(self, reason):
        with self.lock:
            if not self.stopped:
                self.stopped = True
                self.reason = reason


class RateLimiter:
    """全局速率限制器：所有线程共享一个配额，保证整体 QPS 不超标。

    为什么需要它：以前是「每个线程各自 sleep(间隔)」，于是并发数一提高，
    实际 QPS 就成倍上涨——并发 8 会变成 8 QPS，这等于悄悄突破了限速约定。
    改成全局令牌桶后，不管开多少线程，整体速率都锁在 target_qps。

    同时具备「被限流就退让」的能力：遇到 429 时调用 penalize()，
    速率立刻砍半并暂停一段时间，而不是继续硬撞。
    """

    def __init__(self, qps=TARGET_QPS):
        self.target = max(0.0, qps)
        self.current = self.target
        self.lock = threading.Lock()
        self.next_slot = time.time()
        self.paused_until = 0.0
        self.hit_429 = 0
        self._log_every = 20
        self._served = 0

    def acquire(self):
        """取一个请求配额；必要时阻塞等待。qps<=0 表示不限速。"""
        if self.target <= 0:
            return
        with self.lock:
            now = time.time()
            if now < self.paused_until:
                wait = self.paused_until - now
            else:
                interval = 1.0 / max(self.current, 0.01)
                slot = max(self.next_slot, now)
                self.next_slot = slot + interval
                wait = slot - now
                self._served += 1
        if wait > 0:
            time.sleep(wait)

    def penalize(self, reason=""):
        """被限流：速率砍半 + 惩罚性暂停。"""
        with self.lock:
            self.hit_429 += 1
            old = self.current
            self.current = max(MIN_QPS, self.current / 2.0)
            self.paused_until = time.time() + THROTTLE_BACKOFF
            self.next_slot = self.paused_until
            new = self.current
        log(
            "  ⚠ 服务端限流%s：速率 %.1f → %.1f QPS，暂停 %d 秒后继续"
            % (("（%s）" % reason) if reason else "", old, new, THROTTLE_BACKOFF)
        )

    def relax(self):
        """长时间没再被限流，就逐步恢复到目标速率。"""
        with self.lock:
            if self.current < self.target:
                old = self.current
                self.current = min(self.target, self.current * 1.25)
                new = self.current
            else:
                return
        log("  速率回升 %.1f → %.1f QPS" % (old, new))

    def stats(self):
        with self.lock:
            return self.current, self.hit_429


# 全局实例：所有阶段共用，确保整体速率受控
LIMITER = RateLimiter(TARGET_QPS)


def request_json(
    url,
    token=None,
    timeout=TIMEOUT,
    throttle=True,
    method="GET",
    headers=None,
    body=None,
    max_bytes=None,
):
    """发一个 HTTP 请求并解析 JSON。

    throttle=True 时会先向全局限速器申请配额，保证所有线程加起来
    也不超过设定 QPS；HTTP 429 会转成 Throttled 交给上层降速处理。
    """
    if throttle:
        LIMITER.acquire()
    hdrs = dict(headers or {})
    if token is not None and "token" not in hdrs:
        hdrs["token"] = token
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            metrics.increment("http.requests", 1)
            metrics.increment(f"http.status.{resp.status}", 1)
            raw = resp.read() if max_bytes is None else resp.read(max_bytes + 1)
            if max_bytes is not None and len(raw) > max_bytes:
                raise ValueError("JSON response too large")
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        metrics.increment("http.requests", 1)
        metrics.increment(f"http.status.{e.code}", 1)
        if e.code == 429:
            value = e.headers.get("Retry-After", "") if e.headers else ""
            try:
                delay = max(0, int(value))
            except ValueError:
                try:
                    delay = max(0, parsedate_to_datetime(value).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    delay = 0
            e.close()
            raise Throttled("HTTP 429 Too Many Requests", retry_after=delay) from None
        raise
    except Exception:
        metrics.increment("http.requests", 1)
        metrics.increment("http.errors", 1)
        raise
    finally:
        metrics.timing("http.duration", time.time() - t0)


def get_bytes(url, headers=None, timeout=TIMEOUT, throttle=False, max_bytes=None):
    """GET 原始字节（图片下载、代理）。默认不走全局限速（保持历史行为）。"""
    if throttle:
        LIMITER.acquire()
    req = urllib.request.Request(url, headers=headers or {})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        if max_bytes is None:
            return resp.read()
        data = resp.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise OSError("response too large")
        return data


def api_get(token, path, params=None, allow_null_data=False):
    """调用湖底接口，返回 data 字段。

    返回码约定（源码 + 实测）：200 成功 / 400 参数错误 / 401 认证失败
    """
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    r = request_json(LAKE_API + path + qs, token)
    code = r.get("code")
    if code != 200:
        msg = str(r.get("msg"))[:160]
        if code in (401, 403):
            raise FatalError(
                "接口 %s 返回 code=%s（%s），token 可能已失效，"
                "请重新运行以重新登录" % (path, code, msg)
            )
        if code == 400:
            raise BadRequest(
                "接口 %s 返回 code=400（%s）；请求参数：%s" % (path, msg, str(params)[:200])
            )
        if code == 429:
            raise Throttled("接口 %s 返回 code=429（%s）" % (path, msg))
        raise RuntimeError("接口 %s 返回 code=%s：%s" % (path, code, msg))
    data = r.get("data")
    if data is None and not allow_null_data:
        raise RuntimeError("接口 %s 的 data 为空：%s" % (path, str(r)[:160]))
    return data


def with_retry(fn, what, fuse, interval=REQUEST_INTERVAL):
    """带指数退避的重试；成功返回结果，耗尽重试抛异常。

    400（参数错误）不重试：这类错误对同一批请求是系统性的，
    重试只是浪费时间和请求量，直接把熔断器打到停止。
    429（限流）会触发全局降速，然后重试。
    """
    last = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            out = fn()
            fuse.ok()
            return out
        except FatalError:
            raise
        except BadRequest as e:
            # 参数错误：立刻熔断，不再重试
            fuse.force_stop("参数错误（code=400）：%s" % str(e)[:140])
            raise
        except Throttled as e:
            # 被限流：降速后重试，不计入熔断（这不是我们请求有问题）
            last = e
            LIMITER.penalize(str(e)[:60])
            if attempt < MAX_RETRY:
                continue
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            ConnectionError,
            OSError,
            RuntimeError,
            ValueError,
        ) as e:
            last = e
            fuse.fail(e)
            if fuse.stopped:
                break
            if attempt < MAX_RETRY:
                wait = min(2**attempt, 10)
                log("  %s 第 %d 次失败（%s），%d 秒后重试" % (what, attempt, str(e)[:90], wait))
                time.sleep(wait)
    raise RuntimeError("%s 重试 %d 次仍失败：%s" % (what, MAX_RETRY, last))


def pace(interval=0.0):
    """兼容保留的空实现：限速已统一由 LIMITER 在请求层控制。

    以前这里做「每个线程各睡一会儿」，那是单线程时代的做法——多线程下
    每个线程各睡各的，整体 QPS 会随并发数成倍上涨，等于偷偷突破限速。
    现在速率由 RateLimiter 全局控制，这个函数不再需要休眠。
    --interval 仅在 --qps 0（显式关闭限速）时才作为额外间隔生效。
    """
    if interval and LIMITER.target <= 0:
        time.sleep(interval)
