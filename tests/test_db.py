# -*- coding: utf-8 -*-
"""wpy.db 中纯函数的单元测试。"""
import json
import pytest
from wpy.db import _int, is_essence, post_tuple, floor_tuple, iter_floors, official_row


class TestInt:
    """测试 _int — 安全整数转换。"""

    def test_none(self):
        assert _int(None) == 0

    def test_empty_string(self):
        assert _int("") == 0

    def test_valid_int(self):
        assert _int(42) == 42

    def test_string_int(self):
        assert _int("123") == 123

    def test_negative(self):
        assert _int("-5") == -5

    def test_float_string(self):
        assert _int("3.14") == 0

    def test_float(self):
        # int(3.14) == 3，_int 对 float 不做特殊处理
        assert _int(3.14) == 3

    def test_text(self):
        assert _int("hello") == 0

    def test_zero(self):
        assert _int(0) == 0
        assert _int("0") == 0


class TestIsEssence:
    """测试 is_essence — 精华判断。"""

    def test_recommend_etag(self):
        assert is_essence({"e_tag": "recommend"}) is True

    def test_essence_etag(self):
        assert is_essence({"e_tag": "essence"}) is True

    def test_good_etag(self):
        assert is_essence({"e_tag": "good"}) is True

    def test_digest_etag(self):
        assert is_essence({"e_tag": "digest"}) is True

    def test_mixed_case_etag(self):
        assert is_essence({"e_tag": "RECOMMEND"}) is True
        assert is_essence({"e_tag": "Recommend"}) is True

    def test_is_essence_flag(self):
        assert is_essence({"is_essence": 1}) is True
        assert is_essence({"is_essence": True}) is True
        assert is_essence({"is_essence": "1"}) is True
        assert is_essence({"is_essence": "true"}) is True
        assert is_essence({"is_essence": "True"}) is True

    def test_is_recommend_flag(self):
        assert is_essence({"is_recommend": 1}) is True

    def test_essence_flag(self):
        assert is_essence({"essence": 1}) is True

    def test_digest_flag(self):
        assert is_essence({"digest": 1}) is True

    def test_no_match(self):
        assert is_essence({"e_tag": "normal"}) is False
        assert is_essence({"e_tag": ""}) is False
        assert is_essence({}) is False
        assert is_essence({"is_essence": 0}) is False
        assert is_essence({"is_essence": False}) is False
        assert is_essence({"is_essence": "0"}) is False


class TestPostTuple:
    """测试 post_tuple — JSON → 数据库元组转换。"""

    def _sample(self):
        return {
            "id": 1001,
            "uid": 77990,
            "title": "测试帖子",
            "content": "正文内容",
            "created_at": "2026-09-01T12:00:00",
            "nickname": "测试用户",
            "user_info": {"level": 5, "avatar": "http://av.png"},
            "type": 2,
            "department": {"name": "计算机学院"},
            "tag": {"name": "技术", "id": 10},
            "like_count": 10,
            "fav_count": 5,
            "comment_count": 3,
            "visit_count": 100,
            "rating": 0,
            "campus": 1,
            "variant": 0,
            "solved": True,
            "e_tag": "",
            "image_urls": ["http://img1.jpg", "http://img2.jpg"],
        }

    def test_full_conversion(self):
        result = post_tuple(self._sample())
        assert result[0] == 1001   # id
        assert result[1] == 77990  # uid
        assert result[2] == "测试帖子"
        assert result[3] == "正文内容"
        assert result[4] == "2026-09-01T12:00:00"
        assert result[5] == "测试用户"
        assert result[6] == 5      # level
        assert result[7] == "http://av.png"
        assert result[8] == 2      # type
        assert result[9] == "计算机学院"
        assert result[10] == "技术"
        assert result[11] == 10    # tag id
        assert result[12] == 10    # like_count
        assert result[13] == 5     # fav_count
        assert result[14] == 3     # comment_count
        assert result[15] == 100   # visit_count
        assert result[16] == 0     # rating
        assert result[17] == 1     # campus
        assert result[18] == 0     # variant
        assert result[19] == "是"  # solved
        assert result[20] == ""    # e_tag
        assert result[21] == 2     # image count
        assert len(json.loads(result[22])) == 2  # image urls json

    def test_missing_fields_defaults(self):
        result = post_tuple({})
        assert result[0] == 0      # id
        assert result[2] == ""     # title
        assert result[3] == ""     # content
        assert result[19] == "否"  # solved

    def test_image_count_zero(self):
        result = post_tuple({"id": 1, "image_urls": []})
        assert result[21] == 0

    def test_image_count_none(self):
        result = post_tuple({"id": 1, "image_urls": None})
        assert result[21] == 0


class TestFloorTuple:
    """测试 floor_tuple — 楼层 JSON → 元组转换。"""

    def _sample_floor(self):
        return {
            "id": 5001,
            "uid": 77990,
            "nickname": "评论者",
            "user_info": {"level": 3, "avatar": "http://u.png"},
            "content": "评论内容",
            "created_at": "2026-09-01T13:00:00",
            "reply_to_name": "被回复者",
            "like_count": 2,
            "value": 1,
            "sub_floor_cnt": 0,
            "image_url": "http://f.png",
            "is_owner": False,
        }

    def test_basic(self):
        result = floor_tuple(self._sample_floor(), post_id=1001, parent_id=0)
        assert result[0] == 5001   # floor id
        assert result[1] == 1001   # post id
        assert result[2] == 0      # parent id
        assert result[3] == 77990  # uid
        assert result[4] == "评论者"
        assert result[5] == 3      # level
        assert result[6] == "http://u.png"
        assert result[7] == "评论内容"
        assert result[8] == "2026-09-01T13:00:00"
        assert result[9] == "被回复者"
        assert result[10] == 2     # like_count
        assert result[11] == 1     # value
        assert result[12] == 0     # sub_floor_cnt
        assert result[13] == "http://f.png"
        assert result[14] == 0     # is_owner


class TestIterFloors:
    """测试 iter_floors — 楼层与楼中楼递归展平。"""

    def test_flat_list(self):
        floors = [
            {"id": 1, "user_info": {}, "content": "c1"},
            {"id": 2, "user_info": {}, "content": "c2"},
        ]
        result = list(iter_floors(floors, post_id=100))
        assert len(result) == 2
        assert result[0][1] == 100
        assert result[1][1] == 100

    def test_nested_sub_floors(self):
        floors = [
            {
                "id": 1,
                "user_info": {},
                "content": "top",
                "sub_floors": [
                    {"id": 10, "user_info": {}, "content": "sub1"},
                    {"id": 11, "user_info": {}, "content": "sub2"},
                ],
            }
        ]
        result = list(iter_floors(floors, post_id=100))
        assert len(result) == 3
        # 主楼 parent_id=0
        assert result[0][2] == 0
        # 子楼 parent_id=1
        assert result[1][2] == 1
        assert result[2][2] == 1

    def test_deeply_nested(self):
        floors = [
            {
                "id": 1,
                "user_info": {},
                "content": "top",
                "sub_floors": [
                    {
                        "id": 2,
                        "user_info": {},
                        "content": "mid",
                        "sub_floors": [
                            {"id": 3, "user_info": {}, "content": "deep"},
                        ],
                    }
                ],
            }
        ]
        result = list(iter_floors(floors, post_id=100))
        assert len(result) == 3
        assert result[0][0] == 1
        assert result[1][0] == 2
        assert result[2][0] == 3

    def test_empty_list(self):
        assert list(iter_floors([], post_id=100)) == []

    def test_none_list(self):
        assert list(iter_floors(None, post_id=100)) == []


class TestOfficialRow:
    """测试 official_row — 官方回复行转换。"""

    def test_basic(self):
        f = {
            "id": 6001,
            "uid": 100,
            "nickname": "官方",
            "user_info": {"level": 10, "avatar": ""},
            "content": "官方回复内容",
            "created_at": "2026-09-01T14:00:00",
        }
        result = official_row(f, post_id=1001)
        assert result[0] == 6001   # id
        assert result[1] == 1001   # post_id
        assert result[2] == 100    # uid
        assert result[3] == "官方"  # nickname
        assert result[4] == ""     # avatar
        assert result[5] == "官方回复内容"  # content
        assert result[6] == "2026-09-01T14:00:00"  # created_at

    def test_missing_fields(self):
        f = {"id": 6001, "content": "回复", "user_info": {}}
        result = official_row(f, post_id=1001)
        assert result[0] == 6001   # id
        assert result[5] == "回复"  # content (index 5)
