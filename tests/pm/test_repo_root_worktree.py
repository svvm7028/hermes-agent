"""Regression tests for pm.paths.repo_root() worktree resolution.

Tests cover two critical cases:
1. cwd inside a git worktree that belongs to the same repo as the module's physical location
   → repo_root() resolves to the worktree root
2. cwd inside an unrelated git repo (no relationship to the Hermes install)
   → repo_root() does NOT redirect to that unrelated repo; falls back to the real Hermes install tree
"""

import os
import sys
import subprocess
import importlib.util
from pathlib import Path

import pytest


def make_repo_with_worktree(base: Path) -> tuple[Path, Path]:
    """Create a main repo and a worktree under base. Returns (main_repo, worktree)."""
    repo = base / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    (repo / "pyproject.toml").write_text('[project]\nname="test-repo"\nversion="0.1"\n')
    (repo / "__init__.py").write_text("\n")
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=repo, check=True)
    worktree_dir = base / "worktree"
    subprocess.run(["git", "worktree", "add", str(worktree_dir), "HEAD"], cwd=repo, check=True)
    return repo, worktree_dir


def make_unrelated_repo(base: Path) -> Path:
    """Create a completely unrelated git repo (different origin)."""
    repo = base / "unrelated"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Unrelated Repo\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=repo, check=True)
    return repo


def write_test_paths_module(target_dir: Path, module_repo_root: Path | None = None) -> Path:
    """Write a test pm.paths module to target_dir/pm/paths.py.
    
    Reads the ACTUAL pm/paths.py source from the real repo to avoid staleness.
    The test file lives in tests/pm/, so the real pm/paths.py is at parents[2] / "pm" / "paths.py".
    """
    pm_dir = target_dir / "pm"
    pm_dir.mkdir(parents=True, exist_ok=True)
    
    # Read the real pm/paths.py from the repo (this test file is in tests/pm/)
    repo_root = Path(__file__).resolve().parents[2]
    real_paths_py = repo_root / "pm" / "paths.py"
    paths_py = real_paths_py.read_text()
    
    (pm_dir / "paths.py").write_text(paths_py)
    (pm_dir / "__init__.py").write_text("")
    return pm_dir


def load_module_from_path(module_name: str, file_path: Path):
    """Load a Python module from a specific file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hermes_repo_structure(tmp_path):
    """Create a fake Hermes repo structure with a worktree for testing."""
    # Create the "Hermes" repo (where the module actually lives)
    hermes_repo = tmp_path / "hermes-repo"
    hermes_repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=hermes_repo, check=True)
    (hermes_repo / "README.md").write_text("# Hermes Repo\n")
    subprocess.run(["git", "add", "."], cwd=hermes_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=hermes_repo, check=True)

    # Create a worktree of the Hermes repo
    hermes_worktree = tmp_path / "hermes-worktree"
    subprocess.run(["git", "worktree", "add", str(hermes_worktree), "HEAD"], cwd=hermes_repo, check=True)

    # Write test module to the worktree (so __file__ resolves to worktree)
    write_test_paths_module(hermes_worktree)

    return hermes_repo, hermes_worktree


def test_repo_root_resolves_to_hermes_worktree(hermes_repo_structure):
    """Case 1a: cwd inside a Hermes git worktree → repo_root() returns worktree root.

    This is the original bug fix: when running from inside a worktree of the SAME
    repo where pm/paths.py lives, we should resolve to the worktree root.
    """
    hermes_repo, hermes_worktree = hermes_repo_structure

    # Change to the worktree and test
    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_worktree)

        # Load the test module from the worktree
        test_module = load_module_from_path("test_pm_paths", hermes_worktree / "pm" / "paths.py")
        
        rr = test_module.repo_root().resolve()
        assert rr == hermes_worktree.resolve(), (
            f"repo_root()={rr} but expected worktree={hermes_worktree.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        # Clean up
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_resolves_to_main_repo_when_in_main(hermes_repo_structure):
    """Case 1b: cwd inside the main Hermes repo → repo_root() returns main repo root."""
    hermes_repo, hermes_worktree = hermes_repo_structure

    # Also write test module to main repo
    write_test_paths_module(hermes_repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_repo)

        # Load the test module from the main repo
        test_module = load_module_from_path("test_pm_paths_main", hermes_repo / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        assert rr == hermes_repo.resolve(), (
            f"repo_root()={rr} but expected main repo={hermes_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_does_not_redirect_to_unrelated_repo(tmp_path):
    """Case 2: cwd inside an UNRELATED git repo → repo_root() does NOT redirect to it.

    This is the critical security/correctness fix: a pip/pipx/git-installed Hermes user
    running `hermes chat` from inside their OWN unrelated git-tracked project must NOT
    have PM's install verification, venv selection, etc. silently redirected to their
    project directory.
    """
    # Create an "installed" Hermes location (simulating pip/pipx install - no .git)
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    write_test_paths_module(installed_hermes)  # No .git at this location

    # Create an UNRELATED git repo (user's project)
    unrelated_repo = make_unrelated_repo(tmp_path)

    # Change to the unrelated repo and test
    old_cwd = os.getcwd()
    try:
        os.chdir(unrelated_repo)

        # Load the test module from the installed hermes location
        test_module = load_module_from_path("test_pm_paths_installed", installed_hermes / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        # MUST return the installed hermes location, NOT the unrelated repo
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}. "
            f"BUG: Redirected to unrelated repo={unrelated_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_does_not_redirect_to_unrelated_repo_nested(tmp_path):
    """Case 2 nested: cwd inside a subdirectory of an unrelated git repo."""
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    write_test_paths_module(installed_hermes)

    # Create an UNRELATED git repo with a subdirectory
    unrelated_repo = make_unrelated_repo(tmp_path)
    subdir = unrelated_repo / "subdir"
    subdir.mkdir()

    old_cwd = os.getcwd()
    try:
        os.chdir(subdir)

        test_module = load_module_from_path("test_pm_paths_installed2", installed_hermes / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}. "
            f"BUG: Redirected to unrelated repo={unrelated_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_installed_no_git_fallback(tmp_path):
    """When installed (no .git at module location), always fall back to module location."""
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    write_test_paths_module(installed_hermes)  # No .git at this location

    # Create an unrelated repo
    unrelated_repo = make_unrelated_repo(tmp_path)

    old_cwd = os.getcwd()
    try:
        os.chdir(unrelated_repo)

        test_module = load_module_from_path("test_pm_paths_installed3", installed_hermes / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_same_repo_not_worktree(tmp_path):
    """Running from main repo (not worktree) of the same repo as module → main repo root."""
    hermes_repo = tmp_path / "hermes-repo"
    hermes_repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=hermes_repo, check=True)
    (hermes_repo / "README.md").write_text("# Hermes\n")
    subprocess.run(["git", "add", "."], cwd=hermes_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=hermes_repo, check=True)

    write_test_paths_module(hermes_repo)

    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_repo)

        test_module = load_module_from_path("test_pm_paths_main2", hermes_repo / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        assert rr == hermes_repo.resolve(), (
            f"repo_root()={rr} but expected hermes_repo={hermes_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]


def test_repo_root_nested_worktree_under_main_repo(tmp_path):
    """Case 1a (nested topology): Module in main repo, cwd in a worktree NESTED under main repo.

    This is the exact topology this project uses: the pm/paths.py module physically lives in
    the MAIN repo (e.g. /Users/.../.hermes/hermes-agent), and kanban workspaces are worktrees
    nested under it (e.g. .worktrees/t_d9977f07). When running from the nested worktree,
    repo_root() must resolve to the worktree root, not the outer main repo.

    The bug was a short-circuit that returned the module's repo root whenever cwd was under
    it, BEFORE checking for a closer nested worktree .git file.
    """
    # Create the "Hermes" main repo (where the module actually lives)
    hermes_repo = tmp_path / "hermes-repo"
    hermes_repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=hermes_repo, check=True)
    (hermes_repo / "README.md").write_text("# Hermes Repo\n")
    subprocess.run(["git", "add", "."], cwd=hermes_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=hermes_repo, check=True)

    # Create a worktree NESTED under the main repo (not a tmp_path sibling!)
    # This mimics .worktrees/<task> under the main hermes-agent repo
    nested_worktree = hermes_repo / ".worktrees" / "nested-task"
    nested_worktree.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", str(nested_worktree), "HEAD"], cwd=hermes_repo, check=True)

    # Write test module to the MAIN repo (not the worktree)
    # This simulates pm/paths.py living in the main hermes-agent repo
    write_test_paths_module(hermes_repo)

    # Change to the NESTED worktree and test
    old_cwd = os.getcwd()
    try:
        os.chdir(nested_worktree)

        # Load the test module from the MAIN repo location
        test_module = load_module_from_path("test_pm_paths_nested", hermes_repo / "pm" / "paths.py")

        rr = test_module.repo_root().resolve()
        # MUST resolve to the worktree root, NOT the main repo
        assert rr == nested_worktree.resolve(), (
            f"repo_root()={rr} but expected nested worktree={nested_worktree.resolve()}. "
            f"BUG: Returned main repo={hermes_repo.resolve()} instead of worktree root"
        )
    finally:
        os.chdir(old_cwd)
        for key in list(sys.modules.keys()):
            if key.startswith("test_pm_paths"):
                del sys.modules[key]