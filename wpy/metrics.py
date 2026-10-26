# -*- coding: utf-8 -*-
"""轻量级指标收集与结构化日志。

设计原则：
- 零外部依赖：只用标准库（json、threading、time）
- 低开销：计数器用原子整数，指标写入异步批量
- 可观测：支持查询当前指标快照、导出为 JSON
- 不影响主流程：指标收集失败不抛异常

使用方式：
    from .metrics import metrics
    metrics.increment("http.requests", tags={"method": "GET", "status": "200"})
    metrics.timing("http.duration", 0.234)
    snapshot = metrics.snapshot()
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any


class Counter:
    """计数器：记录事件发生次数。"""

    __slots__ = ("value", "mu")

    def __init__(self) -> None:
        self.value = 0
        self.mu = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self.mu:
            self.value += n

    def get(self) -> int:
        with self.mu:
            return self.value


class Timer:
    """计时器：记录某项操作的耗时分布。"""

    __slots__ = ("count", "total", "mu")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.mu = threading.Lock()

    def observe(self, seconds: float) -> None:
        with self.mu:
            self.count += 1
            self.total += seconds

    def stats(self) -> dict[str, float]:
        with self.mu:
            if self.count == 0:
                return {"count": 0, "avg": 0.0, "total": 0.0}
            return {
                "count": self.count,
                "avg": self.total / self.count,
                "total": self.total,
            }


class MetricsRegistry:
    """指标注册表：管理所有 Counter 和 Timer。"""

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._timers: dict[str, Timer] = {}
        self._mu = threading.Lock()

    def _get_counter(self, name: str) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter()
        return self._counters[name]

    def _get_timer(self, name: str) -> Timer:
        if name not in self._timers:
            self._timers[name] = Timer()
        return self._timers[name]

    def increment(self, name: str, n: int = 1) -> None:
        self._get_counter(name).inc(n)

    def timing(self, name: str, seconds: float) -> None:
        self._get_timer(name).observe(seconds)

    def snapshot(self) -> dict[str, Any]:
        """返回所有指标的快照（线程安全拷贝）。"""
        with self._mu:
            result: dict[str, Any] = {"counters": {}, "timers": {}}
            for name, c in self._counters.items():
                result["counters"][name] = c.get()
            for name, t in self._timers.items():
                result["timers"][name] = t.stats()
            return result

    def reset(self) -> None:
        """重置所有指标（用于测试或周期性清零）。"""
        with self._mu:
            self._counters.clear()
            self._timers.clear()


# 全局单例
metrics = MetricsRegistry()


class StructuredLogger:
    """结构化日志：将 log() 的输出同时写入 JSON lines 文件。

    每行 JSON 包含：timestamp、level、message、source。
    便于用 jq / grep / 日志系统解析。
    """

    __slots__ = ("json_path", "mu", "buffer")

    def __init__(self, json_path: str) -> None:
        self.json_path = json_path
        self.mu = threading.Lock()
        self.buffer: list[str] = []

    def write(self, level: str, message: str, source: str = "") -> None:
        entry = json.dumps(
            {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "level": level,
                "msg": message,
                "source": source,
            },
            ensure_ascii=False,
        )
        with self.mu:
            self.buffer.append(entry + "\n")
            # 每 50 行或缓冲区较大时刷盘
            if len(self.buffer) >= 50:
                self._flush()

    def _flush(self) -> None:
        if not self.buffer:
            return
        data = "".join(self.buffer)
        self.buffer.clear()
        try:
            with open(self.json_path, "a", encoding="utf-8") as f:
                f.write(data)
        except OSError:
            pass

    def flush(self) -> None:
        with self.mu:
            self._flush()


# 全局单例（在 config 中提供路径）
_structured_logger: StructuredLogger | None = None


def get_structured_logger() -> StructuredLogger | None:
    return _structured_logger


def init_structured_logger(json_path: str) -> StructuredLogger:
    global _structured_logger
    _structured_logger = StructuredLogger(json_path)
    return _structured_logger
