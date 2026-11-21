#!/usr/bin/env bash
# Build and publish once or, with POLL=true, repeatedly while `f1data should-poll` says so (it prints
# `true <interval_s>` or `false 0`), until POLL_BUDGET_S seconds have passed (the job timeout is 6 h).
# When the budget runs out and polling should go on, the run dispatches its successor (workflow_dispatch with
# poll=true, via GITHUB_TOKEN); the concurrency group queues it behind this run.
#
# Exit codes of `f1data build`: 0 ok, 1 something failed (keep going, fail the job at the end),
# 75 OpenF1 locked during a live session (skip this poll; a single run ends as "skipped, live session").
set -uo pipefail

: "${YEAR:?}" "${POLL:=false}" "${POLL_BUDGET_S:=19800}" "${POLL_TTL_MINUTES:=10}"
args=("$@")
failed=0
poll=0

manifest_hash() {
  if [ -f site/v1/manifest.json ]; then sha256sum site/v1/manifest.json | cut -d' ' -f1; fi
}

# gh-pages holds a single squashed commit: build an orphan commit from site/ and force-push it,
# but only when manifest.json changed.
publish() {
  local before=$1 after
  after=$(manifest_hash)
  if [ -z "$after" ]; then echo "no manifest written; nothing to publish"; return 0; fi
  if [ "$after" = "$before" ]; then echo "manifest.json unchanged; nothing to publish"; return 0; fi
  touch site/.nojekyll
  local tree commit
  tree=$(GIT_INDEX_FILE="$RUNNER_TEMP/gh-pages.index" sh -c 'git --work-tree=site add -A . && git write-tree') || return 1
  commit=$(git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit-tree "$tree" -m "data: $(date -u +%Y-%m-%dT%H:%M:%SZ) (run $GITHUB_RUN_ID)") || return 1
  git push --force origin "$commit:refs/heads/gh-pages" || return 1
  echo "published $commit"
}

dispatch_successor() {
  echo "poll budget used after $poll polls and polling should continue: dispatching the next run"
  if GH_TOKEN="$GITHUB_TOKEN" gh workflow run build.yml --repo "$GITHUB_REPOSITORY" --ref "$GITHUB_REF_NAME" \
    -f year="$YEAR" -f poll=true; then
    echo "dispatched the next polling run"
  else
    echo "::error::could not dispatch the next run; scheduled runs will start one"
    failed=1
  fi
}

while :; do
  poll=$((poll + 1))
  before=$(manifest_hash)
  if [ "$POLL" = true ]; then
    echo "::group::poll $poll at $(date -u +%H:%M:%SZ)"
    uv run f1data build "${args[@]}" --ttl-minutes "$POLL_TTL_MINUTES"
  else
    uv run f1data build "${args[@]}"
  fi
  code=$?
  case $code in
    0) ;;
    75)
      if [ "$POLL" = true ]; then
        echo "::warning::poll $poll: OpenF1 locked during a live session; skipping this poll"
      else
        echo "::notice::skipped, live session (OpenF1 locked); the next run retries"
      fi
      ;;
    *) echo "::error::build failed (exit $code)"; failed=1 ;;
  esac
  publish "$before" || { echo "::error::publish failed"; failed=1; }
  [ "$POLL" = true ] || break
  echo "::endgroup::"

  read -r keep interval <<<"$(uv run f1data should-poll --year "$YEAR")"
  if [ "$keep" != true ] || ! [[ "$interval" =~ ^[0-9]+$ ]]; then
    echo "polling not needed any more ($keep $interval); stopping after $poll polls"
    break
  fi
  if [ $((SECONDS + interval)) -ge "$POLL_BUDGET_S" ]; then
    dispatch_successor
    break
  fi
  echo "next poll in ${interval}s"
  sleep "$interval"
done
exit $failed
