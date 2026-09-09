"""Job conditions: decided when they can be, refused when they cannot."""

from __future__ import annotations

import pytest

from deadtrigger.ifexpr import Unsupported, satisfiable_events, strip_expression

PUSH_ONLY = ["push"]
PUSH_AND_PR = ["push", "pull_request"]


class TestDecided:
    def test_matching_event(self):
        assert satisfiable_events("github.event_name == 'push'", PUSH_AND_PR) == ["push"]

    def test_event_the_workflow_never_receives(self):
        assert satisfiable_events("github.event_name == 'schedule'", PUSH_AND_PR) == []

    def test_braces_are_optional(self):
        condition = "${{ github.event_name == 'schedule' }}"
        assert satisfiable_events(condition, PUSH_AND_PR) == []

    def test_or_chain(self):
        condition = "github.event_name == 'push' || github.event_name == 'schedule'"
        assert satisfiable_events(condition, PUSH_AND_PR) == ["push"]

    def test_or_chain_with_no_live_branch(self):
        condition = "github.event_name == 'schedule' || github.event_name == 'release'"
        assert satisfiable_events(condition, PUSH_AND_PR) == []

    def test_and_of_two_events_is_never_true(self):
        """A single run has one event name, so this is always false --
        a real mistake, usually meant as ||."""
        condition = "github.event_name == 'push' && github.event_name == 'pull_request'"
        assert satisfiable_events(condition, PUSH_AND_PR) == []

    def test_negation(self):
        condition = "github.event_name != 'push'"
        assert satisfiable_events(condition, PUSH_ONLY) == []
        assert satisfiable_events(condition, PUSH_AND_PR) == ["pull_request"]

    def test_bang_operator(self):
        condition = "!(github.event_name == 'push')"
        assert satisfiable_events(condition, PUSH_ONLY) == []

    def test_parentheses(self):
        condition = (
            "(github.event_name == 'push' || github.event_name == 'schedule') "
            "&& github.event_name != 'schedule'"
        )
        assert satisfiable_events(condition, PUSH_AND_PR) == ["push"]

    def test_literal_false(self):
        assert satisfiable_events("false", PUSH_AND_PR) == []

    def test_yaml_boolean_false(self):
        """`if: false` with no quotes arrives from YAML as a bool."""
        assert satisfiable_events(False, PUSH_AND_PR) == []

    def test_literal_true(self):
        assert satisfiable_events("true", PUSH_AND_PR) == PUSH_AND_PR

    def test_bare_event_name_is_truthy(self):
        """A non-empty string is true in GitHub's expression language."""
        assert satisfiable_events("github.event_name", PUSH_AND_PR) == PUSH_AND_PR

    def test_quoted_string_with_escaped_quote(self):
        condition = "github.event_name == 'it''s'"
        assert satisfiable_events(condition, PUSH_AND_PR) == []


class TestRefused:
    """Everything here must raise rather than guess. A wrong accusation is
    worse than a missed finding."""

    @pytest.mark.parametrize(
        "condition",
        [
            "github.ref == 'refs/heads/main'",
            "needs.build.result == 'success'",
            "success()",
            "github.event.pull_request.draft == false",
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
            "contains(github.event_name, 'push')",
            "matrix.os == 'ubuntu-latest'",
            "env.DEPLOY == 'yes'",
        ],
    )
    def test_unsupported_conditions(self, condition):
        with pytest.raises(Unsupported):
            satisfiable_events(condition, PUSH_AND_PR)

    def test_boolean_against_string_is_refused(self):
        """GitHub coerces mixed types to numbers. Not guessing at that."""
        with pytest.raises(Unsupported):
            satisfiable_events("github.event_name == true", PUSH_AND_PR)

    def test_template_with_literal_text_is_refused(self):
        with pytest.raises(Unsupported):
            satisfiable_events("run-${{ github.event_name }}", PUSH_AND_PR)

    def test_unbalanced_parentheses(self):
        with pytest.raises(Unsupported):
            satisfiable_events("(github.event_name == 'push'", PUSH_AND_PR)

    def test_trailing_junk(self):
        with pytest.raises(Unsupported):
            satisfiable_events("github.event_name == 'push' 'x'", PUSH_AND_PR)

    def test_empty(self):
        with pytest.raises(Unsupported):
            satisfiable_events("", PUSH_AND_PR)

    def test_none(self):
        with pytest.raises(Unsupported):
            satisfiable_events(None, PUSH_AND_PR)


class TestStripExpression:
    def test_plain(self):
        assert strip_expression(" a == 'b' ") == "a == 'b'"

    def test_braces(self):
        assert strip_expression("${{ a == 'b' }}") == "a == 'b'"

    def test_yaml_true(self):
        assert strip_expression(True) == "true"
