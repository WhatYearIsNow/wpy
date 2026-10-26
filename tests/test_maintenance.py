"""Development isolation and release failure modes; no real services or external calls."""

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import tarfile

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"maintenance_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_development_removes_credentials_and_redirects_all_state(tmp_path, monkeypatch):
    dev = load_script("dev")
    monkeypatch.setenv("WPY_PASSWORD", "fake_password")
    monkeypatch.setenv("WPY_AI_MODEL_CONFIG", "/fake/private/model.json")
    monkeypatch.setenv("WPY_DATA_DIR", "/fake/production")
    monkeypatch.setenv("PYTHONPATH", "/fake/production")
    env = dev.isolated_env(tmp_path)
    assert "WPY_PASSWORD" not in env
    assert "WPY_AI_MODEL_CONFIG" not in env
    assert "PYTHONPATH" not in env
    for key in ("DATA", "BUILD", "LOG", "PAGES"):
        assert Path(env[f"WPY_{key}_DIR"]).is_relative_to(tmp_path)
    assert Path(env["WPY_AI_STATE_DIR"]).is_relative_to(tmp_path)
    assert env["WPY_HOST"] == "127.0.0.1"


@pytest.mark.skipif(sys.platform == "win32", reason="process-group cleanup uses POSIX signals")
def test_hot_preview_uses_its_own_empty_backend(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "wpy", tmp_path / "wpy", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "scripts").mkdir()
    for name in ("dev.py", "build_hot.py"):
        shutil.copyfile(root / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / "pyproject.toml").write_text("")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env["WPY_DATA_DIR"] = "/fake/production/data"
    proc = subprocess.Popen(
        [sys.executable, "scripts/dev.py", "hot", "--port", str(port)],
        cwd=tmp_path,
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert proc.poll() is None, "hot preview exited before startup"
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/init", timeout=1) as response:
                    assert json.load(response)["total"] == 0
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("isolated hot backend never became ready")
        with opener.open(f"http://127.0.0.1:{port}/", timeout=1) as response:
            assert len(response.read()) > 1000
        assert (tmp_path / ".local/dev/data/sitewide.db").exists()
    finally:
        os.killpg(proc.pid, signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_sqlite_backup_includes_committed_wal_and_refuses_overwrite(tmp_path):
    release = load_script("release")
    source = tmp_path / "source.sqlite3"
    output = tmp_path / "backup.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE sample (value TEXT)")
        conn.execute("INSERT INTO sample VALUES ('synthetic row')")
        conn.commit()
        release.backup(source, output)
        with sqlite3.connect(output) as copy:
            assert copy.execute("SELECT value FROM sample").fetchall() == [("synthetic row",)]
            assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        before = output.read_bytes()
        with pytest.raises(FileExistsError):
            release.backup(source, output)
        assert output.read_bytes() == before
        assert output.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "name,kind", [("../escape.py", "file"), ("/escape.py", "file"), ("wpy/link", "link")]
)
def test_archive_rejects_escape_and_links(tmp_path, name, kind):
    release = load_script("release")
    archive = tmp_path / "source.tar"
    with tarfile.open(archive, "w") as stream:
        entry = tarfile.TarInfo(name)
        if kind == "link":
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/fake/private"
        else:
            entry.size = 1
        stream.addfile(entry, io.BytesIO(b"x") if kind == "file" else None)
    with pytest.raises(ValueError):
        release.extract_archive(archive, tmp_path / "out")


def test_directives_preserve_venv_runtime_and_existing_stats_arguments(tmp_path):
    release = load_script("release")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "release.json").write_text(json.dumps({"python": str(bundle / "venv/bin/python")}))
    config = json.loads(
        (Path(__file__).resolve().parents[1] / "deploy/runtime.example.json").read_text()
    )
    values = release.directives(bundle, config)
    assert set(values) == set(release.UNITS)
    assert str(bundle / "venv/bin/python") + " -m wpy" in values["wpy-board"]
    assert "WPY_DATA_DIR=" + config["paths"]["DATA"] in values["wpy-board"]
    assert "refresh_stats.py --rebuild --qps 6" in values["wpy-refresh-stats"]
    assert "--all" not in values["wpy-refresh-stats"]
    assert "ExecStartPost=\n" in values["wpy-update"]
    assert all("wpy-notify" not in text and "wpy-ai-agent" not in text for text in values.values())


def test_activation_checks_source_before_systemd_calls(tmp_path, monkeypatch):
    release = load_script("release")
    bundle = tmp_path / "bundle"
    (bundle / "source").mkdir(parents=True)
    (bundle / "source/module.py").write_text("changed = True\n")
    (bundle / "release.json").write_text(json.dumps({"files": {}}))
    monkeypatch.setattr(
        release, "systemctl", lambda *a, **k: pytest.fail("systemd must not be touched")
    )
    with pytest.raises(ValueError, match="manifest"):
        release.activate(bundle, {"release_root": str(tmp_path)})


def test_health_waits_for_restarted_socket_and_only_checks_running_units(monkeypatch):
    release = load_script("release")
    calls = []

    def health(urls):
        calls.append(urls)
        if len(calls) == 1:
            raise ConnectionRefusedError("synthetic startup delay")

    monkeypatch.setattr(release, "health", health)
    monkeypatch.setattr(release.time, "sleep", lambda *args: None)
    release.wait_healthy(["wpy-hot"])
    assert calls == [["http://127.0.0.1:8766/healthz"]] * 2


def test_activation_health_failure_restores_previous_configuration(tmp_path, monkeypatch):
    release = load_script("release")
    bundle = tmp_path / "bundle"
    (bundle / "source").mkdir(parents=True)
    (bundle / "release.json").write_text(json.dumps({"files": {}}))
    applied = []
    restored = []
    monkeypatch.setattr(
        release, "directives", lambda *a: {unit: "candidate" for unit in release.UNITS}
    )
    monkeypatch.setattr(
        release, "unit_state", lambda unit: "active" if unit in release.WEB_UNITS else "inactive"
    )
    monkeypatch.setattr(release, "apply_dropins", lambda values: applied.append(values))
    monkeypatch.setattr(release, "systemctl", lambda *a, **k: None)
    monkeypatch.setattr(release, "restore", lambda tx: restored.append(tx))
    monkeypatch.setattr(release.time, "sleep", lambda *a: None)
    monkeypatch.setattr(
        release, "health", lambda *a: (_ for _ in ()).throw(ValueError("synthetic failure"))
    )
    with pytest.raises(ValueError):
        release.activate(bundle, {"release_root": str(tmp_path), "state_dir": str(tmp_path)})
    assert applied and len(restored) == 1
    assert restored[0]["release"] == str(bundle)
