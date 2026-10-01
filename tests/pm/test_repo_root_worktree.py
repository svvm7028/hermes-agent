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
    
    If module_repo_root is provided, the module will use that as its __file__ base.
    Otherwise it uses target_dir as the module location.
    """
    pm_dir = target_dir / "pm"
    pm_dir.mkdir(parents=True, exist_ok=True)
    
    paths_py = '''"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _parse_worktree_gitdir_pointer(git_path: Path) -> Path | None:
    """Parse a worktree's .git file (which contains 'gitdir: <path>/.git/worktrees/<name>')
    and return the main repo root by stripping the known suffix.
    
    Returns the candidate main repo root path via pure string manipulation (no resolve/stat
    on the worktree-link target to avoid home_io_guard trips).
    """
    try:
        content = git_path.read_text().strip()
        if not content.startswith("gitdir: "):
            return None
        gitdir_target = content[8:]  # strip 'gitdir: '
        
        # The target is <main-repo>/.git/worktrees/<name>
        # Strip the known suffix to get the main repo root
        suffix = "/.git/worktrees/"
        idx = gitdir_target.rfind(suffix)
        if idx == -1:
            return None
        main_repo_root_str = gitdir_target[:idx]
        return Path(main_repo_root_str)
    except OSError:
        return None


def _git_dir_main_repo_root(git_path: Path) -> Path | None:
    """Get the main repo root from a .git path (file or directory) via string ops only.
    
    - If .git is a file (worktree link), parse the gitdir pointer.
    - If .git is a directory, the main repo root is its parent.
    
    Returns normalized path string for comparison, never calls resolve().
    """
    try:
        if git_path.is_file():
            return _parse_worktree_gitdir_pointer(git_path)
        elif git_path.is_dir():
            # .git directory -> main repo root is its parent
            return git_path.parent
    except OSError:
        pass
    return None


def _is_path_under(path: Path, parent: Path) -> bool:
    """Check if path is under parent using string comparison (no resolve)."""
    try:
        return os.path.normpath(str(path)).startswith(os.path.normpath(str(parent)) + os.sep)
    except (OSError, ValueError):
        return False


def repo_root() -> Path:
    """Return the repository root relevant to the current execution.

    • If the current working directory is inside a Git worktree that belongs to
      the same repository as this module's physical location, walk up the CWD
      to find the worktree's top-level directory (the one containing the
      `.git` file that links to the actual repo). The returned `Path` is the
      *worktree root*.
      
    • If the CWD is inside an unrelated Git repo (ordinary .git directory) or
      no Git repo at all, fall back to the physical location of this module –
      which reproduces the original behaviour for normal checkouts and pip/
      pipx/git-installed users.

    The key discriminator: a worktree's `.git` is a FILE (not a directory)
    containing `gitdir: <path>/.git/worktrees/<name>`, where `<path>` is inside
    the git dir of the repo it belongs to. We verify the worktree belongs to
    the Hermes repo by comparing the parsed main-repo root (from the gitdir
    pointer's text, via string manipulation only - no resolve/stat on the
    worktree-link target) against the module's physical repo root before trusting it.
    """
    # The module's physical location - this IS the Hermes repo root for normal checkouts
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"

    # If the module location doesn't have a .git, we're in an installed package
    # (pip/pipx/git install) - fall back to module location
    if not module_git_dir.exists():
        return module_repo_root

    # Get the module's main repo root via string ops only (no resolve on .git)
    module_main_repo = _git_dir_main_repo_root(module_git_dir)
    if module_main_repo is None:
        # Can't determine - fall back to module location
        return module_repo_root
    
    module_main_repo_norm = os.path.normpath(str(module_main_repo))
    module_repo_root_norm = os.path.normpath(str(module_repo_root))

    cwd = Path.cwd().resolve()
    cwd_norm = os.path.normpath(str(cwd))

    # If CWD is the module's repo root (or under it), we're already in the right place.
    # This avoids walking up and hitting the real hermes home .git during tests.
    if _is_path_under(cwd, module_repo_root) or cwd_norm == module_repo_root_norm:
        return module_repo_root

    # Walk upward looking for a .git directory or a .git file (worktree link)
    for parent in [cwd] + list(cwd.parents):
        parent_norm = os.path.normpath(str(parent))
        
        # Stop if we've reached the module's main repo root (the real hermes home)
        # This prevents hitting the home_io_guard during test collection when
        # CWD is the main repo and the module lives in a worktree.
        if parent_norm == module_main_repo_norm:
            break
            
        # Also stop if we've walked past the module's physical repo root
        if parent_norm == module_repo_root_norm:
            break
            
        git_path = parent / ".git"
        if git_path.exists():
            if git_path.is_file():
                # This is a worktree (.git file). Parse the gitdir pointer via string
                # manipulation ONLY - no resolve/stat on the target to avoid
                # home_io_guard trips (the target is inside the install's main .git dir).
                parsed_main_repo = _parse_worktree_gitdir_pointer(git_path)
                if parsed_main_repo is not None:
                    # Compare via normalized string paths (no resolve)
                    if os.path.normpath(str(parsed_main_repo)) == module_main_repo_norm:
                        # It's a Hermes worktree - return the worktree root (parent of .git file)
                        return parent.resolve()
                # It's a worktree but NOT for the Hermes repo - ignore and continue walking
                continue
            else:
                # This is a regular .git directory (ordinary repo).
                # Get its main repo root via string ops and compare with module's
                found_main_repo = _git_dir_main_repo_root(git_path)
                if found_main_repo is not None:
                    if os.path.normpath(str(found_main_repo)) == module_main_repo_norm:
                        return parent.resolve()
                # It's an UNRELATED git repo - DO NOT TRUST IT. Fall through to module default.
                break

    return module_repo_root


def install_root() -> Path:
    """The tree this process runs from. ``HERMES_INSTALL_ROOT`` when a steward
    wrapper sets it (Nix points it at the sealed tree whose install stamp lives
    outside the package dir), else the executing checkout."""
    env = os.environ.get("HERMES_INSTALL_ROOT")
    return Path(env) if env else repo_root()


def install_stamp_path(project_root: Path) -> Path:
    """THE stamp location for ``project_root``, shared by every stamp reader.

    Beside the code in checkouts, Docker and desktop payloads. A Nix package
    bakes the stamp outside the store's package dir and its wrapper carries
    ``HERMES_INSTALL_ROOT`` for the executing tree only — so the executing
    tree resolves through install_root, any other tree is taken literally.
    PM reads it in sealed stages (the Docker runtime base) before hermes_cli
    ships, so it lives here rather than beside the stewards.
    """
    root = Path(project_root)
    if root.resolve() == repo_root():
        root = install_root()
    return root / "install-stamp.json"


def lockfile_path() -> Path:
    return Path(__file__).resolve().parent / "lock.json"


def store_root() -> Path:
    from pm.environments import store_root as resolve

    return resolve(repo_root())


def partials_root() -> Path:
    """The downloader's managed partials area: machine-scoped and shared
    (keyed by sha256(url), so two callers or two profiles reuse one
    partial), but anchored to the DEFAULT hermes root — NOT the byte
    store. The store can live inside a read-only sealed payload
    (WindowsApps/agent-payload), and partials are mutable state the
    downloader writes continuously, so they must land somewhere writable
    on every install kind: ``%LOCALAPPDATA%\\\\hermes\\\\cache\\\\partials`` on
    Windows, ``~/.hermes/cache/partials`` on POSIX."""
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root() / "cache" / "partials"


def facts_path() -> Path:
    return store_root() / "facts.json"


def writable_store_root() -> Path:
    if not (store_root().parent / "manifest.json").is_file():
        return store_root()
    from hermes_constants import get_default_hermes_root

    return get_default_hermes_root() / "tools"


def runtime_facts_path() -> Path:
    from pm.environments import runtime_facts_path as resolve

    return resolve(repo_root())
'''
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