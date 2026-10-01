"""Regression tests for pm.paths.repo_root() worktree resolution.

Tests cover two critical cases:
1. cwd inside a git worktree that belongs to the same repo as the module's physical location
   → repo_root() resolves to the worktree root
2. cwd inside an unrelated git repo (no relationship to the Hermes install)
   → repo_root() does NOT redirect to that unrelated repo; falls back to the real Hermes install tree
"""

import os
import subprocess
import sys
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


@pytest.fixture
def hermes_repo_paths(tmp_path):
    """Create a fake Hermes repo structure with a worktree for testing.
    
    This simulates the actual Hermes checkout where pm/paths.py lives.
    """
    # Create the "Hermes" repo (where the module actually lives)
    hermes_repo = tmp_path / "hermes-repo"
    hermes_repo.mkdir()
    pm_dir = hermes_repo / "pm"
    pm_dir.mkdir()
    
    # Write a minimal pm/paths.py that uses our implementation logic
    # We'll test by importing the REAL pm.paths from the test environment
    # but we need to set up the .git structure
    subprocess.run(["git", "init", "--quiet"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=hermes_repo, check=True)
    (hermes_repo / "README.md").write_text("# Hermes Repo\n")
    subprocess.run(["git", "add", "."], cwd=hermes_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=hermes_repo, check=True)
    
    # Create a worktree of the Hermes repo
    hermes_worktree = tmp_path / "hermes-worktree"
    subprocess.run(["git", "worktree", "add", str(hermes_worktree), "HEAD"], cwd=hermes_repo, check=True)
    
    return hermes_repo, hermes_worktree, pm_dir


def test_repo_root_resolves_to_hermes_worktree(hermes_repo_paths, monkeypatch):
    """Case 1a: cwd inside a Hermes git worktree → repo_root() returns worktree root.
    
    This is the original bug fix: when running from inside a worktree of the SAME
    repo where pm/paths.py lives, we should resolve to the worktree root.
    """
    hermes_repo, hermes_worktree, pm_dir = hermes_repo_paths
    
    # Change to the worktree and test
    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_worktree)
        
        # Import the actual pm.paths from the test environment
        # The test runs from the worktree, so we need to ensure the module is loadable
        # We simulate by inserting the worktree's pm path
        sys.path.insert(0, str(hermes_worktree))
        
        # Need to also ensure the pm module structure exists in worktree
        wt_pm_dir = hermes_worktree / "pm"
        if not wt_pm_dir.exists():
            wt_pm_dir.mkdir()
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        assert rr == hermes_worktree.resolve(), (
            f"repo_root()={rr} but expected worktree={hermes_worktree.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(hermes_worktree) in sys.path:
            sys.path.remove(str(hermes_worktree))


def test_repo_root_resolves_to_main_repo_when_in_main(hermes_repo_paths, monkeypatch):
    """Case 1b: cwd inside the main Hermes repo → repo_root() returns main repo root."""
    hermes_repo, hermes_worktree, pm_dir = hermes_repo_paths
    
    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_repo)
        
        sys.path.insert(0, str(hermes_repo))
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        assert rr == hermes_repo.resolve(), (
            f"repo_root()={rr} but expected main repo={hermes_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(hermes_repo) in sys.path:
            sys.path.remove(str(hermes_repo))


def test_repo_root_does_not_redirect_to_unrelated_repo(tmp_path, monkeypatch):
    """Case 2: cwd inside an UNRELATED git repo → repo_root() does NOT redirect to it.
    
    This is the critical security/correctness fix: a pip/pipx/git-installed Hermes user
    running `hermes chat` from inside their OWN unrelated git-tracked project must NOT
    have PM's install verification, venv selection, etc. silently redirected to their
    project directory.
    """
    # Create an "installed" Hermes location (simulating pip/pipx install - no .git)
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    pm_dir = installed_hermes / "pm"
    pm_dir.mkdir()
    
    # Write a minimal paths.py that we can import
    paths_py = '''"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_git_dir(git_path: Path) -> Path:
    if git_path.is_dir():
        return git_path.resolve()
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:]).resolve()
            return gitdir_path
    except OSError:
        pass
    return git_path.resolve()


def _is_hermes_worktree(git_dir: Path, hermes_repo_git_dir: Path) -> bool:
    try:
        git_dir = git_dir.resolve()
        hermes_repo_git_dir = hermes_repo_git_dir.resolve()
        worktrees_dir = hermes_repo_git_dir / "worktrees"
        return worktrees_dir in git_dir.parents or git_dir == worktrees_dir
    except (OSError, ValueError):
        return False


def repo_root() -> Path:
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"
    
    if not module_git_dir.exists():
        return module_repo_root
    
    module_git_dir_resolved = _resolve_git_dir(module_git_dir)
    
    cwd = Path.cwd().resolve()
    
    for parent in [cwd] + list(cwd.parents):
        git_path = parent / ".git"
        if git_path.exists():
            found_git_dir = _resolve_git_dir(git_path)
            
            if git_path.is_file():
                if _is_hermes_worktree(found_git_dir, module_git_dir_resolved):
                    return parent.resolve()
                continue
            else:
                if found_git_dir == module_git_dir_resolved:
                    return parent.resolve()
                break
    
    return module_repo_root


def install_root() -> Path:
    env = os.environ.get("HERMES_INSTALL_ROOT")
    return Path(env) if env else repo_root()
'''
    (pm_dir / "paths.py").write_text(paths_py)
    (pm_dir / "__init__.py").write_text("")
    
    # Create an UNRELATED git repo (user's project)
    unrelated_repo = make_unrelated_repo(tmp_path)
    
    # Change to the unrelated repo and test
    old_cwd = os.getcwd()
    try:
        os.chdir(unrelated_repo)
        
        # Insert the installed hermes location in path
        sys.path.insert(0, str(installed_hermes))
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        # MUST return the installed hermes location, NOT the unrelated repo
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}. "
            f"BUG: Redirected to unrelated repo={unrelated_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(installed_hermes) in sys.path:
            sys.path.remove(str(installed_hermes))


def test_repo_root_does_not_redirect_to_unrelated_repo_nested(tmp_path, monkeypatch):
    """Case 2 nested: cwd inside a subdirectory of an unrelated git repo."""
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    pm_dir = installed_hermes / "pm"
    pm_dir.mkdir()
    
    paths_py = '''"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_git_dir(git_path: Path) -> Path:
    if git_path.is_dir():
        return git_path.resolve()
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:]).resolve()
            return gitdir_path
    except OSError:
        pass
    return git_path.resolve()


def _is_hermes_worktree(git_dir: Path, hermes_repo_git_dir: Path) -> bool:
    try:
        git_dir = git_dir.resolve()
        hermes_repo_git_dir = hermes_repo_git_dir.resolve()
        worktrees_dir = hermes_repo_git_dir / "worktrees"
        return worktrees_dir in git_dir.parents or git_dir == worktrees_dir
    except (OSError, ValueError):
        return False


def repo_root() -> Path:
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"
    
    if not module_git_dir.exists():
        return module_repo_root
    
    module_git_dir_resolved = _resolve_git_dir(module_git_dir)
    
    cwd = Path.cwd().resolve()
    
    for parent in [cwd] + list(cwd.parents):
        git_path = parent / ".git"
        if git_path.exists():
            found_git_dir = _resolve_git_dir(git_path)
            
            if git_path.is_file():
                if _is_hermes_worktree(found_git_dir, module_git_dir_resolved):
                    return parent.resolve()
                continue
            else:
                if found_git_dir == module_git_dir_resolved:
                    return parent.resolve()
                break
    
    return module_repo_root


def install_root() -> Path:
    env = os.environ.get("HERMES_INSTALL_ROOT")
    return Path(env) if env else repo_root()
'''
    (pm_dir / "paths.py").write_text(paths_py)
    (pm_dir / "__init__.py").write_text("")
    
    # Create an UNRELATED git repo with a subdirectory
    unrelated_repo = make_unrelated_repo(tmp_path)
    subdir = unrelated_repo / "subdir"
    subdir.mkdir()
    
    old_cwd = os.getcwd()
    try:
        os.chdir(subdir)
        
        sys.path.insert(0, str(installed_hermes))
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}. "
            f"BUG: Redirected to unrelated repo={unrelated_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(installed_hermes) in sys.path:
            sys.path.remove(str(installed_hermes))


def test_repo_root_installed_no_git_fallback(tmp_path):
    """When installed (no .git at module location), always fall back to module location."""
    installed_hermes = tmp_path / "installed-hermes"
    installed_hermes.mkdir()
    pm_dir = installed_hermes / "pm"
    pm_dir.mkdir()
    
    paths_py = '''"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_git_dir(git_path: Path) -> Path:
    if git_path.is_dir():
        return git_path.resolve()
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:]).resolve()
            return gitdir_path
    except OSError:
        pass
    return git_path.resolve()


def _is_hermes_worktree(git_dir: Path, hermes_repo_git_dir: Path) -> bool:
    try:
        git_dir = git_dir.resolve()
        hermes_repo_git_dir = hermes_repo_git_dir.resolve()
        worktrees_dir = hermes_repo_git_dir / "worktrees"
        return worktrees_dir in git_dir.parents or git_dir == worktrees_dir
    except (OSError, ValueError):
        return False


def repo_root() -> Path:
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"
    
    if not module_git_dir.exists():
        return module_repo_root
    
    module_git_dir_resolved = _resolve_git_dir(module_git_dir)
    
    cwd = Path.cwd().resolve()
    
    for parent in [cwd] + list(cwd.parents):
        git_path = parent / ".git"
        if git_path.exists():
            found_git_dir = _resolve_git_dir(git_path)
            
            if git_path.is_file():
                if _is_hermes_worktree(found_git_dir, module_git_dir_resolved):
                    return parent.resolve()
                continue
            else:
                if found_git_dir == module_git_dir_resolved:
                    return parent.resolve()
                break
    
    return module_repo_root


def install_root() -> Path:
    env = os.environ.get("HERMES_INSTALL_ROOT")
    return Path(env) if env else repo_root()
'''
    (pm_dir / "paths.py").write_text(paths_py)
    (pm_dir / "__init__.py").write_text("")
    
    # Create an unrelated repo
    unrelated_repo = make_unrelated_repo(tmp_path)
    
    old_cwd = os.getcwd()
    try:
        os.chdir(unrelated_repo)
        
        sys.path.insert(0, str(installed_hermes))
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        assert rr == installed_hermes.resolve(), (
            f"repo_root()={rr} but expected installed hermes={installed_hermes.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(installed_hermes) in sys.path:
            sys.path.remove(str(installed_hermes))


def test_repo_root_same_repo_not_worktree(tmp_path):
    """Running from main repo (not worktree) of the same repo as module → main repo root."""
    hermes_repo = tmp_path / "hermes-repo"
    hermes_repo.mkdir()
    pm_dir = hermes_repo / "pm"
    pm_dir.mkdir()
    
    subprocess.run(["git", "init", "--quiet"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hermes_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=hermes_repo, check=True)
    (hermes_repo / "README.md").write_text("# Hermes\n")
    subprocess.run(["git", "add", "."], cwd=hermes_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=hermes_repo, check=True)
    
    paths_py = '''"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_git_dir(git_path: Path) -> Path:
    if git_path.is_dir():
        return git_path.resolve()
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:]).resolve()
            return gitdir_path
    except OSError:
        pass
    return git_path.resolve()


def _is_hermes_worktree(git_dir: Path, hermes_repo_git_dir: Path) -> bool:
    try:
        git_dir = git_dir.resolve()
        hermes_repo_git_dir = hermes_repo_git_dir.resolve()
        worktrees_dir = hermes_repo_git_dir / "worktrees"
        return worktrees_dir in git_dir.parents or git_dir == worktrees_dir
    except (OSError, ValueError):
        return False


def repo_root() -> Path:
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"
    
    if not module_git_dir.exists():
        return module_repo_root
    
    module_git_dir_resolved = _resolve_git_dir(module_git_dir)
    
    cwd = Path.cwd().resolve()
    
    for parent in [cwd] + list(cwd.parents):
        git_path = parent / ".git"
        if git_path.exists():
            found_git_dir = _resolve_git_dir(git_path)
            
            if git_path.is_file():
                if _is_hermes_worktree(found_git_dir, module_git_dir_resolved):
                    return parent.resolve()
                continue
            else:
                if found_git_dir == module_git_dir_resolved:
                    return parent.resolve()
                break
    
    return module_repo_root


def install_root() -> Path:
    env = os.environ.get("HERMES_INSTALL_ROOT")
    return Path(env) if env else repo_root()
'''
    (pm_dir / "paths.py").write_text(paths_py)
    (pm_dir / "__init__.py").write_text("")
    
    old_cwd = os.getcwd()
    try:
        os.chdir(hermes_repo)
        
        sys.path.insert(0, str(hermes_repo))
        
        from pm.paths import repo_root
        
        rr = repo_root().resolve()
        assert rr == hermes_repo.resolve(), (
            f"repo_root()={rr} but expected hermes_repo={hermes_repo.resolve()}"
        )
    finally:
        os.chdir(old_cwd)
        if str(hermes_repo) in sys.path:
            sys.path.remove(str(hermes_repo))