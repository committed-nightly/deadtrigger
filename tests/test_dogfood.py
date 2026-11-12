"""The dogfood job's own assertions, where they can be tested at all.

`.github/workflows/ci.yml` ends with a step that reads the tool's report of
this repository and fails if anything in it was declined:

    deadtrigger --default-branch main > report.txt
    if grep -q '^not checked:' report.txt; then ... exit 1; fi

`deadassert` reported that as `unwitnessed` (logbook#64): it fails only when
its pattern appears, and nothing in those workflows established that the
pattern can appear at all. If the two skips stopped working -- if
`is_shallow` always answered False, say -- the step would have gone on
passing, and so would the step above it that exists to prove the
`fetch-depth: 0` advice is needed.

The fix is in ci.yml rather than here. A witness has to come from a corpus
where `not checked:` really is printed, and it cannot be this repository,
because the guard requires the string's absence here. The job now builds one
control per cause and greps each for the line it should produce.

That leaves one thing a control cannot tell you, and it is what this file
is for: whether the controls still cover every check the tool can decline.
Add a third `report.skip(...)` to `core.check` and the guard quietly goes
back to being part-witnessed -- two causes proven, one not, output
indistinguishable.

The behaviour of the skips themselves is tested properly elsewhere and
belongs there: `TestSkippedChecks.test_shallow_clone_declines_the_branch_check`,
`test_check_is_named_as_skipped_when_the_default_branch_is_unknown` and
`TestHealthy.test_nothing_is_skipped_in_a_full_clone_with_a_default_branch`,
all in test_cli.py. This file does not repeat them.

Reading source text in a test is a cost, and it is deliberate: the thing
being checked is a relationship between a Python file and a YAML file, and
nothing at runtime holds both.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "deadtrigger" / "core.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"

#: Every check `core.check` can decline, by the name the CLI prints for it.
#: One control in the dogfood job per entry.
DECLINABLE = ("branch and tag filters", "default-branch-only triggers")


def skip_calls() -> tuple[int, list[str]]:
    """How many times `core.check` declines, and the names it declines under.

    The two numbers are returned together on purpose. A `report.skip(reason)`
    built from a variable would be invisible to the second, and the test
    below notices because it is no longer invisible to the first.
    """
    source = CORE.read_text(encoding="utf-8")
    calls = re.findall(r"report\.skip\(", source)
    named = re.findall(r'report\.skip\(\s*"([^"]+)"', source)
    return len(calls), named


def test_every_declinable_check_names_itself_with_a_literal() -> None:
    calls, named = skip_calls()
    assert calls == len(named), (
        f"{calls} skip call(s) in core.py, {len(named)} of them with a literal "
        "name. The rest cannot be matched up with a control in the dogfood "
        "job; give the check a literal name or witness it some other way."
    )
    assert sorted(named) == sorted(DECLINABLE)


def test_every_declinable_check_has_a_witness_in_the_dogfood_job() -> None:
    ci = CI.read_text(encoding="utf-8")
    missing = [name for name in DECLINABLE if f"^not checked: {name} --" not in ci]
    assert not missing, (
        f"declined without a control in the dogfood job: {missing}. The "
        "`^not checked:` guard in ci.yml passes when nothing is declined, so "
        "each cause needs a corpus where it fires and a grep that names it."
    )
