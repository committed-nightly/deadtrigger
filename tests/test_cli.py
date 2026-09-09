"""End to end, against real repositories built by the `repo` fixture."""

from __future__ import annotations

import io
import json

import pytest

from deadtrigger.cli import EXIT_DEAD, EXIT_ERROR, EXIT_OK, main

HEALTHY = """
    name: CI
    on:
      push:
        branches: [main]
        paths: ['src/**']
    jobs:
      build:
        runs-on: ubuntu-latest
        steps: [{run: 'true'}]
"""


def run(repo, *args):
    out, err = io.StringIO(), io.StringIO()
    code = main([repo.path, *args], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def run_json(repo, *args):
    code, out, err = run(repo, "--json", *args)
    return code, json.loads(out), err


@pytest.fixture
def healthy(repo):
    repo.write("src/app.py")
    repo.workflow("ci.yml", HEALTHY)
    repo.commit()
    return repo


class TestHealthy:
    def test_clean_repository_exits_zero(self, healthy):
        code, out, _ = run(healthy)
        assert code == EXIT_OK
        assert "1 workflow checked, clean" in out

    def test_nothing_is_skipped_in_a_full_clone_with_a_default_branch(self, healthy):
        code, out, _ = run(healthy, "--default-branch", "main")
        assert code == EXIT_OK
        assert "not checked:" not in out


class TestWorkflowRun:
    def test_name_that_matches_nothing(self, repo):
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.workflow(
            "after.yml",
            """
            name: After
            on:
              workflow_run:
                workflows: [Build]
                types: [completed]
            jobs: {}
            """,
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        finding = next(f for f in data["findings"] if f["kind"] == "no-such-workflow")
        assert finding["subject"] == "'Build'"
        assert "nothing can ever trigger" in finding["detail"]

    def test_name_that_matches_is_clean(self, repo):
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.workflow(
            "after.yml",
            "name: After\non:\n  workflow_run:\n    workflows: [CI]\njobs: {}\n",
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_unnamed_workflow_is_matched_by_its_path(self, repo):
        """A workflow with no name: is shown by GitHub as its path, so a
        workflow_run naming that path is not dead."""
        repo.workflow("ci.yml", "on: push\njobs: {}\n")
        repo.workflow(
            "after.yml",
            "name: After\non:\n  workflow_run:\n"
            "    workflows: ['.github/workflows/ci.yml']\njobs: {}\n",
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_one_bad_name_among_good_ones_is_still_reported(self, repo):
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.workflow(
            "after.yml",
            "name: After\non:\n  workflow_run:\n    workflows: [CI, Buidl]\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        finding = data["findings"][0]
        assert finding["subject"] == "'Buidl'"
        assert "fires less often than it reads" in finding["detail"]


class TestSchedule:
    def test_impossible_date(self, repo):
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '0 0 30 2 *'\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["kind"] == "dead-cron"
        assert "February" in data["findings"][0]["detail"]

    def test_invalid_cron(self, repo):
        repo.workflow(
            "nightly.yml", "name: N\non:\n  schedule:\n    - cron: '0 0 * *'\njobs: {}\n"
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert "expected 5" in data["findings"][0]["detail"]

    def test_schedule_declared_with_no_cron(self, repo):
        """`on: [push, schedule]` reads like a nightly build and schedules
        nothing at all."""
        repo.workflow("nightly.yml", "name: N\non: [push, schedule]\njobs: {}\n")
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert "no cron entry" in data["findings"][0]["detail"]

    def test_ordinary_schedule_is_clean(self, repo):
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '17 22 * * *'\njobs: {}\n",
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK


class TestRefFilters:
    def test_literal_branch_that_does_not_exist(self, repo):
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    branches: [master]\njobs: {}\n"
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["kind"] == "unmatched-ref"
        assert data["findings"][0]["subject"] == "master"

    def test_literal_branch_that_exists_only_as_a_remote_is_fine(self, repo):
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    branches: [release]\njobs: {}\n"
        )
        repo.commit()
        repo.git("update-ref", "refs/remotes/origin/release", "HEAD")
        assert run(repo)[0] == EXIT_OK

    def test_wildcard_branch_pattern_is_left_alone(self, repo):
        """`release/**` with no release branches yet is a filter waiting for
        a branch, not a mistake. Nagging about it would make the tool useless
        in every repository that plans ahead."""
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    branches: [main, 'release/**']\njobs: {}\n",
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_literal_tag(self, repo):
        repo.workflow(
            "rel.yml", "name: R\non:\n  push:\n    tags: [v1.0.0]\njobs: {}\n"
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert "no tag in this repository" in data["findings"][0]["detail"]

    def test_existing_tag_is_clean(self, repo):
        repo.workflow(
            "rel.yml", "name: R\non:\n  push:\n    tags: [v1.0.0]\njobs: {}\n"
        )
        repo.commit()
        repo.tag("v1.0.0")
        assert run(repo)[0] == EXIT_OK

    def test_branches_ignore_is_checked_too(self, repo):
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    branches-ignore: [gh-pages]\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["subject"] == "gh-pages"

    def test_scalar_form_of_a_filter(self, repo):
        """`branches: master` without the brackets is legal YAML and legal
        Actions."""
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    branches: master\njobs: {}\n"
        )
        repo.commit()
        assert run(repo)[0] == EXIT_DEAD


class TestPathFilters:
    def test_whole_filter_dead(self, repo):
        repo.write("app/main.py")
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    paths: ['src/**']\njobs: {}\n"
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["kind"] == "dead-path-filter"
        assert "push can never start this workflow" in data["findings"][0]["detail"]

    def test_one_dead_pattern_among_live_ones(self, repo):
        repo.write("src/main.py")
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    paths: ['src/**', 'lib/**']\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert [f["kind"] for f in data["findings"]] == ["unmatched-path"]
        assert data["findings"][0]["subject"] == "lib/**"

    def test_dead_filter_is_reported_once_not_per_pattern(self, repo):
        repo.write("app/main.py")
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    paths: ['src/**', 'lib/**']\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert len(data["findings"]) == 1

    def test_negated_pattern_that_excludes_nothing_is_left_alone(self, repo):
        """An exclusion guarding against files that do not exist yet is what
        an exclusion is for."""
        repo.write("src/main.py")
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    paths: ['src/**', '!src/vendor/**']\njobs: {}\n",
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_paths_ignore_that_matches_nothing(self, repo):
        repo.write("src/main.py")
        repo.workflow(
            "ci.yml",
            "name: CI\non:\n  push:\n    paths-ignore: ['docs/**']\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["kind"] == "unmatched-path"

    def test_the_regex_quantifier_really_is_used(self, repo):
        """`*.jsx?` must not be satisfied by a file called page.jsz. If the
        matcher ever falls back to fnmatch, this goes green and stops
        catching anything."""
        repo.write("page.jsz")
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    paths: ['*.jsx?']\njobs: {}\n"
        )
        repo.commit()
        assert run(repo)[0] == EXIT_DEAD


class TestJobConditions:
    def test_condition_naming_an_event_the_workflow_lacks(self, repo):
        repo.workflow(
            "ci.yml",
            """
            name: CI
            on: push
            jobs:
              nightly:
                if: github.event_name == 'schedule'
                runs-on: ubuntu-latest
                steps: [{run: 'true'}]
            """,
        )
        repo.commit()
        code, data, _ = run_json(repo)
        assert code == EXIT_DEAD
        assert data["findings"][0]["kind"] == "impossible-if"
        assert "triggers: push" in data["findings"][0]["detail"]

    def test_condition_that_can_fire_is_clean(self, repo):
        repo.workflow(
            "ci.yml",
            """
            name: CI
            on:
              push:
              schedule:
                - cron: '0 3 * * *'
            jobs:
              nightly:
                if: github.event_name == 'schedule'
                steps: []
            """,
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_undecidable_condition_is_not_reported(self, repo):
        repo.workflow(
            "ci.yml",
            """
            name: CI
            on: push
            jobs:
              deploy:
                if: github.ref == 'refs/heads/main'
                steps: []
            """,
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK

    def test_reusable_workflows_are_left_alone(self, repo):
        """Under workflow_call, github.event_name is the caller's event and
        is not knowable from this file."""
        repo.workflow(
            "reusable.yml",
            """
            name: Reusable
            on: workflow_call
            jobs:
              only-on-push:
                if: github.event_name == 'push'
                steps: []
            """,
        )
        repo.commit()
        assert run(repo)[0] == EXIT_OK


class TestDefaultBranch:
    def test_schedule_on_a_branch_that_is_not_the_default(self, repo):
        repo.write("README.md")
        repo.commit()
        repo.git("checkout", "-q", "-b", "topic")
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs: {}\n",
        )
        repo.commit()
        code, data, _ = run_json(repo, "--default-branch", "main")
        assert code == EXIT_DEAD
        finding = data["findings"][0]
        assert finding["kind"] == "off-default-branch"
        assert finding["subject"] == "schedule"

    def test_same_workflow_on_the_default_branch_is_clean(self, repo):
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs: {}\n",
        )
        repo.commit()
        assert run(repo, "--default-branch", "main")[0] == EXIT_OK

    def test_push_only_workflow_off_the_default_branch_is_fine(self, repo):
        """push is delivered to whatever branch the workflow is on. Only
        schedule, workflow_dispatch and workflow_run are stranded."""
        repo.write("README.md")
        repo.commit()
        repo.git("checkout", "-q", "-b", "topic")
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.commit()
        assert run(repo, "--default-branch", "main")[0] == EXIT_OK

    def test_default_branch_that_only_exists_as_a_remote_ref(self, repo, tmp_path):
        """The shape actions/checkout leaves behind on a pull request: the
        default branch is refs/remotes/origin/main and nothing else, where
        plain `git rev-parse main` fails. If --default-branch quietly did
        nothing here it would do nothing on every CI run."""
        repo.write("README.md")
        repo.commit()
        repo.git("checkout", "-q", "-b", "topic")
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs: {}\n",
        )
        repo.commit()

        import subprocess

        clone = tmp_path / "pr"
        subprocess.run(
            ["git", "clone", "-q", "--no-checkout", "file://" + repo.path, str(clone)],
            check=True, capture_output=True,
        )
        # --no-checkout never creates a local `main`, so after this the only
        # trace of the default branch is refs/remotes/origin/main.
        subprocess.run(
            ["git", "checkout", "-q", "-b", "pr-merge", "origin/topic"],
            cwd=clone, check=True, capture_output=True,
        )
        assert subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "main^{commit}"],
            cwd=clone, capture_output=True,
        ).returncode != 0

        out = io.StringIO()
        code = main([str(clone), "--default-branch", "main"], out=out, err=io.StringIO())
        assert "not checked: default-branch-only triggers" not in out.getvalue()
        assert code == EXIT_DEAD
        assert "off-default-branch" in out.getvalue()

    def test_check_is_named_as_skipped_when_the_default_branch_is_unknown(self, repo):
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.commit()
        _, out, _ = run(repo)
        assert "not checked: default-branch-only triggers" in out

    def test_a_default_branch_that_does_not_exist_skips_rather_than_accuses(self, repo):
        repo.workflow(
            "nightly.yml",
            "name: N\non:\n  schedule:\n    - cron: '0 3 * * *'\njobs: {}\n",
        )
        repo.commit()
        code, out, _ = run(repo, "--default-branch", "trunk")
        assert code == EXIT_OK
        assert "not checked: default-branch-only triggers" in out


class TestSkippedChecks:
    def test_shallow_clone_declines_the_branch_check(self, repo, tmp_path):
        """A shallow clone holds the branches it fetched, not the ones that
        exist, so `master` there proves nothing."""
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    branches: [master]\njobs: {}\n"
        )
        repo.commit()

        clone = tmp_path / "shallow"
        import subprocess

        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", "file://" + repo.path, str(clone)],
            check=True,
            capture_output=True,
        )

        out = io.StringIO()
        code = main([str(clone)], out=out, err=io.StringIO())
        assert code == EXIT_OK
        assert "not checked: branch and tag filters" in out.getvalue()

    def test_skips_are_listed_in_json_too(self, repo):
        repo.workflow("ci.yml", "name: CI\non: push\njobs: {}\n")
        repo.commit()
        _, data, _ = run_json(repo)
        assert [s["check"] for s in data["skipped"]] == ["default-branch-only triggers"]


class TestErrors:
    def test_not_a_repository(self, tmp_path):
        out, err = io.StringIO(), io.StringIO()
        assert main([str(tmp_path)], out=out, err=err) == EXIT_ERROR
        assert "not a git repository" in err.getvalue()

    def test_not_a_directory(self, tmp_path):
        err = io.StringIO()
        assert main([str(tmp_path / "nope")], out=io.StringIO(), err=err) == EXIT_ERROR
        assert "not a directory" in err.getvalue()

    def test_no_workflows_is_an_error_not_a_pass(self, repo):
        """The whole point of this tool is that a check which did not run
        must not look like a check that passed."""
        repo.write("README.md")
        repo.commit()
        err = io.StringIO()
        assert main([repo.path], out=io.StringIO(), err=err) == EXIT_ERROR
        assert ".github/workflows" in err.getvalue()

    def test_bad_ref(self, healthy):
        code, _, err = run(healthy, "--ref", "nope")
        assert code == EXIT_ERROR
        assert "no such revision: nope" in err

    def test_unreadable_workflow(self, repo):
        repo.workflow("ci.yml", "name: [\n")
        repo.commit()
        code, _, err = run(repo)
        assert code == EXIT_ERROR
        assert "not valid YAML" in err

    def test_unparseable_pattern_is_an_error_not_a_pass(self, repo):
        """A pattern we cannot read means we cannot say whether the filter is
        dead. Exit 2, not a green tick."""
        repo.write("src/main.py")
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    paths: ['src/[!x]/**']\njobs: {}\n"
        )
        repo.commit()
        code, _, err = run(repo)
        assert code == EXIT_ERROR
        assert "on.push.paths" in err


class TestUncommittedWork:
    """With no --ref the answer is about the files as they are now, which is
    the only moment at which it can still save you a push."""

    def test_a_staged_workflow_is_checked(self, repo):
        repo.write("src/main.py")
        repo.workflow("ci.yml", "name: CI\non:\n  push:\n    paths: ['nope/**']\njobs: {}\n")
        assert run(repo)[0] == EXIT_DEAD

    def test_an_uncommitted_edit_is_checked(self, repo):
        repo.write("src/main.py")
        repo.workflow("ci.yml", "name: CI\non:\n  push:\n    paths: ['src/**']\njobs: {}\n")
        repo.commit()
        assert run(repo)[0] == EXIT_OK

        # Edited on disk, not staged, not committed.
        import os

        with open(
            os.path.join(repo.path, ".github/workflows/ci.yml"), "w", encoding="utf-8"
        ) as handle:
            handle.write("name: CI\non:\n  push:\n    paths: ['lib/**']\njobs: {}\n")
        assert run(repo)[0] == EXIT_DEAD

    def test_untracked_files_are_not_counted_as_matches(self, repo):
        """git decides what is in the repository, not the filesystem: a build
        directory nobody committed must not make a path filter look alive."""
        repo.write("src/main.py")
        repo.workflow("ci.yml", "name: CI\non:\n  push:\n    paths: ['build/**']\njobs: {}\n")
        repo.commit()

        import os

        os.makedirs(os.path.join(repo.path, "build"))
        with open(os.path.join(repo.path, "build/out.o"), "w") as handle:
            handle.write("x")
        assert run(repo)[0] == EXIT_DEAD


class TestRef:
    def test_ref_reads_the_workflows_at_that_revision(self, repo):
        repo.write("src/main.py")
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    paths: ['src/**']\njobs: {}\n"
        )
        first = repo.commit()
        repo.workflow(
            "ci.yml", "name: CI\non:\n  push:\n    paths: ['lib/**']\njobs: {}\n"
        )
        repo.commit()

        assert run(repo)[0] == EXIT_DEAD
        assert run(repo, "--ref", first)[0] == EXIT_OK
