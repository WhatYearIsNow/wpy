# -*- coding: utf-8 -*-
"""wpy.board_api 中纯函数的单元测试。"""
import pytest
from wpy.board_api import _quote, _json_list


class TestQuote:
    """测试 _quote — URL 安全编码。"""

    def test_alphanumeric(self):
        assert _quote("abc123") == "abc123"

    def test_safe_special_chars(self):
        assert _quote("._-~/") == "._-~/"

    def test_space_encoded(self):
        result = _quote("hello world")
        assert "hello" in result
        assert "world" in result
        assert "%" in result  # 空格被编码

    def test_unicode_encoded(self):
        # Python 的 str.isalnum() 对中文返回 True，所以中文不会被编码
        result = _quote("中文")
        assert result == "中文"

    def test_equals(self):
        assert _quote("a=b") == "a%3Db"

    def test_ampersand(self):
        assert _quote("a&b") == "a%26b"

    def test_question_mark(self):
        assert _quote("?") == "%3F"

    def test_slash(self):
        assert _quote("/") == "/"

    def test_empty(self):
        assert _quote("") == ""

    def test_mixed(self):
        result = _quote("path/to/file.jpg")
        assert result == "path/to/file.jpg"

    def test_url_with_query(self):
        result = _quote("http://example.com?a=1&b=2")
        assert "example.com" in result
        assert "%3F" in result
        assert "%26" in result


class TestJsonList:
    """测试 _json_list — JSON 数组解析与安全回退。"""

    def test_valid_array(self):
        assert _json_list('[1, 2, 3]') == [1, 2, 3]

    def test_empty_array(self):
        assert _json_list('[]') == []

    def test_string_array(self):
        assert _json_list('["a", "b"]') == ["a", "b"]

    def test_empty_string(self):
        assert _json_list("") == []

    def test_none(self):
        assert _json_list(None) == []

    def test_invalid_json(self):
        assert _json_list("{invalid}") == []

    def test_non_list_json(self):
        assert _json_list('{"key": "value"}') == []

    def test_number_json(self):
        assert _json_list("42") == []

    def test_string_json(self):
        assert _json_list('"hello"') == []

    def test_nested_array(self):
        assert _json_list('[[1], [2]]') == [[1], [2]]
