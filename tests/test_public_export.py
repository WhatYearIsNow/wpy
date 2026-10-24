"""发布导出必须排除私有路径，并在验证失败时保留目标目录。"""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def source_exporter(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_public_source.py"
    spec = importlib.util.spec_from_file_location("source_exporter", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(module, "ROOT", root)
    for name in ("LICENSE", "README.md", "pyproject.toml"):
        (root / name).write_text("fixture\n")
    (root / "scripts").mkdir()
    # 本测试聚焦导出边界，源码扫描器的通过/失败由独立进程模拟。
    (root / "scripts" / "check_public_source.py").write_text("raise SystemExit(0)\n")
    return module, root, tmp_path / "public"


def test_export_copies_source_but_excludes_runtime_and_credentials(source_exporter):
    module, root, output = source_exporter
    for name in ("wpy/__init__.py", "pages/content.example.json", "deploy/env.update.example"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public fixture\n")
    for name in (".git/config", "data/sitewide.db", "build/hot.html", "logs/run.txt",
                 ".tju_key", "deploy/env.update", "deploy/.env", "wpy/__pycache__/config.pyc",
                 "docs/.backup/old.md", "pages/exp_add.json", "pages/exp_refined.json",
                 "pages/exp_search.json"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private fixture\n")

    assert module.export(output) == 7
    files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert files == {"LICENSE", "README.md", "pyproject.toml", "scripts/check_public_source.py",
                     "wpy/__init__.py", "pages/content.example.json", "deploy/env.update.example"}
    for name in ("exp_add.json", "exp_refined.json", "exp_search.json"):
        assert (root / "pages" / name).read_text() == "private fixture\n"
        assert not (output / "pages" / name).exists()


def test_export_includes_page_documentation_and_empty_template(source_exporter):
    module, root, output = source_exporter
    pages = root / "pages"
    pages.mkdir()
    (pages / "README.md").write_text("Local page contents stay private.\n")
    (pages / "content.example.json").write_text("{}\n")
    (pages / "local.json").write_text("private fixture\n")

    module.export(output)

    assert (output / "pages" / "README.md").is_file()
    assert (output / "pages" / "content.example.json").read_text() == "{}\n"
    assert not (output / "pages" / "local.json").exists()


def test_scan_failure_does_not_create_output(source_exporter):
    module, root, output = source_exporter
    (root / "scripts" / "check_public_source.py").write_text("raise SystemExit(1)\n")
    with pytest.raises(ValueError, match="源码检查失败"):
        module.export(output)
    assert not output.exists()


def test_export_refuses_existing_output(source_exporter):
    module, _, output = source_exporter
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="目标已存在"):
        module.export(output)
    assert marker.read_text() == "keep"


def test_export_refuses_output_inside_source(source_exporter):
    module, root, _ = source_exporter
    with pytest.raises(ValueError, match="项目目录之外"):
        module.export(root / "export")
    assert not (root / "export").exists()


def test_export_refuses_symlink_to_private_file(source_exporter):
    module, root, output = source_exporter
    secret = root / "private.txt"
    secret.write_text("private fixture")
    try:
        (root / "scripts" / "linked.py").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="符号链接"):
        module.export(output)
    assert not output.exists()
