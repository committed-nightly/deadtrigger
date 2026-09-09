"""The filter pattern matcher, pinned to GitHub's own cheat sheet.

CHEAT_SHEET below is transcribed from the "Filter pattern cheat sheet"
section of GitHub's workflow syntax reference: every pattern in the two
tables, with every example match they publish for it. If the matcher stops
agreeing with the documentation, these fail by name.

The non-matches are mine, not GitHub's, and they are the cases where a
glob-shaped implementation would go wrong.
"""

from __future__ import annotations

import pytest

from deadtrigger.pattern import PatternError, compile_all, compile_pattern, included

# (pattern, [documented matches], [things it must not match])
CHEAT_SHEET = [
    # --- Patterns to match branches and tags ---
    ("feature/*", ["feature/my-branch", "feature/your-branch"], ["feature/a/b", "feature"]),
    (
        "feature/**",
        ["feature/beta-a/my-branch", "feature/your-branch", "feature/mona/the/octocat"],
        ["features/x", "feature"],
    ),
    ("main", ["main"], ["mainline", "release/main", "mai"]),
    ("releases/mona-the-octocat", ["releases/mona-the-octocat"], ["releases/mona"]),
    ("*", ["main", "releases"], ["all/the/branches", "releases/v1"]),
    ("**", ["all/the/branches", "every/tag", "main"], []),
    ("*feature", ["mona-feature", "feature", "ver-10-feature"], ["feature-x", "a/feature"]),
    ("v2*", ["v2", "v2.0", "v2.9"], ["v3", "v", "av2"]),
    (
        "v[12].[0-9]+.[0-9]+",
        ["v1.10.1", "v2.0.0"],
        # v3 is out of the class; the trailing `+` means at least one digit,
        # so an empty patch component must not match.
        ["v3.0.0", "v1.10.", "v1.a.1"],
    ),
    # --- Patterns to match file paths ---
    ("*", ["README.md", "server.rb"], ["docs/README.md"]),
    # The `?` case: `x` is optional, so page.js and page.jsx match. A glob
    # implementation reads `?` as "any one character" and lets page.jsz
    # through, which is the bug this whole module exists to avoid.
    ("*.jsx?", ["page.js", "page.jsx"], ["page.jsz", "page.jsxx", "a/page.js"]),
    ("**", ["all/the/files.md"], []),
    ("*.js", ["app.js", "index.js"], ["js/index.js", "src/js/app.js"]),
    ("**.js", ["index.js", "js/index.js", "src/js/app.js"], ["index.jsx", "index.j"]),
    ("docs/*", ["docs/README.md", "docs/file.txt"], ["docs/mona/octocat.txt", "a/docs/x.md"]),
    ("docs/**", ["docs/README.md", "docs/mona/octocat.txt"], ["a/docs/x.md", "documents/x"]),
    (
        "docs/**/*.md",
        ["docs/README.md", "docs/mona/hello-world.md", "docs/a/markdown/file.md"],
        ["docs/README.txt", "a/docs/README.md"],
    ),
    (
        "**/docs/**",
        ["docs/hello.md", "dir/docs/my-file.txt", "space/docs/plan/space.doc"],
        ["dir/documents/x.md", "docs"],
    ),
    ("**/README.md", ["README.md", "js/README.md"], ["README.markdown", "a/READMEx.md"]),
    ("**/*src/**", ["a/src/app.js", "my-src/code/js/app.js"], ["src", "a/source"]),
    ("**/*-post.md", ["my-post.md", "path/their-post.md"], ["post.md", "my-post.markdown"]),
    (
        "**/migrate-*.sql",
        ["migrate-10909.sql", "db/migrate-v1.0.sql", "db/sept/migrate-v1.sql"],
        ["migrate.sql", "db/migrate-v1.sqlx"],
    ),
]


@pytest.mark.parametrize("text,hits,misses", CHEAT_SHEET, ids=[row[0] for row in CHEAT_SHEET])
def test_cheat_sheet(text, hits, misses):
    pattern = compile_pattern(text)
    for candidate in hits:
        assert pattern.matches(candidate), f"{text!r} should match {candidate!r}"
    for candidate in misses:
        assert not pattern.matches(candidate), f"{text!r} should not match {candidate!r}"


def test_patterns_are_anchored_at_both_ends():
    """"Path patterns must match the whole path", per the docs."""
    pattern = compile_pattern("docs/file.txt")
    assert pattern.matches("docs/file.txt")
    assert not pattern.matches("x/docs/file.txt")
    assert not pattern.matches("docs/file.txt.bak")


def test_dot_is_literal():
    """`.` is an ordinary character here, not regex's any-character."""
    assert not compile_pattern("v1.0").matches("v1x0")


def test_plus_needs_at_least_one():
    assert compile_pattern("a+b").matches("ab")
    assert compile_pattern("a+b").matches("aaab")
    assert not compile_pattern("a+b").matches("b")


def test_question_mark_applies_to_a_group_not_a_lazy_quantifier():
    """`**?` must mean "an optional `**`", not a lazily-matched one.

    Translating `?` by appending it to the previous atom would turn `.*` into
    `.*?`, which still matches everything and would hide the bug.
    """
    pattern = compile_pattern("a**?b")
    assert pattern.matches("ab")
    assert pattern.matches("a/x/y/b")


def test_escaping_makes_a_special_character_literal():
    pattern = compile_pattern(r"release\*")
    assert pattern.matches("release*")
    assert not pattern.matches("release-1")
    assert not pattern.has_wildcard


def test_has_wildcard():
    assert not compile_pattern("main").has_wildcard
    assert not compile_pattern("releases/mona-the-octocat").has_wildcard
    for text in ["v2*", "feature/**", "*.jsx?", "a+b", "v[12].0"]:
        assert compile_pattern(text).has_wildcard, text


def test_negation_is_recorded_and_stripped():
    pattern = compile_pattern("!feature/*")
    assert pattern.negated
    assert pattern.matches("feature/x")


def test_bang_is_only_special_at_the_start():
    pattern = compile_pattern("wip!")
    assert not pattern.negated
    assert pattern.matches("wip!")


class TestIncluded:
    """Ordering rules for a whole filter list."""

    def test_last_match_wins_negative_after_positive(self):
        patterns = compile_all(["releases/**", "!releases/**-alpha"])
        assert included(patterns, "releases/v1")
        assert not included(patterns, "releases/v1-alpha")

    def test_last_match_wins_positive_after_negative(self):
        patterns = compile_all(["releases/**", "!releases/**-alpha", "releases/v9-alpha"])
        assert included(patterns, "releases/v9-alpha")

    def test_order_matters(self):
        """The same two patterns the other way round include everything."""
        patterns = compile_all(["!releases/**-alpha", "releases/**"])
        assert included(patterns, "releases/v1-alpha")

    def test_negations_alone_include_nothing(self):
        assert not included(compile_all(["!main"]), "topic")

    def test_no_match_is_not_included(self):
        assert not included(compile_all(["main", "release/**"]), "topic")


class TestPatternError:
    def test_unclosed_class(self):
        with pytest.raises(PatternError):
            compile_pattern("v[12.0")

    def test_non_alphanumeric_class(self):
        """Ranges are documented as a-z / A-Z / 0-9 only."""
        with pytest.raises(PatternError):
            compile_pattern("v[!0-9]")

    def test_trailing_backslash(self):
        with pytest.raises(PatternError):
            compile_pattern("main\\")

    def test_quantifier_with_nothing_to_repeat(self):
        with pytest.raises(PatternError):
            compile_pattern("?main")

    def test_empty(self):
        with pytest.raises(PatternError):
            compile_pattern("")

    def test_bare_bang(self):
        with pytest.raises(PatternError):
            compile_pattern("!")

    def test_non_string(self):
        """YAML will hand us an int for `branches: [2]`."""
        with pytest.raises(PatternError):
            compile_pattern(2)
