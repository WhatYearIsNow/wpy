"""导出不含本地数据和 Git 历史的源码目录；只创建新目录，不覆盖目标。"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = {"wpy", "scripts", "tests", "tools", "deploy", "docs", "pages", ".github"}
SOURCE_FILES = {
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "pyproject.toml",
    "MANIFEST.in", ".gitignore", ".gitattributes", "ask.py", "AGENTS.md",
}
PRIVATE_DIRS = {
    ".git", ".backup", "data", "build", "dist", "logs", "__pycache__",
    ".pytest_cache", ".venv", "venv", "htmlcov",
}


def public_path(path: Path) -> bool:
    """路径白名单与本地文件排除同时应用，不依赖当前 Git 忽略规则。"""
    if any(part in PRIVATE_DIRS or part.endswith(".egg-info") for part in path.parts):
        return False
    name = path.name
    if name.startswith(".env") and name != ".env.example":
        return False
    if (name == ".tju_key" or name.startswith(".coverage")
            or name.endswith((".pyc", ".pyo", ".bak", ".tmp", ".db", ".sqlite", ".sqlite3"))
            or ".bak-" in name):
        return False
    if path.parts[0] == "deploy" and name.startswith("env.") and not name.endswith(".example"):
        return False
    if path.parts[0] == "pages" and name.endswith(".json") and not name.endswith(".example.json"):
        return False
    return path.parts[0] in SOURCE_DIRS or (len(path.parts) == 1 and name in SOURCE_FILES)


def export(output: Path) -> int:
    output = output.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        raise ValueError("导出目录必须位于项目目录之外")
    if output.exists():
        raise ValueError("目标已存在，请指定新的目录（不会覆盖现有文件）")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".wpy-source-", dir=output.parent) as work:
        stage = Path(work) / "source"
        stage.mkdir()
        count = 0
        # 仅遍历源码目录，不进入数据库、密钥、备份或 Git 历史目录。
        candidates = [ROOT / name for name in sorted(SOURCE_FILES | SOURCE_DIRS)]
        while candidates:
            src = candidates.pop()
            relative = src.relative_to(ROOT)
            if not public_path(relative):
                continue
            if src.is_symlink():
                raise ValueError(f"源码中含符号链接，需人工检查：{relative}")
            if src.is_dir():
                candidates.extend(sorted(src.iterdir()))
            elif src.is_file():
                dest = stage / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                count += 1

        for required in ("LICENSE", "README.md", "pyproject.toml", "scripts/check_public_source.py"):
            if not (stage / required).is_file():
                raise ValueError(f"缺少发布文件：{required}")
        result = subprocess.run(
            [sys.executable, str(stage / "scripts" / "check_public_source.py")],
            cwd=stage, check=False,
        )
        if result.returncode:
            raise ValueError("源码检查失败，未生成发布目录")
        # rename 在同一文件系统完成；目标必须为新目录。
        stage.rename(output)
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="项目目录外的新目录")
    args = parser.parse_args(argv)
    try:
        count = export(args.output)
    except (OSError, ValueError) as error:
        print(f"导出失败：{error}", file=sys.stderr)
        return 1
    print(f"已导出 {count} 个源码文件：{args.output.expanduser().resolve()}")
    print("未复制 Git 历史、数据库、本地页面内容、运行日志、生成产物或本地环境文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
