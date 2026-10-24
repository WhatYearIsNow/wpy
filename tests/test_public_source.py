"""源码扫描须报告位置而不泄露值，且不读取私有文件。"""
import importlib.util
from pathlib import Path
import subprocess


_script = Path(__file__).resolve().parents[1] / "scripts" / "check_public_source.py"
_spec = importlib.util.spec_from_file_location("source_checker", _script)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def test_secret_literals_and_environment_fallback_are_detected():
    value = "a1B2c3D4" * 4
    for text in (f'APP_SECRET = "{value}"', f'TOKEN = "{value}"',
                 f'os.environ.get("WPY_APP_SECRET", "{value}")'):
        findings = checker.scan_text(text)
        assert findings
        assert all(line == 1 and value not in category for line, category in findings)


def test_runtime_environment_and_placeholders_are_allowed():
    text = 'APP_SECRET = os.environ.get("WPY_APP_SECRET", "")\nTOKEN = "your_example_token_here"'
    assert checker.scan_text(text) == []


def test_service_tokens_are_reported_without_values():
    value = "ghp_" + "a1B2c3D4" * 5
    findings = checker.scan_text("first line\n" + value)
    assert findings == [(2, "service token")]
    assert value not in repr(findings)


def test_private_files_are_not_opened(tmp_path, monkeypatch):
    private = tmp_path / "deploy" / "env.update"
    private.parent.mkdir()
    private.write_text("private contents")

    def reject_read(*args, **kwargs):
        raise AssertionError("private file must not be read")

    monkeypatch.setattr(Path, "read_text", reject_read)
    findings = checker.scan_paths(tmp_path, [Path("deploy/env.update")])
    assert findings == [("deploy/env.update", 0, "private runtime file or directory")]


def test_snapshot_reports_private_directories_without_descending(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "sitewide.db").write_text("not read")
    (tmp_path / "README.md").write_text("public text")
    paths = checker.candidates(tmp_path)
    assert Path("data") in paths
    assert Path("data/sitewide.db") not in paths
    assert checker.scan_paths(tmp_path, paths) == [("data", 0, "private runtime file or directory")]


def test_personal_identifiers_are_reported_without_values():
    values = ["person" + "@" + "mail.invalid-domain.com", "13" + "012345678", "30" + "12345678"]
    expected = ["personal email", "possible phone number", "possible student identifier"]
    for value, category in zip(values, expected):
        findings = checker.scan_text(value)
        assert (1, category) in findings
        assert value not in repr(findings)


def test_reserved_example_addresses_are_allowed():
    for domain in ("example.com", "example.org", "example.net", "example.test", "host.local"):
        assert checker.scan_text("maintainer" + "@" + domain) == []


def test_local_pages_are_rejected_without_reading(tmp_path, monkeypatch):
    path = tmp_path / "pages" / "local.json"
    path.parent.mkdir()
    path.write_text("local data")

    def reject_read(*args, **kwargs):
        raise AssertionError("local page data must not be read")

    monkeypatch.setattr(Path, "read_text", reject_read)
    assert checker.scan_paths(tmp_path, [Path("pages/local.json")]) == [
        ("pages/local.json", 0, "private runtime file or directory")]


def test_page_templates_must_be_empty(tmp_path):
    path = tmp_path / "pages" / "content.example.json"
    path.parent.mkdir()
    path.write_text("{}\n")
    assert checker.scan_paths(tmp_path, [Path("pages/content.example.json")]) == []
    path.write_text('{"author": "nonempty content"}')
    assert checker.scan_paths(tmp_path, [Path("pages/content.example.json")]) == [
        ("pages/content.example.json", 0, "page example must be empty")]


def test_history_checks_commit_identity_without_printing_address(tmp_path):
    address = "person" + "@" + "mail.invalid-domain.com"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=Test Contributor",
                    "-c", "user.email=" + address, "commit", "--allow-empty", "-qm", "fixture"],
                   check=True)
    findings = checker.scan_history(tmp_path)
    assert any(category == "personal email" for _, _, category in findings)
    assert address not in repr(findings)
