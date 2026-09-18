# -*- coding: utf-8 -*-
"""运行日志与状态输出。独立于 HTTP / DB / 抓取阶段。"""
import os
import threading
import time

from .config import LOG_PATH, RUN_STATE_PATH


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write_run_state(state, detail=""):
    """把「当前正在做什么」写到一个文件里。

    用途：抓取可能跑很久，用户（或另一个 Agent）想知道到底有没有在跑、
    跑到哪一步了，看这个文件即可，不用去猜命令行窗口。
    """
    try:
        with open(RUN_STATE_PATH, "w", encoding="utf-8") as f:
            f.write("状态：%s\n" % state)
            f.write("更新时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            if detail:
                f.write("当前步骤：%s\n" % detail)
            f.write("进程号：%d\n" % os.getpid())
    except OSError:
        pass


def mark_finished(state="已结束"):
    write_run_state(state)


def progress(msg, every=25, state=None):
    """按次数节流的进度输出。

    抓取可能持续很久，必须让用户随时看到「还活着、进行到哪了」，
    否则会以为卡死。默认每 25 次输出一次（约 10~25 秒）。
    """
    if state is None:
        state = {}
    n = state.get("count", 0) + 1
    state["count"] = n
    if n == 1 or n % every == 0:
        log(msg)
    return n


class Ticker:
    """后台心跳 + 状态条：定期打印「进度条 0~100% + 已用时长」。

    为什么需要它：正文和评论阶段可能跑很久，即使按次数节流，
    遇到慢请求时仍可能几十秒没有输出。有了心跳，用户随时能确认程序活着，
    进度条则让人一眼看出「完成多少了」。
    """

    def __init__(self, what, interval=10, width=28):
        self.what = what
        self.interval = interval
        self.width = width
        self.start = time.time()
        self.done = 0
        self.total = 0
        self._stop = threading.Event()
        self._t = None
        self._lock = threading.Lock()

    def start_beat(self):
        self._t = threading.Thread(target=self._beat, daemon=True)
        self._t.start()

    def bar(self):
        """生成形如 [████████░░░░░░░░] 50% 的进度条。"""
        if self.total <= 0:
            return None
        with self._lock:
            done, total = self.done, self.total
        pct = max(0.0, min(100.0, done * 100.0 / total))
        filled = int(self.width * pct / 100)
        return "[%s%s] %5.1f%%" % ("█" * filled, "░" * (self.width - filled), pct)

    def _beat(self):
        while not self._stop.wait(self.interval):
            el = int(time.time() - self.start)
            b = self.bar()
            if b:
                # 估算剩余时间，让人知道还要等多久
                with self._lock:
                    done, total = self.done, self.total
                eta = ""
                if done > 0 and total > done:
                    remain = (time.time() - self.start) / done * (total - done)
                    eta = "，预计还需 %d 分 %d 秒" % (int(remain) // 60,
                                                     int(remain) % 60)
                log("  %s %s %d/%d，已用 %d 分 %d 秒%s"
                    % (self.what, b, done, total, el // 60, el % 60, eta))
            else:
                log("  %s进行中：已用 %d 分 %d 秒" % (self.what, el // 60, el % 60))

    def bump(self, n=1):
        with self._lock:
            self.done += n

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=1)
