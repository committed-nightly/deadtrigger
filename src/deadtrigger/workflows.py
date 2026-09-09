"""Finding and reading the workflow files in a repository.

The one thing worth knowing here: in YAML 1.1, which is what PyYAML
implements, the bare word ``on`` is a **boolean**. So the key of the block
every workflow file in the world starts with does not come back as the
string ``"on"``; it comes back as ``True``. Look it up by name and every
workflow in the repository appears to have no triggers at all, which for a
tool about triggers is a quiet and total failure. ``trigger_block`` below is
the whole fix and it is three lines long.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any

import yaml

WORKFLOW_DIR = ".github/workflows"
WORKFLOW_SUFFIXES = (".yml", ".yaml")

# Triggers GitHub only ever delivers to the copy of a workflow that is on the
# repository's default branch, however many other branches it appears on.
DEFAULT_BRANCH_ONLY = ("schedule", "workflow_dispatch", "workflow_run")

# Events that carry a pull request. Used to decide whether a job condition
# that reads github.event.pull_request can ever see one.
PULL_REQUEST_EVENTS = ("pull_request", "pull_request_target", "pull_request_review",
                       "pull_request_review_comment", "merge_group")


class WorkflowError(ValueError):
    """A workflow file that could not be read as a workflow."""


@dataclass
class Workflow:
    path: str
    declared_name: str | None
    triggers: dict[str, Any]
    jobs: dict[str, Any]
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def display_name(self) -> str:
        """What GitHub calls this workflow in the UI and in the API.

        With no `name:`, GitHub falls back to the workflow's path. That
        fallback is also what a `workflow_run` reference has to be matched
        against, since there is no name for it to use.
        """
        return self.declared_name if self.declared_name else self.path

    @property
    def events(self) -> list[str]:
        return list(self.triggers)


def is_workflow_path(path: str) -> bool:
    """Whether a repo-relative path is a file GitHub will read as a workflow.

    GitHub only looks in .github/workflows itself, not in subdirectories of
    it -- a file one level down is silently not a workflow at all.
    """
    if not path.endswith(WORKFLOW_SUFFIXES):
        return False
    return posixpath.dirname(path) == WORKFLOW_DIR


def trigger_block(document: dict[str, Any]) -> Any:
    """The value of the `on:` key, whatever YAML decided that key was."""
    if "on" in document:
        return document["on"]
    return document.get(True)


def normalise_triggers(raw: Any) -> dict[str, Any]:
    """The `on:` value as {event: config}, for all three spellings.

    ``on: push``, ``on: [push, pull_request]`` and the mapping form all mean
    the same thing to GitHub, and only the mapping form carries filters. An
    event with no configuration gets an empty dict rather than None so that
    callers can index it without checking. Anything else is passed through
    untouched -- ``schedule:`` is a *list* of cron entries, and coercing
    every non-mapping config to {} would throw every schedule in the
    repository away without saying so.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {raw: {}}
    if isinstance(raw, list):
        return {event: {} for event in raw if isinstance(event, str)}
    if isinstance(raw, dict):
        return {
            event: ({} if config is None else config)
            for event, config in raw.items()
            if isinstance(event, str)
        }
    return {}


def parse(text: str, path: str) -> Workflow:
    """Parse one workflow file. Raises WorkflowError if it is not one."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowError(f"{path}: not valid YAML: {exc}") from exc

    if document is None:
        raise WorkflowError(f"{path}: file is empty")
    if not isinstance(document, dict):
        raise WorkflowError(f"{path}: top level is not a mapping")

    declared = document.get("name")
    jobs = document.get("jobs")

    return Workflow(
        path=path,
        declared_name=declared if isinstance(declared, str) and declared else None,
        triggers=normalise_triggers(trigger_block(document)),
        jobs=jobs if isinstance(jobs, dict) else {},
        raw=document,
    )


def as_list(value: Any) -> list[Any]:
    """A YAML field that may be written as a scalar or a sequence.

    `branches: main` and `branches: [main]` are both legal.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
