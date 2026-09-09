"""Deciding whether a job's ``if:`` can ever be true.

This only answers the question for conditions built entirely out of
``github.event_name``, string literals and booleans. That is a small corner
of the expression language, and deliberately so: the whole value of this
check is that when it fires it is *right*, and there is no way to be right
about ``needs.build.result`` or ``github.event.pull_request.draft`` without
knowing what happened at run time.

So the parser refuses anything it does not fully recognise. A refusal means
the job is not reported, which is the correct failure direction -- a missed
dead job costs nothing, a false accusation costs the tool its credibility.

Why evaluation and not pattern-matching: the condition is evaluated once per
event the workflow actually declares, with ``github.event_name`` bound to
that event. If every one of them comes out false, no event this workflow can
receive will ever start the job. That handles ``||`` chains, negation and
parentheses without special-casing any of them.

Not handled on purpose: ``github.event.pull_request`` on a workflow with no
pull request trigger. It looks like the same kind of check and it is a trap.
The value is ``null``, and in GitHub's expression language ``null == false``
compares 0 to 0 and is **true**, so the obvious reading of a draft check
gets the answer backwards. A tool that guessed here would be confidently
wrong about a job that runs perfectly well.
"""

from __future__ import annotations

import re

EVENT_NAME = "github.event_name"

_TOKENS = re.compile(
    r"""
    \s*(?:
      (?P<op>==|!=|\|\||&&|!|\(|\))
    | (?P<string>'(?:[^']|'')*')
    | (?P<word>[A-Za-z_][A-Za-z_0-9.\-]*)
    )
    """,
    re.VERBOSE,
)


class Unsupported(Exception):
    """The condition uses something this module will not guess about."""


def _tokenise(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKENS.match(text, position)
        if not match:
            raise Unsupported(f"cannot read {text[position:position + 12]!r}")
        kind = match.lastgroup
        tokens.append((kind, match.group(kind)))
        position = match.end()
    return tokens


class _Parser:
    """Recursive descent over the fragment of the language we accept.

    ``event`` is the value bound to github.event_name for this evaluation.
    """

    def __init__(self, tokens: list[tuple[str, str]], event: str) -> None:
        self.tokens = tokens
        self.index = 0
        self.event = event

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise Unsupported("expression ends early")
        self.index += 1
        return token

    def parse(self) -> bool:
        value = self.or_expr()
        if self.peek() is not None:
            raise Unsupported(f"trailing {self.peek()[1]!r}")
        return _truthy(value)

    def or_expr(self):
        value = self.and_expr()
        while self.peek() == ("op", "||"):
            self.take()
            right = self.and_expr()
            # GitHub's || returns the first truthy operand, but every caller
            # here immediately asks whether the result is truthy, so folding
            # to a bool loses nothing.
            value = _truthy(value) or _truthy(right)
        return value

    def and_expr(self):
        value = self.unary()
        while self.peek() == ("op", "&&"):
            self.take()
            right = self.unary()
            value = _truthy(value) and _truthy(right)
        return value

    def unary(self):
        if self.peek() == ("op", "!"):
            self.take()
            return not _truthy(self.unary())
        return self.comparison()

    def comparison(self):
        left = self.primary()
        token = self.peek()
        if token is not None and token[0] == "op" and token[1] in ("==", "!="):
            self.take()
            right = self.primary()
            equal = _equal(left, right)
            return equal if token[1] == "==" else not equal
        return left

    def primary(self):
        kind, text = self.take()
        if (kind, text) == ("op", "("):
            value = self.or_expr()
            if self.take() != ("op", ")"):
                raise Unsupported("unbalanced parentheses")
            return value
        if (kind, text) == ("op", "!"):
            return not _truthy(self.unary())
        if kind == "string":
            return text[1:-1].replace("''", "'")
        if kind == "word":
            if text == EVENT_NAME:
                return self.event
            if text == "true":
                return True
            if text == "false":
                return False
            raise Unsupported(f"{text} is not github.event_name")
        raise Unsupported(f"unexpected {text!r}")


def _equal(left, right) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        # Mixed-type comparison in GitHub's language coerces to numbers, and
        # guessing at that is exactly what this module refuses to do.
        if not (isinstance(left, bool) and isinstance(right, bool)):
            raise Unsupported("comparison mixes a boolean with a string")
    return left == right


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != ""
    raise Unsupported(f"cannot take the truth of {value!r}")


def strip_expression(condition) -> str:
    """The condition with a single wrapping ``${{ }}`` removed.

    A job `if:` may be written with or without the braces. Anything with
    braces *around part of it* is a template with literal text in it, which
    this module does not handle.
    """
    if isinstance(condition, bool):
        return "true" if condition else "false"
    if not isinstance(condition, str):
        raise Unsupported(f"not a condition: {condition!r}")

    text = condition.strip()
    if text.startswith("${{"):
        if not text.endswith("}}") or "${{" in text[3:]:
            raise Unsupported("condition is a template, not a single expression")
        text = text[3:-2].strip()
    elif "${{" in text:
        raise Unsupported("condition is a template, not a single expression")
    if not text:
        raise Unsupported("empty condition")
    return text


def satisfiable_events(condition, events: list[str]) -> list[str]:
    """Which of ``events`` could make ``condition`` true.

    Raises Unsupported if the condition is not one we can decide, which the
    caller must treat as "no opinion" rather than as a pass or a failure.
    """
    text = strip_expression(condition)
    tokens = _tokenise(text)
    if not tokens:
        raise Unsupported("empty condition")

    mentions_event = ("word", EVENT_NAME) in tokens
    if not mentions_event and text not in ("true", "false"):
        # A condition that never looks at the event cannot be decided from
        # the trigger list, and this module has nothing else to go on.
        raise Unsupported("condition does not depend on github.event_name")

    return [event for event in events if _Parser(tokens, event).parse()]
