"""Cron parsing, and the day-of-month / day-of-week OR rule."""

from __future__ import annotations

import pytest

from deadtrigger.cron import CronError, impossible_reason, parse


def impossible(text: str) -> str | None:
    return impossible_reason(parse(text))


class TestParse:
    def test_every_minute(self):
        cron = parse("* * * * *")
        assert len(cron.minutes) == 60
        assert len(cron.hours) == 24
        assert not cron.dom_restricted
        assert not cron.dow_restricted

    def test_a_real_schedule(self):
        cron = parse("30 5,17 * * *")
        assert cron.minutes == {30}
        assert cron.hours == {5, 17}

    def test_step(self):
        assert parse("*/15 * * * *").minutes == {0, 15, 30, 45}

    def test_range_with_step(self):
        assert parse("0 9-17/4 * * *").hours == {9, 13, 17}

    def test_range(self):
        assert parse("0 0 * * 1-5").days_of_week == {1, 2, 3, 4, 5}

    def test_names(self):
        assert parse("0 0 1 JAN,DEC *").months == {1, 12}
        assert parse("0 0 * * MON-FRI").days_of_week == {1, 2, 3, 4, 5}

    def test_names_are_case_insensitive(self):
        assert parse("0 0 * * sun").days_of_week == {0}

    def test_seven_is_sunday(self):
        """Vixie cron accepts 7 for Sunday; calling it invalid would be a
        false report on a schedule that runs fine."""
        assert parse("0 0 * * 7").days_of_week == {0}
        assert parse("0 0 * * 0,7").days_of_week == {0}

    def test_value_with_step_runs_to_the_top_of_the_field(self):
        assert parse("5/20 * * * *").minutes == {5, 25, 45}


class TestParseErrors:
    @pytest.mark.parametrize(
        "text",
        [
            "* * * *",  # four fields
            "* * * * * *",  # six -- seconds, which cron does not have
            "",
            "60 * * * *",  # minute 60
            "* 24 * * *",  # hour 24
            "0 0 0 * *",  # day of month 0
            "0 0 * 13 *",  # month 13
            "0 0 * * 8",  # day of week 8
            "0 0 * * MOO",
            "0 0 * * 5-1",  # reversed range
            "*/0 * * * *",  # zero step
            "0,, * * * *",
            "@daily",  # GitHub does not take the nicknames
        ],
    )
    def test_rejected(self, text):
        with pytest.raises(CronError):
            parse(text)

    def test_not_a_string(self):
        """`- cron: 5` in YAML arrives here as an int."""
        with pytest.raises(CronError):
            parse(5)


class TestImpossible:
    def test_ordinary_schedules_are_possible(self):
        assert impossible("0 3 * * *") is None
        assert impossible("30 5 1 * *") is None
        assert impossible("0 0 * * 1") is None

    def test_february_30th(self):
        assert impossible("0 0 30 2 *") == "day 30 does not occur in February"

    def test_april_31st(self):
        assert impossible("0 0 31 4 *") is not None

    def test_february_29th_is_possible(self):
        """Leap years happen. A schedule can legitimately want one."""
        assert impossible("0 0 29 2 *") is None

    def test_one_reachable_month_is_enough(self):
        """31 is impossible in April but fine in May."""
        assert impossible("0 0 31 4,5 *") is None

    def test_one_reachable_day_is_enough(self):
        assert impossible("0 0 28,31 2 *") is None

    def test_day_of_week_rescues_an_impossible_day_of_month(self):
        """cron ORs the two day fields when both are restricted, so this
        fires every Monday in February and is not dead."""
        assert impossible("0 0 30 2 1") is None

    def test_all_impossible_days_across_all_short_months(self):
        assert impossible("0 0 31 2,4,6,9,11 *") is not None
