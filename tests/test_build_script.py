"""build-and-publish.sh with stubbed uv, git and gh: polling, lockout skips, successor dispatch."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "build-and-publish.sh"

UV = """#!/usr/bin/env bash
# uv run f1data <command> ...: build exit codes from $BUILD_CODES (one per call), should-poll from $POLL_ANSWERS.
shift 2
log="$STUBS/calls"
case $1 in
  build)
    n=$(grep -c '^build' "$log" 2>/dev/null); echo "build $*" >> "$log"
    codes=($BUILD_CODES); code=${codes[$n]:-0}
    [ "$code" = 0 ] && echo "{\\"n\\":$n}" > site/v1/manifest.json
    exit "$code" ;;
  should-poll)
    n=$(grep -c '^should-poll' "$log" 2>/dev/null); echo "should-poll" >> "$log"
    answers=($POLL_ANSWERS); echo "${answers[$((2 * n))]:-false} ${answers[$((2 * n + 1))]:-0}" ;;
esac
"""
GIT = """#!/usr/bin/env bash
case $* in *write-tree*) echo tree ;; *commit-tree*) echo commit ;; *push*) echo "push" >> "$STUBS/calls" ;; esac
"""
GH = """#!/usr/bin/env bash
echo "gh $* token=$GH_TOKEN" >> "$STUBS/calls"
"""
SLEEP = """#!/usr/bin/env bash
echo "sleep $1" >> "$STUBS/calls"
"""


def run(tmp_path, *, poll, build_codes, poll_answers="", budget=19800):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in {"uv": UV, "git": GIT, "gh": GH, "sleep": SLEEP}.items():
        f = stubs / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    work = tmp_path / "work"
    (work / "site" / "v1").mkdir(parents=True)
    env = {
        **os.environ,
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "STUBS": str(stubs),
        "YEAR": "2026",
        "POLL": "true" if poll else "false",
        "POLL_BUDGET_S": str(budget),
        "BUILD_CODES": build_codes,
        "POLL_ANSWERS": poll_answers,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": "1",
        "GITHUB_TOKEN": "t0k",
        "GITHUB_REPOSITORY": "owner/f1-data",
        "GITHUB_REF_NAME": "main",
    }
    proc = subprocess.run(["bash", str(SCRIPT), "--year", "2026"], cwd=work, env=env, capture_output=True, text=True)
    calls = (stubs / "calls").read_text().splitlines()
    return proc, calls


def test_polls_until_window_closes_without_dispatching(tmp_path):
    proc, calls = run(tmp_path, poll=True, build_codes="0 0 0", poll_answers="true 900 true 1800 false 0")
    assert proc.returncode == 0
    assert [c.split()[0] for c in calls if not c.startswith("push")] == [
        "build", "should-poll", "sleep", "build", "should-poll", "sleep", "build", "should-poll"
    ]  # fmt: skip
    assert [c for c in calls if c.startswith("sleep")] == ["sleep 900", "sleep 1800"]
    assert not any(c.startswith("gh") for c in calls)
    assert "stopping after 3 polls" in proc.stdout


def test_dispatches_successor_when_budget_runs_out_and_window_is_open(tmp_path):
    proc, calls = run(tmp_path, poll=True, build_codes="0", poll_answers="true 900", budget=600)
    assert proc.returncode == 0
    gh = [c for c in calls if c.startswith("gh")]
    assert gh == ["gh workflow run build.yml --repo owner/f1-data --ref main -f year=2026 -f poll=true token=t0k"]
    assert not any(c.startswith("sleep") for c in calls)


def test_no_successor_when_window_closed_at_budget_end(tmp_path):
    proc, calls = run(tmp_path, poll=True, build_codes="0", poll_answers="false 0", budget=600)
    assert proc.returncode == 0
    assert not any(c.startswith("gh") for c in calls)


def test_live_lockout_skips_the_poll_and_keeps_polling(tmp_path):
    proc, calls = run(tmp_path, poll=True, build_codes="75 0", poll_answers="true 900 false 0")
    assert proc.returncode == 0
    assert sum(c.startswith("build") for c in calls) == 2
    assert "poll 1: OpenF1 locked during a live session; skipping this poll" in proc.stdout


def test_failed_poll_keeps_polling_and_fails_at_the_end(tmp_path):
    proc, calls = run(tmp_path, poll=True, build_codes="1 0", poll_answers="true 900 false 0")
    assert proc.returncode == 1
    assert sum(c.startswith("build") for c in calls) == 2


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (75, 0), (1, 1)])
def test_single_run(tmp_path, code, expected):
    proc, calls = run(tmp_path, poll=False, build_codes=str(code))
    assert proc.returncode == expected
    assert [c.split()[0] for c in calls if not c.startswith("push")] == ["build"]
    if code == 75:
        assert "skipped, live session" in proc.stdout
