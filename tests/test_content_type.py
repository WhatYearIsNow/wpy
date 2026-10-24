# -*- coding: utf-8 -*-
"""wpy.content_type 的单元测试。"""
from wpy.content_type import guess_type


class TestGuessType:
    """测试 guess_type 按扩展名映射 Content-Type。"""

    def test_jpg(self):
        assert guess_type("photo.jpg") == "image/jpeg"

    def test_jpeg(self):
        assert guess_type("photo.jpeg") == "image/jpeg"

    def test_png(self):
        assert guess_type("image.png") == "image/png"

    def test_gif(self):
        assert guess_type("anim.gif") == "image/gif"

    def test_webp(self):
        assert guess_type("hero.webp") == "image/webp"

    def test_bmp(self):
        assert guess_type("screenshot.bmp") == "image/bmp"

    def test_case_insensitive(self):
        assert guess_type("photo.JPG") == "image/jpeg"
        assert guess_type("photo.JpEg") == "image/jpeg"
        assert guess_type("image.PNG") == "image/png"

    def test_unknown_extension(self):
        assert guess_type("file.xyz") == "application/octet-stream"

    def test_no_extension(self):
        assert guess_type("Makefile") == "application/octet-stream"

    def test_hidden_file(self):
        # 兼容 os.path.splitext 行为：".hidden" 没有扩展名
        assert guess_type(".hidden") == "application/octet-stream"

    def test_multiple_dots(self):
        # 取最后一个点之后的部分：archive.tar.gz → gz → 未知扩展名
        assert guess_type("archive.tar.gz") == "application/octet-stream"
        # 但如果是 .gif 则正确
        assert guess_type("archive.tar.gif") == "image/gif"

    def test_empty_string(self):
        assert guess_type("") == "application/octet-stream"
