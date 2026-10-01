"""Where the store, lockfile, and installed-state file live."""

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
    
    # Use realpath for comparison to handle symlinks (e.g. /var -> /private/var on macOS)
    # This does NOT resolve the worktree-link target - just normalizes symlinks in
    # the already-resolved module path and the parsed string from gitdir pointer.
    module_main_repo_norm = os.path.normpath(os.path.realpath(str(module_main_repo)))
    module_repo_root_norm = os.path.normpath(os.path.realpath(str(module_repo_root)))

    cwd = Path.cwd().resolve()
    cwd_norm = os.path.normpath(os.path.realpath(str(cwd)))

    # Walk upward looking for a .git directory or a .git file (worktree link)
    # Check each parent's .git FIRST before applying boundary checks, so we can
    # discover a closer/nested worktree .git file before falling back to the
    # module location. This fixes the case where module is in main repo and
    # cwd is in a nested worktree under it.
    for parent in [cwd] + list(cwd.parents):
        parent_norm = os.path.normpath(str(parent))
        
        git_path = parent / ".git"
        if git_path.exists():
            if git_path.is_file():
                # This is a worktree (.git file). Parse the gitdir pointer via string
                # manipulation ONLY - no resolve/stat on the target to avoid
                # home_io_guard trips (the target is inside the install's main .git dir).
                parsed_main_repo = _parse_worktree_gitdir_pointer(git_path)
                if parsed_main_repo is not None:
                    # Compare via normalized realpath paths (handles symlinks like /var -> /private/var)
                    # This does NOT call resolve() on the worktree-link target - just realpath
                    # on the parsed string from the gitdir pointer text.
                    if os.path.normpath(os.path.realpath(str(parsed_main_repo))) == module_main_repo_norm:
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

        # After checking this parent's .git, stop if we've reached the module's main repo root
        # (the real hermes home). This prevents hitting the home_io_guard during test collection
        # when CWD is the main repo and the module lives in a worktree.
        if parent_norm == module_main_repo_norm:
            break
           
        # Also stop if we've walked past the module's physical repo root
        if parent_norm == module_repo_root_norm:
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
    on every install kind: ``%LOCALAPPDATA%\\hermes\\cache\\partials`` on
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