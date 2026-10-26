"""源码与安装包的默认写入目录应各自可用。"""

import os

import pytest

from wpy import config


@pytest.mark.parametrize("marker", [".git", "pyproject.toml"])
def test_source_checkout_keeps_project_root(tmp_path, monkeypatch, marker):
    project = tmp_path / "project"
    (project / "wpy").mkdir(parents=True)
    (project / marker).touch()
    monkeypatch.setattr(config, "__file__", str(project / "wpy" / "config.py"))

    assert config._default_base_dir() == str(project)


def test_installed_package_uses_xdg_data_home(tmp_path, monkeypatch):
    package_dir = tmp_path / "site-packages" / "wpy"
    package_dir.mkdir(parents=True)
    user_data = tmp_path / "user-data"
    monkeypatch.setattr(config, "__file__", str(package_dir / "config.py"))
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(user_data))

    assert config._default_base_dir() == str(user_data / "wpy")
    assert not (package_dir.parent / "data").exists()


def test_windows_install_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "__file__", str(tmp_path / "site-packages" / "wpy" / "config.py"))
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))

    assert config._default_base_dir() == os.path.join(str(tmp_path / "local-app-data"), "wpy")
