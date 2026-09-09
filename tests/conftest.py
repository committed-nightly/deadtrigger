"""A real git repository per test.

The checks are about a repository, so the tests build one: real commits,
real branches, real tags, real `git ls-tree`. Faking the git layer would
leave the part most likely to be wrong -- what a checkout can actually see
-- untested.
"""

from __future__ import annotations

import subprocess
import textwrap

import pytest


class Repo:
    def __init__(self, path):
        self.path = str(path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")

    def git(self, *args) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, check=True
        )
        return proc.stdout.decode()

    def write(self, relpath: str, content: str = "x\n") -> None:
        import os

        full = os.path.join(self.path, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True) if os.path.dirname(
            relpath
        ) else None
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(textwrap.dedent(content))
        self.git("add", relpath)

    def workflow(self, name: str, content: str) -> None:
        self.write(f".github/workflows/{name}", content)

    def commit(self, message: str = "commit") -> str:
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def branch(self, name: str) -> None:
        self.git("branch", name)

    def tag(self, name: str) -> None:
        self.git("tag", name)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)
