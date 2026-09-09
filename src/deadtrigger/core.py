"""The checks themselves.

Each one answers the same question in a different place: *given this
repository as it actually is, can this thing ever fire?* That is the line
between this tool and a workflow linter. A linter reads the file; these
checks read the file against the branch list, the tag list, the file tree
and the other workflows sitting next to it.

Where a check cannot be answered -- a shallow clone with one branch in it,
a job condition built out of things only the run knows -- it is recorded as
skipped and named in the output. A check that did not run must never be
indistinguishable from a check that passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import cron as croncheck
from . import ifexpr, workflows
from .pattern import PatternError, Pattern, compile_all, included
from .workflows import DEFAULT_BRANCH_ONLY, Workflow, as_list

NO_SUCH_WORKFLOW = "no-such-workflow"
OFF_DEFAULT_BRANCH = "off-default-branch"
DEAD_CRON = "dead-cron"
UNMATCHED_REF = "unmatched-ref"
DEAD_PATH_FILTER = "dead-path-filter"
UNMATCHED_PATH = "unmatched-path"
IMPOSSIBLE_IF = "impossible-if"

REF_KEYS = {"branches": "branch", "branches-ignore": "branch",
            "tags": "tag", "tags-ignore": "tag"}
PATH_KEYS = ("paths", "paths-ignore")


@dataclass
class Finding:
    kind: str
    workflow: str
    where: str
    subject: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "workflow": self.workflow,
            "where": self.where,
            "subject": self.subject,
            "detail": self.detail,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    workflows_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, *args) -> None:
        self.findings.append(Finding(*args))

    def skip(self, check: str, reason: str) -> None:
        self.skipped.append((check, reason))


@dataclass
class Repo:
    """Everything the checks are allowed to know about the repository."""

    files: list[str]
    branches: set[str]
    tags: set[str]
    default_branch: str | None
    refs_are_complete: bool
    # Workflow paths present on the default branch, or None if we cannot see it.
    default_branch_workflows: set[str] | None = None


def check(sheet: list[Workflow], repo: Repo) -> Report:
    report = Report(workflows_checked=len(sheet))

    if not repo.refs_are_complete:
        report.skip(
            "branch and tag filters",
            "this clone is shallow, so its branch and tag lists are the ones "
            "fetched rather than the ones that exist "
            "(actions/checkout needs fetch-depth: 0)",
        )
    if repo.default_branch is None:
        report.skip(
            "default-branch-only triggers",
            "cannot tell which branch is the default one "
            "(no refs/remotes/origin/HEAD; pass --default-branch)",
        )

    known_names = {flow.display_name for flow in sheet}
    known_names.update(flow.path for flow in sheet)

    for flow in sheet:
        _check_workflow_run(flow, known_names, report)
        _check_default_branch(flow, repo, report)
        _check_schedule(flow, report)
        _check_filters(flow, repo, report)
        _check_jobs(flow, report)

    return report


def _check_workflow_run(flow: Workflow, known_names: set[str], report: Report) -> None:
    """`workflow_run` matches the *name* of another workflow, and says nothing
    when that name matches nothing."""
    config = flow.triggers.get("workflow_run")
    if not isinstance(config, dict) or not config:
        return

    named = [name for name in as_list(config.get("workflows")) if isinstance(name, str)]
    if not named:
        return

    missing = [name for name in named if name not in known_names]
    if len(missing) == len(named):
        report.add(
            NO_SUCH_WORKFLOW,
            flow.path,
            "on.workflow_run.workflows",
            ", ".join(repr(name) for name in missing),
            "no workflow in .github/workflows has this name, so nothing can "
            "ever trigger this one",
        )
    elif missing:
        report.add(
            NO_SUCH_WORKFLOW,
            flow.path,
            "on.workflow_run.workflows",
            ", ".join(repr(name) for name in missing),
            "no workflow has this name; the other entries in the list still "
            "trigger this one, so it fires less often than it reads",
        )


def _check_default_branch(flow: Workflow, repo: Repo, report: Report) -> None:
    if repo.default_branch is None or repo.default_branch_workflows is None:
        return
    if flow.path in repo.default_branch_workflows:
        return

    stranded = [event for event in DEFAULT_BRANCH_ONLY if event in flow.triggers]
    if stranded:
        report.add(
            OFF_DEFAULT_BRANCH,
            flow.path,
            "on",
            ", ".join(stranded),
            f"GitHub only delivers {'this event' if len(stranded) == 1 else 'these events'} "
            f"to the copy of a workflow on the default branch ({repo.default_branch}), "
            "and this file is not there",
        )


def _check_schedule(flow: Workflow, report: Report) -> None:
    raw = flow.triggers.get("schedule")
    if raw is None:
        return

    entries = raw if isinstance(raw, list) else as_list(raw)
    crons = [
        entry.get("cron") if isinstance(entry, dict) else entry
        for entry in entries
        if not (isinstance(entry, dict) and "cron" not in entry)
    ]
    if not crons:
        report.add(
            DEAD_CRON, flow.path, "on.schedule", "(no cron)",
            "the schedule trigger is declared with no cron entry, so nothing "
            "is ever scheduled",
        )
        return

    for expression in crons:
        try:
            parsed = croncheck.parse(expression)
        except croncheck.CronError as exc:
            report.add(
                DEAD_CRON, flow.path, "on.schedule", repr(expression),
                f"GitHub will not schedule this: {exc}",
            )
            continue

        reason = croncheck.impossible_reason(parsed)
        if reason:
            report.add(
                DEAD_CRON, flow.path, "on.schedule", repr(expression),
                f"valid cron for a date that never happens: {reason}",
            )


def _unmatched(patterns: list[Pattern], candidates: set[str] | list[str]) -> list[Pattern]:
    return [p for p in patterns if not any(p.matches(c) for c in candidates)]


def _check_filters(flow: Workflow, repo: Repo, report: Report) -> None:
    for event, config in flow.triggers.items():
        if not isinstance(config, dict):
            continue

        if repo.refs_are_complete:
            for key, noun in REF_KEYS.items():
                if key in config:
                    _check_ref_filter(flow, event, key, noun, config[key], repo, report)

        for key in PATH_KEYS:
            if key in config:
                _check_path_filter(flow, event, key, config[key], repo, report)


def _compile(texts: list, flow: Workflow, where: str) -> list[Pattern]:
    try:
        return compile_all([t for t in texts])
    except PatternError as exc:
        raise PatternError(f"{flow.path}: {where}: {exc}") from exc


def _check_ref_filter(flow, event, key, noun, raw, repo: Repo, report: Report) -> None:
    """Branch and tag filters.

    Only patterns with no wildcard in them are reported. `release/**` in a
    repository with no release branches is a filter waiting for a branch,
    which is a normal thing to write and none of this tool's business.
    `master` in a repository whose branch is called `main` is a typo, and
    the workflow has never run once.
    """
    where = f"on.{event}.{key}"
    patterns = _compile(as_list(raw), flow, where)
    candidates = repo.branches if noun == "branch" else repo.tags

    for pattern in _unmatched(patterns, candidates):
        if pattern.has_wildcard:
            continue
        report.add(
            UNMATCHED_REF, flow.path, where, pattern.text,
            f"no {noun} in this repository is called that",
        )


def _check_path_filter(flow, event, key, raw, repo: Repo, report: Report) -> None:
    where = f"on.{event}.{key}"
    patterns = _compile(as_list(raw), flow, where)

    # A `paths:` list that matches nothing is reported once, for the whole
    # filter, rather than once per pattern: the interesting fact is that the
    # event cannot fire, not that each line individually missed.
    if key == "paths" and patterns:
        if not any(included(patterns, path) for path in repo.files):
            report.add(
                DEAD_PATH_FILTER, flow.path, where,
                ", ".join(p.text for p in patterns),
                f"no file in the repository is matched by this filter, so "
                f"{event} can never start this workflow",
            )
            return

    for pattern in _unmatched(patterns, repo.files):
        if pattern.negated:
            # An exclusion that currently excludes nothing is a guard against
            # files that do not exist yet. That is what a guard is for.
            continue
        report.add(
            UNMATCHED_PATH, flow.path, where, pattern.text,
            "matches no file in the repository",
        )


def _check_jobs(flow: Workflow, report: Report) -> None:
    """Job conditions that no event this workflow receives can satisfy.

    Skipped entirely for reusable workflows: under `workflow_call`,
    `github.event_name` is the *caller's* event, which is not in this file
    and not knowable from it.
    """
    events = flow.events
    if not events or "workflow_call" in flow.triggers:
        return

    for job_id, job in flow.jobs.items():
        if not isinstance(job, dict) or "if" not in job:
            continue
        try:
            live = ifexpr.satisfiable_events(job["if"], events)
        except ifexpr.Unsupported:
            continue
        if not live:
            report.add(
                IMPOSSIBLE_IF, flow.path, f"jobs.{job_id}.if",
                ifexpr.strip_expression(job["if"]),
                "no event this workflow is triggered by can make this true, "
                f"so the job never runs (triggers: {', '.join(events)})",
            )
