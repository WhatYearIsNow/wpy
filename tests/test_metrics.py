# -*- coding: utf-8 -*-
"""wpy.metrics 的单元测试。"""
import time
import pytest
from wpy.metrics import MetricsRegistry, StructuredLogger, metrics


class TestCounter:
    """测试 Counter 计数器。"""

    def test_initial_value(self):
        from wpy.metrics import Counter
        c = Counter()
        assert c.get() == 0

    def test_increment(self):
        from wpy.metrics import Counter
        c = Counter()
        c.inc()
        assert c.get() == 1

    def test_increment_n(self):
        from wpy.metrics import Counter
        c = Counter()
        c.inc(5)
        assert c.get() == 5

    def test_multiple_increments(self):
        from wpy.metrics import Counter
        c = Counter()
        c.inc(2)
        c.inc(3)
        c.inc()
        assert c.get() == 6


class TestTimer:
    """测试 Timer 计时器。"""

    def test_initial_stats(self):
        from wpy.metrics import Timer
        t = Timer()
        stats = t.stats()
        assert stats["count"] == 0
        assert stats["avg"] == 0.0
        assert stats["total"] == 0.0

    def test_observe(self):
        from wpy.metrics import Timer
        t = Timer()
        t.observe(1.0)
        stats = t.stats()
        assert stats["count"] == 1
        assert stats["total"] == pytest.approx(1.0)
        assert stats["avg"] == pytest.approx(1.0)

    def test_multiple_observations(self):
        from wpy.metrics import Timer
        t = Timer()
        t.observe(0.5)
        t.observe(1.5)
        stats = t.stats()
        assert stats["count"] == 2
        assert stats["total"] == pytest.approx(2.0)
        assert stats["avg"] == pytest.approx(1.0)


class TestMetricsRegistry:
    """测试 MetricsRegistry。"""

    def setup_method(self):
        self.reg = MetricsRegistry()

    def test_increment_counter(self):
        self.reg.increment("http.requests")
        self.reg.increment("http.requests")
        snap = self.reg.snapshot()
        assert snap["counters"]["http.requests"] == 2

    def test_timing(self):
        self.reg.timing("http.duration", 0.5)
        self.reg.timing("http.duration", 1.5)
        snap = self.reg.snapshot()
        assert snap["timers"]["http.duration"]["count"] == 2
        assert snap["timers"]["http.duration"]["avg"] == pytest.approx(1.0)

    def test_snapshot_empty(self):
        snap = self.reg.snapshot()
        assert snap == {"counters": {}, "timers": {}}

    def test_snapshot_contains_both(self):
        self.reg.increment("a")
        self.reg.timing("b", 1.0)
        snap = self.reg.snapshot()
        assert "a" in snap["counters"]
        assert "b" in snap["timers"]

    def test_reset(self):
        self.reg.increment("x")
        self.reg.timing("y", 1.0)
        self.reg.reset()
        snap = self.reg.snapshot()
        assert snap == {"counters": {}, "timers": {}}

    def test_independent_counters(self):
        self.reg.increment("a")
        self.reg.increment("b")
        snap = self.reg.snapshot()
        assert snap["counters"]["a"] == 1
        assert snap["counters"]["b"] == 1


class TestStructuredLogger:
    """测试 StructuredLogger。"""

    def test_write_creates_entry(self, tmp_path):
        from wpy.metrics import StructuredLogger
        import json
        path = str(tmp_path / "test.jsonl")
        logger = StructuredLogger(path)
        logger.write("INFO", "test message", "test_module")
        logger.flush()
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["level"] == "INFO"
        assert entry["msg"] == "test message"
        assert entry["source"] == "test_module"
        assert "ts" in entry

    def test_multiple_entries(self, tmp_path):
        from wpy.metrics import StructuredLogger
        import json
        path = str(tmp_path / "test.jsonl")
        logger = StructuredLogger(path)
        logger.write("INFO", "msg1")
        logger.write("ERROR", "msg2")
        logger.flush()
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["msg"] == "msg1"
        assert json.loads(lines[1])["msg"] == "msg2"

    def test_flush_empty(self, tmp_path):
        from wpy.metrics import StructuredLogger
        path = str(tmp_path / "test.jsonl")
        logger = StructuredLogger(path)
        logger.flush()  # 不应报错
        assert not (tmp_path / "test.jsonl").exists()

    def test_auto_flush_at_50(self, tmp_path):
        from wpy.metrics import StructuredLogger
        import json
        path = str(tmp_path / "test.jsonl")
        logger = StructuredLogger(path)
        for i in range(55):
            logger.write("INFO", f"msg{i}")
        # 应已自动刷盘
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) >= 50
