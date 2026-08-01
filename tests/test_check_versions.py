"""Tests for the release version-consistency guard.

The guard exists because the version is declared in four files consumed by
four different channels, and only `pyproject.toml` reliably gets bumped. At
the v0.8.5 release, `manifest.json` and `server.json`'s package entry were
both still on 0.8.1 -- two releases behind -- because the 0.8.2 bump touched
only `pyproject.toml` and nothing checked.

The regression test that matters is `test_catches_the_historical_drift`: it
reconstructs the real pre-0.8.5 tree and asserts the guard would have
blocked that release.

`server.json`'s top-level "version" is deliberately excluded from the check
(it tracks the registry entry, not the PyPI package, and is on its own 1.x
line). `test_top_level_server_version_is_ignored` pins that exclusion so it
isn't "fixed" into a permanently failing check later.
"""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONED_FILES = ("pyproject.toml", "uv.lock", "manifest.json", "server.json")


def _load_module():
    """Load scripts/check_versions.py, which is not an installed package."""
    path = REPO_ROOT / "scripts" / "check_versions.py"
    spec = importlib.util.spec_from_file_location("check_versions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_versions = _load_module()


@pytest.fixture
def repo(tmp_path):
    """A throwaway copy of the repo's version-bearing files."""
    for filename in VERSIONED_FILES:
        shutil.copy(REPO_ROOT / filename, tmp_path / filename)
    return tmp_path


def _write_json(root: Path, filename: str, data) -> None:
    (root / filename).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_json(root: Path, filename: str):
    return json.loads((root / filename).read_text(encoding="utf-8"))


class TestCollectVersions:
    def test_collects_every_checked_version(self, repo):
        assert set(check_versions.collect_versions(repo)) == {
            "pyproject.toml:project.version",
            "uv.lock:windows-mcp",
            "manifest.json:version",
            "server.json:packages[0].version",
        }

    def test_top_level_server_version_is_not_collected(self, repo):
        """It tracks the registry entry, not the PyPI package."""
        labels = check_versions.collect_versions(repo)
        assert "server.json:version" not in labels

    def test_missing_manifest_version_is_an_error(self, repo):
        data = _read_json(repo, "manifest.json")
        del data["version"]
        _write_json(repo, "manifest.json", data)

        with pytest.raises(ValueError, match="manifest.json"):
            check_versions.collect_versions(repo)

    def test_missing_uv_lock_entry_is_an_error(self, repo):
        (repo / "uv.lock").write_text(
            '[[package]]\nname = "click"\nversion = "8.0.0"\n', encoding="utf-8"
        )

        with pytest.raises(ValueError, match="uv.lock"):
            check_versions.collect_versions(repo)

    def test_empty_packages_is_an_error(self, repo):
        data = _read_json(repo, "server.json")
        data["packages"] = []
        _write_json(repo, "server.json", data)

        with pytest.raises(ValueError, match="packages"):
            check_versions.collect_versions(repo)


class TestCheck:
    def test_repo_is_currently_consistent(self):
        """The real tree must stay releasable -- guards every future bump."""
        assert check_versions.check(None, REPO_ROOT) == []

    def test_consistent_repo_matches_its_own_version(self, repo):
        current = next(iter(check_versions.collect_versions(repo).values()))
        assert check_versions.check(current, repo) == []

    def test_v_prefixed_tag_is_accepted(self, repo):
        """publish.yml passes github.ref_name, which is tag-shaped."""
        current = next(iter(check_versions.collect_versions(repo).values()))
        assert check_versions.check(f"v{current}", repo) == []

    def test_catches_the_historical_drift(self, repo):
        """Reconstruct the real pre-0.8.5 tree; the guard must block it."""
        pyproject = repo / "pyproject.toml"
        current = next(iter(check_versions.collect_versions(repo).values()))
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8").replace(
                f'version = "{current}"', 'version = "0.8.2"', 1
            ),
            encoding="utf-8",
        )
        lock = repo / "uv.lock"
        lock.write_text(
            lock.read_text(encoding="utf-8").replace(
                f'name = "windows-mcp"\nversion = "{current}"',
                'name = "windows-mcp"\nversion = "0.8.2"',
            ),
            encoding="utf-8",
        )
        manifest = _read_json(repo, "manifest.json")
        manifest["version"] = "0.8.1"
        _write_json(repo, "manifest.json", manifest)
        server = _read_json(repo, "server.json")
        server["packages"][0]["version"] = "0.8.1"
        _write_json(repo, "server.json", server)

        problems = check_versions.check("v0.8.2", repo)

        assert problems, "the guard must reject the release that shipped the drift"
        assert any("disagree" in problem for problem in problems)

    def test_top_level_server_version_is_ignored(self, repo):
        """A divergent top-level version must not fail the check."""
        server = _read_json(repo, "server.json")
        server["version"] = "1.0.1"
        _write_json(repo, "server.json", server)

        assert check_versions.check(None, repo) == []

    def test_catches_single_stale_file(self, repo):
        manifest = _read_json(repo, "manifest.json")
        manifest["version"] = "0.0.1"
        _write_json(repo, "manifest.json", manifest)

        assert check_versions.check(None, repo) != []

    def test_catches_consistent_repo_not_matching_tag(self, repo):
        """Internally consistent but tagged wrong is still a bad release."""
        assert any("99.0.0" in p for p in check_versions.check("v99.0.0", repo))


class TestMainExitCodes:
    """The workflow depends on the process exit status."""

    def test_exit_zero_on_consistent_repo(self, capsys):
        assert check_versions.main(["check_versions.py"]) == 0

    def test_exit_one_on_tag_mismatch(self, capsys):
        assert check_versions.main(["check_versions.py", "v99.0.0"]) == 1
