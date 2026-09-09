"""Reading workflow files, including the `on:` key that isn't called "on"."""

from __future__ import annotations

import pytest
import yaml

from deadtrigger.workflows import (
    WorkflowError,
    as_list,
    is_workflow_path,
    normalise_triggers,
    parse,
    trigger_block,
)


def test_yaml_really_does_parse_on_as_a_boolean():
    """The premise of trigger_block, asserted against PyYAML rather than
    against our own code. If a future YAML 1.2 loader changes this, the
    special case becomes dead weight and this test says so."""
    document = yaml.safe_load("on:\n  push:\n")
    assert "on" not in document
    assert True in document


def test_trigger_block_finds_the_boolean_key():
    assert trigger_block(yaml.safe_load("on:\n  push:\n")) == {"push": None}


def test_trigger_block_finds_a_quoted_on():
    assert trigger_block(yaml.safe_load('"on":\n  push:\n')) == {"push": None}


def test_trigger_block_absent():
    assert trigger_block({"name": "x"}) is None


class TestNormaliseTriggers:
    def test_scalar(self):
        assert normalise_triggers("push") == {"push": {}}

    def test_sequence(self):
        assert normalise_triggers(["push", "pull_request"]) == {
            "push": {},
            "pull_request": {},
        }

    def test_mapping_with_empty_event(self):
        assert normalise_triggers({"push": None}) == {"push": {}}

    def test_mapping_with_filters(self):
        raw = {"push": {"branches": ["main"]}}
        assert normalise_triggers(raw)["push"]["branches"] == ["main"]

    def test_schedule_stays_a_list(self):
        """schedule is a sequence, not a mapping. Flattening it to {} would
        throw away every cron in the repository."""
        raw = {"schedule": [{"cron": "0 0 * * *"}]}
        assert normalise_triggers(raw)["schedule"] == [{"cron": "0 0 * * *"}]

    def test_none(self):
        assert normalise_triggers(None) == {}


class TestIsWorkflowPath:
    @pytest.mark.parametrize(
        "path", [".github/workflows/ci.yml", ".github/workflows/release.yaml"]
    )
    def test_yes(self, path):
        assert is_workflow_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/shared/ci.yml",  # GitHub does not recurse
            ".github/workflow/ci.yml",
            "workflows/ci.yml",
            ".github/workflows/README.md",
            ".github/workflows/ci.yml.bak",
        ],
    )
    def test_no(self, path):
        assert not is_workflow_path(path)


class TestParse:
    def test_name_and_triggers(self):
        flow = parse("name: CI\non:\n  push:\njobs:\n  a:\n    steps: []\n", "p.yml")
        assert flow.declared_name == "CI"
        assert flow.display_name == "CI"
        assert flow.events == ["push"]
        assert list(flow.jobs) == ["a"]

    def test_unnamed_workflow_displays_as_its_path(self):
        """GitHub falls back to the file path when there is no name:, and
        that fallback is what workflow_run has to match against."""
        flow = parse("on:\n  push:\n", ".github/workflows/ci.yml")
        assert flow.declared_name is None
        assert flow.display_name == ".github/workflows/ci.yml"

    def test_empty_name_is_no_name(self):
        assert parse("name: ''\non: push\n", "p.yml").declared_name is None

    def test_no_jobs(self):
        assert parse("on: push\n", "p.yml").jobs == {}

    @pytest.mark.parametrize(
        "text,fragment",
        [
            ("", "empty"),
            ("- a\n- b\n", "not a mapping"),
            ("name: [\n", "not valid YAML"),
        ],
    )
    def test_rejected(self, text, fragment):
        with pytest.raises(WorkflowError) as caught:
            parse(text, "p.yml")
        assert fragment in str(caught.value)


class TestAsList:
    def test_scalar(self):
        assert as_list("main") == ["main"]

    def test_list(self):
        assert as_list(["a", "b"]) == ["a", "b"]

    def test_none(self):
        assert as_list(None) == []
