"""Command line entry point.

Exit codes, because the main use for this is a CI gate:

    0  every trigger, filter and condition can fire
    1  at least one of them cannot
    2  the check could not run at all

2 is deliberately not 1 and very deliberately not 0. No repository, a --ref
that doesn't resolve, a filter pattern that cannot be parsed: those all mean
nobody looked, and "nobody looked" reported as a green tick is worse than no
check at all.

A repository with no workflow files is a 2 for the same reason. You have
pointed a workflow checker at a repository that has no workflows, and the
likeliest explanation is that you are running it somewhere unexpected.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap

from . import gitcmd, workflows
from .core import (
    DEAD_CRON,
    DEAD_PATH_FILTER,
    IMPOSSIBLE_IF,
    NO_SUCH_WORKFLOW,
    OFF_DEFAULT_BRANCH,
    UNMATCHED_PATH,
    UNMATCHED_REF,
    Repo,
    check,
)
from .gitcmd import GitError
from .pattern import PatternError
from .workflows import WORKFLOW_DIR, WorkflowError

EXIT_OK = 0
EXIT_DEAD = 1
EXIT_ERROR = 2

ORDER = [
    OFF_DEFAULT_BRANCH,
    NO_SUCH_WORKFLOW,
    DEAD_PATH_FILTER,
    DEAD_CRON,
    IMPOSSIBLE_IF,
    UNMATCHED_REF,
    UNMATCHED_PATH,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deadtrigger",
        description=(
            "Find the GitHub Actions workflows that can never run, by checking "
            "their triggers against the repository they are actually in."
        ),
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="a path inside the repository to check (default: the current directory)",
    )
    parser.add_argument(
        "--ref", default=None,
        help=(
            "check the workflows as they were at this revision, instead of as "
            "they are on disk now"
        ),
    )
    parser.add_argument(
        "--default-branch", default=None, metavar="NAME",
        help=(
            "the repository's default branch. Only needed when the clone does "
            "not record one, which is the usual case under actions/checkout."
        ),
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    return parser


def _load(repo_root: str, ref: str | None) -> list:
    """Every workflow file, parsed.

    With no --ref this reads the files as they are on disk, so that you can
    edit a filter and find out whether it is dead before committing it --
    which is the moment the answer is worth anything.
    """
    paths = sorted(
        path
        for path in gitcmd.tracked_files(repo_root, ref)
        if workflows.is_workflow_path(path)
    )
    sheet = []
    for path in paths:
        text = (
            gitcmd.read_worktree(repo_root, path)
            if ref is None
            else gitcmd.show(ref, path, repo_root)
        )
        if text is None:  # pragma: no cover - git just said it is there
            continue
        sheet.append(workflows.parse(text, path))
    return sheet


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _print_human(report, out) -> None:
    by_kind: dict[str, list] = {}
    for finding in report.findings:
        by_kind.setdefault(finding.kind, []).append(finding)

    for kind in ORDER:
        for finding in by_kind.get(kind, []):
            print(f"  {finding.workflow}", file=out)
            print(f"      {finding.where}: {finding.subject}", file=out)
            # The kind is printed rather than a prose headline so that what
            # you grep for in the terminal is the same string --json gives.
            print(
                textwrap.fill(
                    f"{kind}: {finding.detail}",
                    width=80,
                    initial_indent=" " * 6,
                    subsequent_indent=" " * 6,
                ),
                file=out,
            )
            print(file=out)

    checked = _plural(report.workflows_checked, "workflow")
    if report.findings:
        print(f"{checked} checked, {_plural(len(report.findings), 'finding')}", file=out)
    else:
        print(f"{checked} checked, clean", file=out)

    # Printed last and printed always. A skipped check is the one thing a
    # green run must not hide.
    for name, reason in report.skipped:
        print(f"not checked: {name} -- {reason}", file=out)


def main(argv: list[str] | None = None, out=None, err=None) -> int:
    args = build_parser().parse_args(argv)
    out = out or sys.stdout
    err = err or sys.stderr

    if not os.path.isdir(args.path):
        print(f"deadtrigger: not a directory: {args.path}", file=err)
        return EXIT_ERROR

    try:
        root = gitcmd.repo_root(args.path)
        if args.ref is not None:
            gitcmd.resolve(args.ref, root)
        sheet = _load(root, args.ref)
    except (GitError, WorkflowError) as exc:
        print(f"deadtrigger: {exc}", file=err)
        return EXIT_ERROR

    if not sheet:
        where = f" at {args.ref}" if args.ref else ""
        print(
            f"deadtrigger: no workflow files in {WORKFLOW_DIR}/{where}",
            file=err,
        )
        return EXIT_ERROR

    try:
        repo = _describe(root, args)
        report = check(sheet, repo)
    except (GitError, PatternError) as exc:
        print(f"deadtrigger: {exc}", file=err)
        return EXIT_ERROR

    if args.json:
        print(
            json.dumps(
                {
                    "workflows_checked": report.workflows_checked,
                    "findings": [f.as_dict() for f in report.findings],
                    "skipped": [
                        {"check": name, "reason": reason}
                        for name, reason in report.skipped
                    ],
                },
                indent=2,
            ),
            file=out,
        )
    else:
        _print_human(report, out)

    return EXIT_OK if report.ok else EXIT_DEAD


def _describe(root: str, args) -> Repo:
    default_branch = args.default_branch or gitcmd.default_branch(root)

    default_workflows = None
    if default_branch is not None:
        rev = gitcmd.resolve_branch(default_branch, root)
        if rev is None:
            # Named a default branch this clone does not have. Better to
            # check nothing than to report every workflow as stranded off it.
            default_branch = None
        else:
            default_workflows = {
                path
                for path in gitcmd.tracked_files(root, rev)
                if workflows.is_workflow_path(path)
            }

    return Repo(
        files=gitcmd.tracked_files(root, args.ref),
        branches=gitcmd.branches(root),
        tags=gitcmd.tags(root),
        default_branch=default_branch,
        refs_are_complete=not gitcmd.is_shallow(root),
        default_branch_workflows=default_workflows,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
