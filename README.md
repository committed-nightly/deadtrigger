# deadtrigger

Finds GitHub Actions workflows that can never run, by checking their triggers
against the repository they are actually in. It is for anyone who has ever
pushed a change, waited for a workflow, and slowly worked out that it was
never going to start.

A workflow that fails is loud. A workflow that is never triggered at all looks
exactly like a workflow with nothing to do: no run, no annotation, no red tick,
nothing in the Actions tab to click on. `deadtrigger` is the thing that
notices.

## Install

```
pip install git+https://github.com/committed-nightly/deadtrigger
```

Python 3.10 or newer. One dependency, PyYAML.

## Use

```
cd your-repo
deadtrigger
```

It reads the workflow files as they are on disk — including one you have
written but not yet `git add`ed — so you can check a filter before you commit
it. What they are checked *against* still comes from git: an untracked build
directory is not something anyone can change in a commit, so it can't make a
dead path filter look alive.

`--ref` checks a revision instead, `--json` gives you the same findings in a
form you can pipe somewhere.

## A real example

Four workflows, none of which is obviously wrong:

```bash
mkdir demo && cd demo && git init -b main
mkdir -p src docs .github/workflows
echo "print('hi')" > src/app.py
echo "# docs" > docs/index.md

cat > .github/workflows/ci.yml <<'YAML'
name: CI
on:
  push:
    branches: [master]
jobs:
  test:
    runs-on: ubuntu-latest
    steps: [{run: pytest}]
YAML

cat > .github/workflows/docs.yml <<'YAML'
name: Docs
on:
  pull_request:
    paths:
      - src/**
      - docs
jobs:
  build:
    runs-on: ubuntu-latest
    steps: [{run: make html}]
YAML

cat > .github/workflows/nightly.yml <<'YAML'
name: Nightly
on:
  schedule:
    - cron: '0 0 30 2 *'
jobs:
  soak:
    runs-on: ubuntu-latest
    steps: [{run: make soak}]
YAML

cat > .github/workflows/publish.yml <<'YAML'
name: Publish
on:
  workflow_run:
    workflows: [Build]
    types: [completed]
jobs:
  upload:
    runs-on: ubuntu-latest
    steps: [{run: make upload}]
YAML

git add -A && git commit -qm "a plausible looking set of workflows"
deadtrigger --default-branch main
```

```
  .github/workflows/publish.yml
      on.workflow_run.workflows: 'Build'
      no-such-workflow: no workflow in .github/workflows has this name, so
      nothing can ever trigger this one

  .github/workflows/nightly.yml
      on.schedule: '0 0 30 2 *'
      dead-cron: valid cron for a date that never happens: day 30 does not occur
      in February

  .github/workflows/ci.yml
      on.push.branches: master
      unmatched-ref: no branch in this repository is called that

  .github/workflows/docs.yml
      on.pull_request.paths: docs
      unmatched-path: docs is a directory, and a path filter is matched against
      the paths of changed *files*. Editing anything under it does not trigger
      this workflow -- docs/** does

4 workflows checked, 4 findings
```

Four workflows, four ways of never running, and not one of them would show up
as a failure anywhere. The `docs` one is the meanest: `src/**` is right there
next to it and works, so the workflow does run — just never for a
documentation change, which is the only thing it was for.

## What it checks

| kind | what it means |
| --- | --- |
| `no-such-workflow` | `on.workflow_run.workflows` names a workflow no file in `.github/workflows` is called |
| `off-default-branch` | `schedule`, `workflow_dispatch` or `workflow_run` on a workflow that is not on the default branch, where GitHub never delivers them |
| `dead-cron` | a cron GitHub will not accept, or one describing a date that does not happen |
| `unmatched-ref` | a branch or tag filter naming a ref that does not exist |
| `dead-path-filter` | a `paths:` list that no file in the repository satisfies, so the event can never start the workflow |
| `unmatched-path` | one pattern in a path filter that matches no file |
| `impossible-if` | a job `if:` that no event this workflow receives can make true |

Exit codes, because this is meant to sit in CI: **0** everything can fire,
**1** something cannot, **2** the check could not run — no repository, no
workflows, a revision that does not resolve, a filter pattern that cannot be
parsed. 2 is deliberately not 0. A checker that passes when it did not run is
the failure this whole tool exists to catch.

## In CI

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- run: pip install git+https://github.com/committed-nightly/deadtrigger
- run: deadtrigger --default-branch ${{ github.event.repository.default_branch }}
```

`fetch-depth: 0` matters. At the default depth `actions/checkout` leaves a
shallow clone holding one branch, and a tool that read that as "this
repository has one branch" would report every branch filter you own as dead.
Instead the branch and tag checks decline to run, and say so:

```
not checked: branch and tag filters -- this clone is shallow, so its branch
and tag lists are the ones fetched rather than the ones that exist
```

Every check that does not run is named in the output, on green runs too.

## What it is not

It is not a workflow linter, and if you are not already running one, run
[actionlint](https://github.com/rhysd/actionlint) first — it type-checks
expressions, shellchecks your `run:` blocks and catches far more than this
does.

The difference is the input. actionlint reads a workflow file; `deadtrigger`
reads it *against the repository*. `branches: [master]` is valid YAML, valid
Actions and a perfectly good filter — the only thing wrong with it is that
this repository's branch is called `main`, which is not a fact any linter has.
Same for a `workflow_run` naming a workflow that was renamed last year, and
for a `paths` filter pointing at a directory that has moved.

## Known limits

**Filters can be legitimately forward-looking.** A `paths` filter can name a
directory you are about to add. That is reported anyway, because "nothing in
this repository can trigger this workflow today" is still true and still worth
knowing, but it is the false-positive case and the first thing to check.
Branch and tag filters get more slack: only patterns with *no wildcard in
them* are reported, because `release/**` in a repository with no release
branches is a filter waiting for a branch, which is a normal thing to write
and none of this tool's business.

**`impossible-if` is the weakest check here.** It refuses any condition it
cannot decide with certainty, which in practice is most of them: across the
168 job conditions in the validation set below it reached a verdict on 6.
When it does fire it is right, and a job gated on an event its workflow no
longer has is genuinely invisible — but do not expect it to earn its keep on
your repository.

**It does not guess at GitHub's expression evaluator.** Checking
`github.event.pull_request` on a workflow with no pull request trigger looks
like it belongs here, and it is a trap: the value is `null`, and `null ==
false` compares 0 to 0 and is **true**, so the obvious reading of a draft
check comes out backwards. Refusing to answer beat guessing.

**Not checked:** step-level `if:`, the five-minute floor on cron intervals
(GitHub runs those less often rather than never), and whether a
`workflow_call` workflow has any caller — a reusable workflow can be called
from another repository, and this only ever looks at one.

## Where the matcher came from

GitHub's filter patterns look like shell globs and are not:

> `?` Matches zero or one of the **preceding character**.
> `+` Matches one or more of the **preceding character**.

Those are regex quantifiers, which is why GitHub's own documented matches for
`*.jsx?` are `page.js` and `page.jsx` and not `page.jsz`. Anything built on
`fnmatch` gets this wrong. Every row of both tables in GitHub's [filter
pattern cheat sheet](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#filter-pattern-cheat-sheet)
is a case in `tests/test_pattern.py` with the documented example matches kept
verbatim, so if the matcher ever stops agreeing with the documentation it
fails by name.

## Validation

Run against 148 workflows in 18 repositories — `cli/cli`, `astral-sh/ruff`,
`tiangolo/fastapi`, `pydantic/pydantic`, `psf/black`, `pallets/flask`,
`actions/checkout`, `scrapy/scrapy` and others — covering 163 path patterns,
89 branch and tag patterns, 39 schedules and 5 `workflow_run` references.

Three findings, all in one repository, all real: `astral-sh/ruff`'s typing
conformance workflow lists `crates/ruff_db`, `crates/ruff_python_ast` and
`crates/ruff_python_parser` in its `paths` filter, on the lines immediately
around `crates/ruff_python_edits/**`. Those three are directories, path
filters only ever match the paths of changed files, and so a change to any of
those three crates does not run that workflow.

Seventeen of the eighteen came out clean. The honest reading is that
well-maintained repositories mostly do not have this problem, and that this is
a tool for the day you go looking for why something never ran.

## Development

```
pip install -e ".[dev]"
python -m pytest
```

177 tests. The end-to-end ones build real git repositories and make real
commits in them, because the question this tool answers is about a repository,
and faking that away would leave the interesting part untested.

## Licence

MIT.
