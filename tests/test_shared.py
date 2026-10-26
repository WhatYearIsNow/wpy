# -*- coding: utf-8 -*-
"""wpy.crawler.shared 中纯函数的单元测试。"""

import threading
import pytest
from wpy.crawler.shared import (
    crawlable_types,
    only_recommend,
    usable_types,
    _parse_ids,
    _parse_board_ids,
    _parse_since_date,
    _fmt_dur,
)
from wpy.http import Fuse, FuseTripped


class TestCrawlableTypes:
    """测试 crawlable_types — 排除精华(0)的板块 ID 列表。"""

    def test_excludes_recommend(self):
        type_names = {0: "精华", 1: "板块A", 2: "板块B"}
        assert crawlable_types(type_names) == [1, 2]

    def test_empty(self):
        assert crawlable_types({}) == []

    def test_only_recommend(self):
        assert crawlable_types({0: "精华"}) == []

    def test_sorted(self):
        type_names = {5: "E", 1: "A", 3: "C", 0: "精华"}
        assert crawlable_types(type_names) == [1, 3, 5]


class TestOnlyRecommend:
    """测试 only_recommend — 判断是否只选了精华。"""

    def test_only_zero(self):
        assert only_recommend({0: "精华"}) is True

    def test_mixed(self):
        assert only_recommend({0: "精华", 1: "板块"}) is False

    def test_empty(self):
        assert only_recommend({}) is False

    def test_only_non_zero(self):
        assert only_recommend({1: "板块", 2: "板块B"}) is False


class TestUsableTypes:
    """测试 usable_types — 交集过滤。"""

    def test_subset(self):
        type_names = {1: "A", 2: "B", 3: "C"}
        assert usable_types([1, 3], type_names) == [1, 3]

    def test_empty_requested(self):
        type_names = {1: "A", 2: "B"}
        assert usable_types([], type_names) == [1, 2]

    def test_no_overlap(self):
        type_names = {1: "A", 2: "B"}
        assert usable_types([5, 6], type_names) == []

    def test_mixed_overlap(self):
        type_names = {1: "A", 2: "B", 3: "C"}
        assert usable_types([1, 5, 3], type_names) == [1, 3]


class TestParseIds:
    """测试 _parse_ids — 帖子 ID 解析。"""

    def test_single_mp(self):
        assert _parse_ids("#MP123456") == [123456]

    def test_multiple_mp(self):
        assert _parse_ids("#MP123 #MP456") == [123, 456]

    def test_app_link(self):
        assert _parse_ids("wpy://school_project/789") == [789]

    def test_plain_numbers(self):
        assert _parse_ids("123 456") == [123, 456]
        assert _parse_ids("123,456") == [123, 456]
        assert _parse_ids("123, 456") == [123, 456]

    def test_mixed_formats(self):
        # MP号先提取，app链接其次，剩余数字最后
        result = _parse_ids("#MP100 200 wpy://school_project/300")
        assert result == [100, 300, 200]

    def test_dedup(self):
        assert _parse_ids("#MP100 #MP100 200") == [100, 200]

    def test_empty(self):
        assert _parse_ids("") == []
        assert _parse_ids(None) == []

    def test_non_digit_ignored(self):
        assert _parse_ids("abc def") == []

    def test_chinese_comma(self):
        assert _parse_ids("123，456") == [123, 456]


class TestParseBoardIds:
    """测试 _parse_board_ids — 板块 ID 解析。"""

    def test_comma_separated(self):
        assert _parse_board_ids("1,2,3") == [1, 2, 3]

    def test_single(self):
        assert _parse_board_ids("5") == [5]

    def test_non_digit_rejected(self):
        assert _parse_board_ids("1,abc,3") == [1, 3]

    def test_empty(self):
        assert _parse_board_ids("") == []


class TestParseSinceDate:
    """测试 _parse_since_date — 日期格式校验。"""

    def test_valid_date(self):
        assert _parse_since_date("2026-09-01") == "2026-09-01"

    def test_invalid_format(self):
        assert _parse_since_date("2026/09/01") == ""

    def test_empty(self):
        assert _parse_since_date("") == ""

    def test_partial_date(self):
        assert _parse_since_date("2026-09") == ""

    def test_random_text(self):
        assert _parse_since_date("hello") == ""


class TestFmtDur:
    """测试 _fmt_dur — 秒数格式化。"""

    def test_seconds(self):
        assert _fmt_dur(30) == "约 30 秒"

    def test_minutes(self):
        result = _fmt_dur(120)
        assert "分" in result
        assert "秒" in result

    def test_hours(self):
        result = _fmt_dur(7200)
        assert "小时" in result
        assert "分" in result

    def test_zero(self):
        assert _fmt_dur(0) == "约 0 秒"

    def test_just_under_minute(self):
        assert _fmt_dur(59) == "约 59 秒"

    def test_just_under_hour(self):
        result = _fmt_dur(3599)
        assert "分" in result
        assert "小时" not in result


class TestFuse:
    """测试 Fuse 熔断器状态机。"""

    def test_ok_resets_count(self):
        fuse = Fuse(limit=3)
        fuse.fail("err1")
        fuse.fail("err2")
        fuse.ok()
        assert fuse.count == 0
        assert not fuse.stopped

    def test_fail_accumulates(self):
        fuse = Fuse(limit=3)
        fuse.fail("e1")
        fuse.fail("e2")
        assert fuse.count == 2
        assert not fuse.stopped

    def test_trips_at_limit(self):
        fuse = Fuse(limit=3)
        fuse.fail("e1")
        fuse.fail("e2")
        fuse.fail("e3")
        assert fuse.stopped
        assert fuse.count == 3
        assert "连续失败 3 次" in fuse.reason

    def test_check_raises(self):
        fuse = Fuse(limit=2)
        fuse.fail("e1")
        fuse.fail("e2")
        with pytest.raises(FuseTripped):
            fuse.check()

    def test_check_no_raise_when_ok(self):
        fuse = Fuse(limit=3)
        fuse.fail("e1")
        # 没到阈值，不应抛出
        fuse.check()  # 不应抛出

    def test_fail_after_stopped_no_accumulate(self):
        fuse = Fuse(limit=2)
        fuse.fail("e1")
        fuse.fail("e2")
        assert fuse.stopped
        fuse.fail("e3")  # 已熔断，不应再累加
        assert fuse.count == 2  # 仍为 2

    def test_force_stop(self):
        fuse = Fuse(limit=10)
        fuse.force_stop("手动停止")
        assert fuse.stopped
        assert fuse.reason == "手动停止"

    def test_force_stop_already_stopped(self):
        fuse = Fuse(limit=2)
        fuse.fail("e1")
        fuse.fail("e2")
        assert fuse.stopped
        original_reason = fuse.reason
        fuse.force_stop("override")
        assert fuse.stopped
        # force_stop 只在未熔断时覆盖 reason
        assert fuse.reason == original_reason

    def test_default_limit(self):
        fuse = Fuse()
        assert fuse.limit == 12  # 默认值


class TestRunWorkers:
    """测试 run_workers — 多线程异常传播。"""

    def test_single_worker_no_threading(self):
        """n=1 时不走线程分支，直接调用 fn。"""
        from wpy.crawler.shared import run_workers

        called = []

        def fn():
            called.append(1)

        run_workers(fn, 1)
        assert called == [1]

    def test_exception_propagation(self):
        from wpy.crawler.shared import run_workers

        def fn():
            raise ValueError("worker error")

        with pytest.raises(ValueError, match="worker error"):
            run_workers(fn, 2)

    def test_fuse_tripped_not_propagated(self):
        """FuseTripped 是正常退出信号，不应传播。"""
        from wpy.crawler.shared import run_workers

        def fn():
            raise FuseTripped("circuit open")

        # 不应抛出
        run_workers(fn, 2)

    def test_first_error_propagated(self):
        from wpy.crawler.shared import run_workers

        def fn():
            raise RuntimeError("error A")

        with pytest.raises(RuntimeError, match="error A"):
            run_workers(fn, 2)
