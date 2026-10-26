"""Prepare immutable WPY site releases, check health, back up SQLite, and switch site code.

Only the existing board/hot/crawler units are managed. Notifications and AI services
have independent releases and state. Code rollback never restores databases.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[1]
UNITS = ("wpy-board", "wpy-hot", "wpy-update", "wpy-refresh-stats", "wpy-refresh-all")
WEB_UNITS = UNITS[:2]
DROPIN = "50-site-release.conf"


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], check=True, **kwargs)


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def inventory(source):
    result = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("release source cannot contain symlinks")
        if path.is_file() and "__pycache__" not in path.parts:
            result[path.relative_to(source).as_posix()] = digest(path)
    return result


def clean_env(base):
    # Importing config creates directories: set isolation before running any code.
    env = {key: value for key, value in os.environ.items() if not key.startswith("WPY_")}
    env.pop("PYTHONPATH", None)
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for name in ("DATA", "BUILD", "LOG", "PAGES"):
        env[f"WPY_{name}_DIR"] = str(base / name.lower())
    env["WPY_NOTIFY_STATE_DIR"] = str(base / "notify-state")
    env["WPY_AI_STATE_DIR"] = str(base / "agent-state")
    return env


def extract_archive(archive, target):
    """Accept regular Git source files only; reject links and path traversal."""
    with tarfile.open(archive) as stream:
        for member in stream:
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("unsafe archive path")
            path = target / relative
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as src, path.open("xb") as dest:
                    import shutil

                    shutil.copyfileobj(src, dest)
            else:
                raise ValueError("release archive contains a link or special file")


def prepare(ref, output):
    sha = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=ROOT, text=True
    ).strip()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source"
    source.mkdir()
    archive = output / "source.tar"
    run(["git", "archive", "--format=tar", "--output", archive, sha], cwd=ROOT)
    extract_archive(archive, source)
    archive.unlink()
    if not (source / "requirements-dev.lock").is_file():
        raise ValueError("candidate commit must include requirements-dev.lock")
    # A fresh builder installs the candidate's lock, rather than using a dirty checkout.
    venv.create(output / "builder", with_pip=True)
    python = output / "builder/bin/python"
    run([python, "-m", "pip", "install", "-r", source / "requirements-dev.lock"])
    run([python, "-m", "pip", "install", "--no-deps", "--no-build-isolation", "-e", source])
    run([python, "-m", "pip", "check"])
    with tempfile.TemporaryDirectory(prefix="wpy-release-test-") as work:
        env = clean_env(Path(work))
        if (source / "scripts/dev.py").is_file():
            run([python, "-m", "ruff", "format", "--check", "."], cwd=source, env=env)
            run([python, "-m", "ruff", "check", "."], cwd=source, env=env)
        run([python, "-m", "pytest", "-q"], cwd=source, env=env)
        run([python, "tools/offline_stage_smoke.py"], cwd=source, env=env)
        run([python, "scripts/check_public_source.py"], cwd=source, env=env)
        run(
            [python, "-m", "build", "--no-isolation", "--outdir", output / "dist"],
            cwd=source,
            env=env,
        )
        venv.create(output / "venv", with_pip=True)
        installed = output / "venv/bin/python"
        wheels = list((output / "dist").glob("*.whl"))
        if len(wheels) != 1:
            raise ValueError("expected exactly one wheel")
        run([installed, "-m", "pip", "install", "--no-deps", wheels[0]])
        run([installed, "-m", "wpy", "--help"], cwd=work, env=env)
        run(
            [installed, "-c", "from wpy.ui_templates import CSS, JS; assert CSS and JS"],
            cwd=work,
            env=env,
        )
    # Build metadata is not runtime code. Keep the source inventory stable.
    import shutil

    for path in [
        source / "build",
        *source.glob("*.egg-info"),
        source / ".pytest_cache",
        source / ".ruff_cache",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
    manifest = {
        "kind": "git",
        "commit": sha,
        "python": str(output / "venv/bin/python"),
        "files": inventory(source),
    }
    (output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {sha[:12]}: {output}")


def health(urls):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for url in urls:
        with opener.open(url, timeout=5) as response:
            if response.status != 200 or json.load(response).get("ok") is not True:
                raise ValueError("health check failed")


def wait_healthy(units=WEB_UNITS):
    # systemctl restart can return before Python binds the listening sockets.
    ports = {"wpy-board": 8765, "wpy-hot": 8766}
    urls = [f"http://127.0.0.1:{ports[unit]}/healthz" for unit in units]
    for attempt in range(20):
        try:
            health(urls)
            return
        except (OSError, ValueError):
            if attempt == 19:
                raise
            time.sleep(0.25)


def backup(database, output):
    """SQLite online backup includes committed WAL content; never overwrite a backup."""
    database = database.resolve(strict=True)
    output = output.resolve()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(output) as dest:
                src.backup(dest)
                if dest.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise ValueError("backup integrity check failed")
    except BaseException:
        output.unlink()
        raise
    print(f"SQLite backup verified: {output}")


def safe_path(path):
    # Paths are embedded in systemd directives, not shell commands.
    value = str(Path(path).resolve())
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise ValueError("deployment paths must use ASCII letters, digits, / . _ -")
    return value


def directives(release, config):
    source = safe_path(release / "source")
    metadata = json.loads((release / "release.json").read_text())
    python = metadata["python"]
    if python != "/usr/bin/python3" and Path(python) != release / "venv/bin/python":
        raise ValueError("release interpreter is outside its virtual environment")
    # Preserve the venv symlink path: resolving it would lose venv isolation.
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", python):
        raise ValueError("unsupported interpreter path")
    common = f"[Service]\nWorkingDirectory={source}\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n"
    for name in ("DATA", "BUILD", "LOG", "PAGES"):
        common += f"Environment=WPY_{name}_DIR={safe_path(config['paths'][name])}\n"
    commands = {
        "wpy-board": ["-m", "wpy", "serve", "--host", "127.0.0.1", "--port", "8765", "--no-open"],
        "wpy-hot": ["-m", "wpy", "hot", "--host", "127.0.0.1", "--port", "8766"],
        "wpy-update": ["-m", "wpy", "update", *config["update_args"]],
        "wpy-refresh-stats": [f"{source}/scripts/refresh_stats.py", *config["stats_args"]],
        "wpy-refresh-all": [f"{source}/scripts/refresh_stats.py", *config["full_stats_args"]],
    }
    result = {}
    for unit, args in commands.items():
        if any(not re.fullmatch(r"[A-Za-z0-9_./-]+", arg) for arg in args):
            raise ValueError("unsupported systemd argument")
        result[unit] = common + "ExecStart=\nExecStart=" + " ".join([python, *args]) + "\n"
        if unit == "wpy-update":
            result[unit] += (
                f"ExecStartPost=\nExecStartPost={python} {source}/scripts/build_hot.py\n"
            )
    return result


def systemctl(*args, capture=False):
    return run(["systemctl", *args], stdout=subprocess.PIPE if capture else None, text=True)


def unit_state(unit):
    return systemctl("show", unit, "--property=ActiveState", "--value", capture=True).stdout.strip()


@contextmanager
def deployment_lock(state_dir):
    import fcntl

    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / "deploy.lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def apply_dropins(values):
    for unit in UNITS:
        directory = Path("/etc/systemd/system") / f"{unit}.service.d"
        path = directory / DROPIN
        if directory.is_symlink() or path.is_symlink():
            raise ValueError("refusing symlinked systemd override")
        if values[unit] is None:
            path.unlink(missing_ok=True)
        else:
            directory.mkdir(exist_ok=True)
            temporary = directory / (DROPIN + ".tmp")
            with temporary.open("x") as stream:
                stream.write(values[unit])
            temporary.replace(path)
    systemctl("daemon-reload")


def restore(transaction):
    apply_dropins(transaction["before"])
    for unit in WEB_UNITS:
        if transaction["active"][unit] == "active":
            systemctl("restart", unit)
        else:
            systemctl("stop", unit)
    wait_healthy([unit for unit in WEB_UNITS if transaction["active"][unit] == "active"])


def activate(release, config):
    release = release.resolve(strict=True)
    if release.parent != Path(config["release_root"]).resolve():
        raise ValueError("release must be directly inside configured release_root")
    manifest = json.loads((release / "release.json").read_text())
    if inventory(release / "source") != manifest["files"]:
        raise ValueError("release source no longer matches its manifest")
    desired = directives(release, config)
    for unit in UNITS[2:]:
        if unit_state(unit) not in ("inactive", "failed"):
            raise ValueError(f"{unit} is running; wait for it to finish")
    before = {}
    for unit in UNITS:
        path = Path("/etc/systemd/system") / f"{unit}.service.d" / DROPIN
        before[unit] = path.read_text() if path.exists() else None
    transaction = {
        "release": str(release),
        "before": before,
        "active": {unit: unit_state(unit) for unit in WEB_UNITS},
    }
    path = Path(config["state_dir"]) / f"switch-{time.time_ns()}.json"
    path.write_text(json.dumps(transaction, indent=2) + "\n")
    path.chmod(0o600)
    try:
        apply_dropins(desired)
        for unit in WEB_UNITS:
            if transaction["active"][unit] == "active":
                systemctl("restart", unit)
        wait_healthy([unit for unit in WEB_UNITS if transaction["active"][unit] == "active"])
    except BaseException:
        restore(transaction)
        print(f"Activation failed; previous overrides restored. Receipt: {path}", file=sys.stderr)
        raise
    transaction["after"] = desired
    path.write_text(json.dumps(transaction, indent=2) + "\n")
    print(f"Site code activated; databases unchanged. Rollback receipt: {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "deploy/runtime.local.json")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--ref", required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("health")
    p.add_argument(
        "--urls",
        nargs="+",
        default=["http://127.0.0.1:8765/healthz", "http://127.0.0.1:8766/healthz"],
    )
    p = sub.add_parser("backup")
    p.add_argument("database", type=Path)
    p.add_argument("output", type=Path)
    for command, field in (("activate", "release"), ("rollback", "receipt")):
        p = sub.add_parser(command)
        p.add_argument(field, type=Path)
        p.add_argument(
            "--data-compatible",
            action="store_true",
            required=True,
            help="Code change reviewed as compatible with current data; no database restore",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare(args.ref, args.output)
        elif args.command == "health":
            health(args.urls)
            print("WPY health checks passed")
        elif args.command == "backup":
            backup(args.database, args.output)
        else:
            if os.geteuid() != 0:
                raise ValueError("systemd release switching requires root")
            config = json.loads(args.config.read_text())
            with deployment_lock(Path(config["state_dir"])):
                if args.command == "activate":
                    activate(args.release, config)
                else:
                    transaction = json.loads(args.receipt.read_text())
                    current = {}
                    for unit in UNITS:
                        path = Path("/etc/systemd/system") / f"{unit}.service.d" / DROPIN
                        current[unit] = path.read_text() if path.exists() else None
                    if transaction.get("after") != current:
                        raise ValueError(
                            "site has changed since this receipt; use the newest receipt"
                        )
                    if any(unit_state(unit) not in ("inactive", "failed") for unit in UNITS[2:]):
                        raise ValueError("a crawler is running; wait for it to finish")
                    restore(transaction)
                    print("Previous site code restored; databases unchanged")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(
            f"Release operation failed ({type(error).__name__}); inspect local state before retrying",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
