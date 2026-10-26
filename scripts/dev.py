"""WPY development entry points; never load deployment environments or production data."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def isolated_env(base: Path) -> dict[str, str]:
    """Remove inherited WPY credentials before importing any WPY modules."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("WPY_")}
    env.pop("PYTHONPATH", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("DATA", "BUILD", "LOG", "PAGES"):
        env[f"WPY_{name}_DIR"] = str(base / name.lower())
    env["WPY_NOTIFY_STATE_DIR"] = str(base / "notify-state")
    env["WPY_AI_STATE_DIR"] = str(base / "agent-state")
    env["WPY_HOST"] = "127.0.0.1"
    # A separate, ephemeral development token; never reuse the deployed shared token.
    env["WPY_BOARD_TOKEN"] = secrets.token_urlsafe(32)
    return env


def run(args: list[str], env: dict[str, str]) -> None:
    subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True)


def build_preview(env):
    run(
        [
            "-c",
            "from wpy.db import open_db; from wpy.config import DB_PATH; open_db(DB_PATH).close()",
        ],
        env,
    )
    run(["-m", "wpy", "build"], env)
    run(["scripts/build_hot.py"], env)


def preview_hot(env, port):
    """The original hot proxy targets production 8765; replace it only in preview."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        board_port = sock.getsockname()[1]
    if board_port in (8765, 8766):
        raise ValueError("refusing a production port for the preview backend")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "wpy",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(board_port),
            "--no-open",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        os.environ.clear()
        os.environ.update(env)
        from wpy import hot_server

        class PreviewHandler(hot_server._Handler):
            def _proxy_api(self, path, query_string):
                url = f"http://127.0.0.1:{board_port}{path}"
                if query_string:
                    url += "?" + query_string
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                # Ports share browser cookies: never forward a cookie from production.
                guest = "wpy_session=guest." + self._session_sig("guest")
                request = urllib.request.Request(url, headers={"Cookie": guest})
                try:
                    with opener.open(request, timeout=5) as response:
                        return self._send(
                            response.read(),
                            response.headers.get("Content-Type", "application/json"),
                            response.status,
                        )
                except OSError:
                    return self._json({"error": "isolated preview backend unavailable"}, 502)

        hot_server._Handler = PreviewHandler
        hot_server.serve(host="127.0.0.1", port=port)
    finally:
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend.kill()
            backend.wait()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("serve", "hot", "build", "test", "check", "format"))
    parser.add_argument("--port", type=int, help="Loopback preview port; default 18765/18766")
    args = parser.parse_args(argv)
    if args.port is not None and (
        args.command not in ("serve", "hot") or not 1024 <= args.port <= 65535
    ):
        parser.error("--port is only for serve/hot and must be between 1024 and 65535")
    if args.port in (8765, 8766):
        parser.error("8765/8766 belong to the deployed WPY services")
    try:
        if args.command in ("serve", "hot", "build"):
            base = ROOT / ".local" / "dev"
            base.mkdir(parents=True, exist_ok=True, mode=0o700)
            env = isolated_env(base)
            if args.command == "build":
                build_preview(env)
            else:
                port = args.port or (18765 if args.command == "serve" else 18766)
                env["WPY_PORT"] = str(port)
                if args.command == "hot":
                    build_preview(env)
                    print(f"Preview: http://127.0.0.1:{port}/ (isolated hot + backend)", flush=True)
                    preview_hot(env, port)
                    return 0
                cmd = ["-m", "wpy", args.command, "--host", "127.0.0.1", "--port", str(port)]
                if args.command == "serve":
                    cmd.append("--no-open")
                print(f"Preview: http://127.0.0.1:{port}/ (isolated empty/local data)", flush=True)
                run(cmd, env)
        else:
            (ROOT / ".local").mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="test-", dir=ROOT / ".local") as work:
                env = isolated_env(Path(work))
                if args.command in ("check", "format"):
                    run(
                        [
                            "-m",
                            "ruff",
                            "format",
                            *(["--check"] if args.command == "check" else []),
                            ".",
                        ],
                        env,
                    )
                    run(["-m", "ruff", "check", "."], env)
                if args.command in ("test", "check"):
                    run(["-m", "pytest", "-q"], env)
                    run(["tools/offline_stage_smoke.py"], env)
                if args.command == "check":
                    run(["scripts/check_public_source.py", "--history"], env)
    except subprocess.CalledProcessError as error:
        return error.returncode
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
