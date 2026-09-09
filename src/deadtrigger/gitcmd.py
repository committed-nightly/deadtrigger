"""Thin wrappers over the ``git`` binary.

Everything this tool compares workflows against -- the file tree, the branch
names, the tag names, the default branch -- comes from git rather than from
the filesystem or the API. No network, no token, no rate limit.

The one thing to be careful about is that a checkout can be a partial view of
the repository. ``actions/checkout`` at its default ``fetch-depth: 1`` leaves
a shallow clone holding exactly one branch, so ``for-each-ref`` there reports
one branch for a repository that has forty. ``is_shallow`` exists so the
branch checks can decline to run rather than accuse every branch filter in
the repository of naming something that does not exist.
"""

from __future__ import annotations

import os
import subprocess


class GitError(RuntimeError):
    """git was missing, unhappy, or pointed at something that isn't a repo."""


def run_git(args: list[str], cwd: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - depends on the box
        raise GitError("git is not on PATH") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(stderr or f"git {' '.join(args)} failed")
    return proc.stdout.decode("utf-8", "replace")


def repo_root(cwd: str) -> str:
    return run_git(["rev-parse", "--show-toplevel"], cwd).strip()


def resolve(rev: str, cwd: str) -> str:
    """Resolve a revision to a commit sha, or raise with the name the user typed."""
    try:
        out = run_git(["rev-parse", "--verify", "--quiet", rev + "^{commit}"], cwd).strip()
    except GitError as exc:
        if str(exc).startswith("git "):
            raise GitError(f"no such revision: {rev}") from exc
        raise
    if not out:
        raise GitError(f"no such revision: {rev}")
    return out


def is_shallow(cwd: str) -> bool:
    return run_git(["rev-parse", "--is-shallow-repository"], cwd).strip() == "true"


def tracked_files(cwd: str, ref: str | None = None) -> list[str]:
    """Every file at ``ref``, as repo-root-relative paths.

    With no ref this is the index rather than a commit, so that editing a
    workflow and running the tool tells you something before you commit it.

    ``-z`` because a newline in a path is legal in git, rare, and exactly the
    sort of thing that would make a checker quietly miscount.
    """
    if ref is None:
        out = run_git(["ls-files", "-z", "--cached"], cwd)
    else:
        out = run_git(["ls-tree", "-r", "-z", "--name-only", ref], cwd)
    return [path for path in out.split("\0") if path]


def show(ref: str, relpath: str, cwd: str) -> str | None:
    """Contents of a file at a revision, or None if it isn't there."""
    try:
        return run_git(["show", f"{ref}:{relpath}"], cwd)
    except GitError:
        return None


def read_worktree(root: str, relpath: str) -> str | None:
    """Contents of a tracked file as it is right now.

    Falls back to the index for a file that is tracked but not on disk --
    staged deletions and sparse checkouts both produce that, and neither is
    a reason to stop checking.
    """
    full = os.path.join(root, relpath)
    try:
        with open(full, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return show("", relpath, root)


def _strip_remote(refname: str) -> str | None:
    """``refs/remotes/origin/feature/x`` -> ``feature/x``.

    ``origin/HEAD`` is a symbolic pointer at the default branch, not a branch
    in its own right, and counting it would invent a branch called HEAD.
    """
    rest = refname[len("refs/remotes/") :]
    _, _, name = rest.partition("/")
    if not name or name == "HEAD":
        return None
    return name


def branches(cwd: str) -> set[str]:
    """Branch names, local and remote-tracking, with the remote prefix removed."""
    out = run_git(
        ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"], cwd
    )
    names: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("refs/heads/"):
            names.add(line[len("refs/heads/") :])
        elif line.startswith("refs/remotes/"):
            name = _strip_remote(line)
            if name:
                names.add(name)
    return names


def tags(cwd: str) -> set[str]:
    out = run_git(["for-each-ref", "--format=%(refname)", "refs/tags"], cwd)
    return {line[len("refs/tags/") :] for line in out.splitlines() if line.strip()}


def default_branch(cwd: str) -> str | None:
    """The default branch, if this clone records one.

    ``refs/remotes/origin/HEAD`` is set by a normal ``git clone`` and is not
    set by ``actions/checkout``, so this returns None often enough that the
    caller has to handle it rather than assume "main".
    """
    try:
        out = run_git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd).strip()
    except GitError:
        return None
    return _strip_remote(out) if out.startswith("refs/remotes/") else None


def resolve_branch(name: str, cwd: str) -> str | None:
    """A branch name as something git can actually resolve, or None.

    Worth the trouble because of the case this tool most needs to get right:
    on a pull request, ``actions/checkout`` leaves ``main`` existing only as
    ``refs/remotes/origin/main``, and plain ``git rev-parse main`` fails
    there. Taking that failure at face value would make --default-branch
    silently do nothing on every CI run -- the one place it is needed.
    """
    for candidate in (f"refs/heads/{name}", f"refs/remotes/origin/{name}", name):
        try:
            resolve(candidate, cwd)
            return candidate
        except GitError:
            continue

    out = run_git(["for-each-ref", "--format=%(refname)", "refs/remotes"], cwd)
    for line in out.splitlines():
        line = line.strip()
        if _strip_remote(line) == name:
            return line
    return None
