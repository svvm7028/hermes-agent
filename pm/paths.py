"""Where the store, lockfile, and installed-state file live."""

from __future__ import annotations

import os
from pathlib import Path


def _resolve_git_dir(git_path: Path) -> Path:
    """Resolve a .git path to the actual git directory.
    
    - If .git is a directory, return it.
    - If .git is a file (worktree), read the 'gitdir:' pointer and return the resolved path.
    """
    if git_path.is_dir():
        return git_path.resolve()
    # .git is a file (worktree link)
    try:
        content = git_path.read_text().strip()
        if content.startswith("gitdir: "):
            gitdir_path = Path(content[8:]).resolve()
            return gitdir_path
    except OSError:
        pass
    return git_path.resolve()


def _is_hermes_worktree(git_dir: Path, hermes_repo_git_dir: Path) -> bool:
    """Check if the given git directory is a worktree belonging to the Hermes repo.
    
    A worktree's git directory lives at .git/worktrees/<name> inside the main repo's .git.
    """
    try:
        # Normalize both paths
        git_dir = git_dir.resolve()
        hermes_repo_git_dir = hermes_repo_git_dir.resolve()
        
        # Check if git_dir is inside hermes_repo_git_dir/worktrees/
        worktrees_dir = hermes_repo_git_dir / "worktrees"
        return worktrees_dir in git_dir.parents or git_dir == worktrees_dir
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
    the Hermes repo before trusting it.
    """
    # The module's physical location - this IS the Hermes repo root for normal checkouts
    module_repo_root = Path(__file__).resolve().parent.parent
    module_git_dir = module_repo_root / ".git"
    
    # If the module location doesn't have a .git, we're in an installed package
    # (pip/pipx/git install) - fall back to module location
    if not module_git_dir.exists():
        return module_repo_root
    
    module_git_dir_resolved = _resolve_git_dir(module_git_dir)
    
    cwd = Path.cwd().resolve()
    
    # Walk upward looking for a .git directory or a .git file (worktree link)
    for parent in [cwd] + list(cwd.parents):
        git_path = parent / ".git"
        if git_path.exists():
            found_git_dir = _resolve_git_dir(git_path)
            
            if git_path.is_file():
                # This is a worktree (.git file). Check if it belongs to the Hermes repo.
                if _is_hermes_worktree(found_git_dir, module_git_dir_resolved):
                    # It's a Hermes worktree - return the worktree root (parent of .git file)
                    return parent.resolve()
                # It's a worktree but NOT for the Hermes repo - ignore and continue walking
                continue
            else:
                # This is a regular .git directory (ordinary repo).
                # Check if it's the SAME repo as the module location (e.g., running from main repo)
                if found_git_dir == module_git_dir_resolved:
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
    on every install kind: ``%LOCALAPPDATA%\\hermes\\cache\\partials`` on
    Windows, ``~/.hermes/cache/partials`` on POSIX.
    """
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