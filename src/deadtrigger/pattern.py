"""GitHub Actions filter patterns, translated to regular expressions.

These are the patterns in ``branches``, ``tags``, ``paths`` and their
``-ignore`` variants. They look like shell globs and they are not. From
GitHub's own filter pattern cheat sheet:

    ``?``  Matches zero or one of the **preceding character**.
    ``+``  Matches one or more of the **preceding character**.

That is regex, not glob. In a shell, ``*.jsx?`` means "any character in place
of the ``?``"; to GitHub it means "the ``x`` is optional", which is why the
documented matches for ``*.jsx?`` are ``page.js`` and ``page.jsx`` and not
``page.jsz``. Any implementation that reaches for ``fnmatch`` gets this wrong,
so this module builds the regex by hand.

The rest:

    ``*``   zero or more characters, but never ``/``
    ``**``  zero or more of any character, ``/`` included
    ``[]``  one alphanumeric character from the listed set or ranges
    ``!``   at the *start of a pattern only*, negates earlier positives
    ``\\``  escapes the next character

One special case that does not fall out of the rules as written: ``**/README.md``
is documented as matching ``README.md`` at the repository root. Read literally,
``**`` matching zero characters would leave ``/README.md``, which has a leading
slash and matches nothing. So the ``**/`` sequence has to be able to swallow
its own slash, and is translated as an optional group.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class PatternError(ValueError):
    """A filter pattern that cannot be translated at all."""


# Ranges in a character class are documented as a-z, A-Z and 0-9 only.
_CLASS_BODY = re.compile(r"\A(?:[0-9A-Za-z]|[0-9]-[0-9]|[a-z]-[a-z]|[A-Z]-[A-Z])+\Z")


@dataclass(frozen=True)
class Pattern:
    """One filter pattern, compiled and ready to test paths or ref names."""

    text: str
    negated: bool
    regex: re.Pattern[str]

    def matches(self, candidate: str) -> bool:
        return self.regex.match(candidate) is not None

    @property
    def has_wildcard(self) -> bool:
        """Whether this pattern can match anything but its own literal text.

        Used to tell a branch filter that names one branch from one that
        describes a family of them. An escaped ``\\*`` is a literal star, so
        this walks the pattern rather than searching it for characters.
        """
        i = 0
        while i < len(self.text):
            char = self.text[i]
            if char == "\\":
                i += 2
                continue
            if char in "*?+[":
                return True
            i += 1
        return False


def compile_pattern(text: str) -> Pattern:
    """Translate one filter pattern into a Pattern.

    Raises PatternError if the pattern cannot be understood. The caller is
    expected to treat that as "this check could not run", not as a pass --
    a matcher that silently ignores the pattern it could not read would
    report every filter using it as healthy.
    """
    if not isinstance(text, str):
        raise PatternError(f"not a string: {text!r}")

    negated = text.startswith("!")
    body = text[1:] if negated else text
    if not body:
        raise PatternError("empty pattern")

    # `parts` holds finished regex atoms. Quantifiers (`?`, `+`) attach to the
    # last one, which is why the pieces are kept separate rather than
    # concatenated as they are produced.
    parts: list[str] = []
    i = 0
    length = len(body)

    while i < length:
        char = body[i]

        if char == "\\":
            if i + 1 >= length:
                raise PatternError(f"trailing backslash: {text!r}")
            parts.append(re.escape(body[i + 1]))
            i += 2
            continue

        if char == "*":
            if body.startswith("**", i):
                # `**/` swallows its own slash so that `**/README.md` matches
                # a root-level README.md, as documented.
                if body.startswith("**/", i):
                    parts.append("(?:.*/)?")
                    i += 3
                else:
                    parts.append(".*")
                    i += 2
            else:
                parts.append("[^/]*")
                i += 1
            continue

        if char in "?+":
            if not parts:
                raise PatternError(
                    f"{char!r} has nothing before it to repeat: {text!r}"
                )
            # `.*?` would be a lazy quantifier rather than "an optional `.*`",
            # so the previous atom is wrapped before the quantifier goes on.
            parts[-1] = f"(?:{parts[-1]}){'?' if char == '?' else '+'}"
            i += 1
            continue

        if char == "[":
            end = body.find("]", i + 1)
            if end == -1:
                raise PatternError(f"unclosed '[': {text!r}")
            inner = body[i + 1 : end]
            if not _CLASS_BODY.match(inner):
                raise PatternError(
                    f"character class [{inner}] is not alphanumeric characters "
                    f"or a-z / A-Z / 0-9 ranges: {text!r}"
                )
            parts.append(f"[{inner}]")
            i = end + 1
            continue

        parts.append(re.escape(char))
        i += 1

    return Pattern(text=text, negated=negated, regex=re.compile("".join(parts) + r"\Z"))


def compile_all(texts: list[str]) -> list[Pattern]:
    return [compile_pattern(text) for text in texts]


def included(patterns: list[Pattern], candidate: str) -> bool:
    """Whether an ordered filter list includes ``candidate``.

    Last match wins, per the docs: "a matching negative pattern after a
    positive match will exclude the ref, and a matching positive pattern
    after a negative match will include it again". With no match at all, the
    ref is not included -- a list of nothing but negations includes nothing,
    because there was never anything positive for them to subtract from.
    """
    verdict = False
    for pattern in patterns:
        if pattern.matches(candidate):
            verdict = not pattern.negated
    return verdict
