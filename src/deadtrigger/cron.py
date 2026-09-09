"""Cron expressions, and whether a date exists that satisfies them.

Two things can go wrong with a `schedule:` entry, and neither of them shows
up anywhere in the Actions UI:

* the expression is not valid cron, so nothing is ever scheduled;
* the expression is perfectly valid and describes a date that does not
  happen. ``0 0 30 2 *`` is February the 30th.

The second one is the reason this module computes rather than validates.
The trap is the day-of-month / day-of-week rule: when both fields are
restricted, cron fires if **either** matches, not both. So ``0 0 30 2 1``
does fire -- every Monday in February -- and calling it impossible because
the 30th does not exist would be wrong. Only a schedule whose day-of-week
is unrestricted can be ruled out on its dates alone.

Anything this module does not recognise is reported as unparseable rather
than guessed at, and the caller must not turn that into a pass.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass

MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
        start=1,
    )
}
DAY_NAMES = {
    name: index
    for index, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])
}

# (name, low, high, aliases)
FIELDS = [
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, MONTH_NAMES),
    # 7 is Sunday as well as 0. Vixie cron allows it and rejecting it would
    # be a false "invalid" on a schedule that runs perfectly well.
    ("day of week", 0, 7, DAY_NAMES),
]


class CronError(ValueError):
    """An expression this module will not pretend to understand."""


@dataclass(frozen=True)
class Cron:
    text: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool


def _parse_field(text: str, name: str, low: int, high: int, aliases: dict) -> set[int]:
    values: set[int] = set()

    for item in text.split(","):
        if not item:
            raise CronError(f"empty value in the {name} field")

        step = 1
        if "/" in item:
            item, _, step_text = item.partition("/")
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"bad step {step_text!r} in the {name} field")
            step = int(step_text)

        if item == "*":
            start, end = low, high
        elif "-" in item:
            start_text, _, end_text = item.partition("-")
            start = _value(start_text, name, low, high, aliases)
            end = _value(end_text, name, low, high, aliases)
            if start > end:
                # Vixie cron rejects a reversed range rather than wrapping it.
                raise CronError(f"reversed range {item!r} in the {name} field")
        else:
            start = _value(item, name, low, high, aliases)
            # `5/10` is "from 5 to the top of the field, every 10". Bare `5`
            # with no step is just the one value.
            end = high if step > 1 else start

        values.update(range(start, end + 1, step))

    if not values:
        raise CronError(f"the {name} field matches nothing")
    return values


def _value(text: str, name: str, low: int, high: int, aliases: dict) -> int:
    key = text.strip().upper()
    if key in aliases:
        return aliases[key]
    if not key.isdigit():
        raise CronError(f"{text!r} is not a number in the {name} field")
    number = int(key)
    if not low <= number <= high:
        raise CronError(f"{number} is outside {low}-{high} in the {name} field")
    return number


def parse(text: str) -> Cron:
    """Parse a five-field cron expression, or raise CronError."""
    if not isinstance(text, str):
        raise CronError(f"not a string: {text!r}")

    fields = text.split()
    if len(fields) != 5:
        raise CronError(
            f"{len(fields)} field{'' if len(fields) == 1 else 's'}, expected 5 "
            "(minute hour day-of-month month day-of-week)"
        )

    parsed = [
        _parse_field(field, name, low, high, aliases)
        for field, (name, low, high, aliases) in zip(fields, FIELDS)
    ]
    minutes, hours, doms, months, dows = parsed

    if 7 in dows:
        dows = (dows - {7}) | {0}

    return Cron(
        text=text,
        minutes=frozenset(minutes),
        hours=frozenset(hours),
        days_of_month=frozenset(doms),
        months=frozenset(months),
        days_of_week=frozenset(dows),
        dom_restricted=fields[2] != "*",
        dow_restricted=fields[4] != "*",
    )


def impossible_reason(cron: Cron) -> str | None:
    """Why no date can ever satisfy this cron, or None if one can.

    Only day-of-month against month can make a schedule impossible, and only
    when day-of-week is unrestricted -- otherwise the day-of-week half of
    cron's OR still fires.
    """
    if not cron.dom_restricted or cron.dow_restricted:
        return None

    # 2024 is a leap year, so February 29 counts as a day that exists.
    for month in sorted(cron.months):
        longest = calendar.monthrange(2024, month)[1]
        if any(day <= longest for day in cron.days_of_month):
            return None

    days = ", ".join(str(day) for day in sorted(cron.days_of_month))
    names = ", ".join(calendar.month_name[month] for month in sorted(cron.months))
    return f"day {days} does not occur in {names}"
