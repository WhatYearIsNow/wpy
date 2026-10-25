"""检查候选发布源码中的常见凭据，不读取被忽略的运行数据、不打印秘密值。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = {".git", "data", "build", "dist", "logs", ".backup", ".venv", "venv"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "htmlcov"}
TEXT_SUFFIXES = {".py", ".md", ".js", ".css", ".json", ".toml", ".yml", ".yaml",
                 ".example", ".service", ".timer", ".txt", ".in"}
SECRET_NAME = r"(?:APP_SECRET|HOT_TOKEN|BOARD_TOKEN|API_KEY|ACCESS_TOKEN|PASSWORD|TOKEN)"
CREDENTIAL_VALUE = r"[A-Za-z0-9_./+=:@!$%&?~*-]{16,}"
LITERAL = re.compile(
    rf"\b{SECRET_NAME}\b[\"']?\s*(?:=|:)\s*([\"'])(?P<value>{CREDENTIAL_VALUE})\1",
    re.IGNORECASE,
)
FALLBACK = re.compile(
    rf"\b(?:getenv|environ\.get)\(\s*[\"'](?:WPY_)?{SECRET_NAME}[\"']"
    rf"\s*,\s*([\"'])(?P<value>{CREDENTIAL_VALUE})\1", re.IGNORECASE,
)
HEX = re.compile(r"\b[a-fA-F0-9]{32,}\b")
SERVICE_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})\b")
PRIVATE_KEY = re.compile(r"-{5}BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-{5}")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?P<domain>[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
PHONE = re.compile(r"\b1[3-9]\d{9}\b")
STUDENT_ID = re.compile(r"\b(?:20|30)\d{8,10}\b")


def example_email(domain: str) -> bool:
    domain = domain.lower()
    # GitHub 服务身份：平台自动生成的合并提交会用这些地址，不属于个人泄露
    if domain == "github.com" or domain.endswith(".github.com"):
        return True
    return (domain in {"example.com", "example.org", "example.net", "localhost"}
            or domain.endswith((".example", ".invalid", ".test", ".localhost", ".local")))


def placeholder(value: str) -> bool:
    value = value.lower()
    return (any(word in value for word in
                ("example", "placeholder", "your_", "your-", "custom_", "test_", "fake_", "dummy"))
            or len(set(value)) <= 1)


def scan_text(text: str) -> list[tuple[int, str]]:
    findings = set()
    for pattern, category in ((LITERAL, "credential literal"), (FALLBACK, "credential fallback"),
                              (HEX, "long hexadecimal literal"), (SERVICE_TOKEN, "service token"),
                              (PRIVATE_KEY, "private key")):
        for match in pattern.finditer(text):
            value = match.groupdict().get("value", match.group())
            if not placeholder(value):
                findings.add((text.count("\n", 0, match.start()) + 1, category))
    for match in EMAIL.finditer(text):
        if not example_email(match.group("domain")):
            findings.add((text.count("\n", 0, match.start()) + 1, "personal email"))
    for pattern, category in ((PHONE, "possible phone number"), (STUDENT_ID, "possible student identifier")):
        for match in pattern.finditer(text):
            findings.add((text.count("\n", 0, match.start()) + 1, category))
    return sorted(findings)


def private_path(path: Path) -> bool:
    name = path.name
    return (any(part in PRIVATE_DIRS for part in path.parts)
            or name in {".tju_key", "凭据.json", "credentials.json"}
            or (name.startswith(".env") and name != ".env.example")
            or (path.parts[0] == "deploy" and name.startswith("env.") and not name.endswith(".example"))
            or (path.parts[0] == "pages" and name.endswith(".json") and not name.endswith(".example.json"))
            or name.endswith((".db", ".sqlite", ".sqlite3", ".bak")) or ".bak-" in name)


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        stderr=subprocess.PIPE,
    )


def candidates(root: Path) -> list[Path]:
    if (root / ".git").exists():
        return sorted({Path(p.decode("utf-8", "surrogateescape")) for p in
                       git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0") if p})
    paths = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            relative = path.relative_to(root)
            if path.name in SKIP_DIRS or path.name.endswith(".egg-info"):
                continue
            if private_path(relative) or path.is_symlink():
                paths.append(relative)
            elif path.is_dir():
                pending.append(path)
            elif path.is_file():
                paths.append(relative)
    return sorted(paths)


def scan_paths(root: Path, paths: list[Path]) -> list[tuple[str, int, str]]:
    findings = []
    for relative in paths:
        path = root / relative
        if private_path(relative):
            findings.append((str(relative), 0, "private runtime file or directory"))
        elif path.is_symlink():
            findings.append((str(relative), 0, "symbolic link requires review"))
        elif path.is_file() and (path.suffix in TEXT_SUFFIXES or not path.suffix or path.name == ".gitignore"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if relative.parts[0] == "pages" and relative.name.endswith(".example.json"):
                try:
                    if json.loads(text) not in ({}, []):
                        findings.append((str(relative), 0, "page example must be empty"))
                except ValueError:
                    findings.append((str(relative), 0, "invalid page example"))
            findings.extend((str(relative), line, category) for line, category in scan_text(text))
    return findings


def scan_history(root: Path) -> list[tuple[str, int, str]]:
    findings = []
    for revision in git(root, "rev-list", "--all").decode().splitlines():
        metadata = git(root, "show", "-s", "--format=%an%n%ae%n%cn%n%ce", revision).decode("utf-8", "replace")
        findings.extend((f"history {revision[:12]} commit metadata", line, category)
                        for line, category in scan_text(metadata))
        for raw in git(root, "ls-tree", "-r", "--name-only", "-z", revision).split(b"\0"):
            if not raw:
                continue
            relative = Path(raw.decode("utf-8", "surrogateescape"))
            label = f"history {revision[:12]} {relative}"
            if private_path(relative):
                findings.append((label, 0, "private runtime file"))
            elif relative.suffix in TEXT_SUFFIXES or not relative.suffix:
                text = git(root, "show", f"{revision}:{relative.as_posix()}").decode("utf-8", "replace")
                findings.extend((label, line, category) for line, category in scan_text(text))
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--history", action="store_true", help="同时检查本地 Git 历史；只输出位置和类别")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        paths = candidates(root)
        findings = scan_paths(root, paths)
        if args.history:
            findings.extend(scan_history(root))
    except (OSError, subprocess.CalledProcessError):
        print("无法完成源码检查，请确认目录可读且 Git 可用。", file=sys.stderr)
        return 2
    for path, line, category in findings:
        print(f"{path}:{line}: {category}")
    if findings:
        print(f"发现 {len(findings)} 个待处理项；未打印任何凭据值。")
        return 1
    print(f"源码检查通过（{len(paths)} 个候选文件；常见凭据与私有路径检查）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
