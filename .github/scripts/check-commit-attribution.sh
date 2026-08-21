#!/usr/bin/env bash

set -euo pipefail

if (( $# > 1 )); then
  echo "Usage: $0 [<revision-or-range>]" >&2
  exit 2
fi

revision=${1:-HEAD}

if [[ $revision == -* ]]; then
  echo "Revision or range must not start with '-': $revision" >&2
  echo "Usage: $0 [<revision-or-range>]" >&2
  exit 2
fi

if (( $# == 0 )) && ! git rev-parse --verify --quiet HEAD >/dev/null; then
  echo 'No commits to check.'
  exit 0
fi

if ! revision_commits=$(git rev-list "$revision"); then
  echo "Unable to resolve revision or range: $revision" >&2
  exit 2
fi

if [[ -z $revision_commits ]]; then
  echo "No commits to check in: $revision"
  exit 0
fi

shopt -s nocasematch
claude_pattern='(^|[^[:alnum:]])claude([^[:alnum:]]|$)'
failed=0

while IFS= read -r commit; do
  mapfile -t identity < <(git show -s --format='%an%n%ae%n%cn%n%ce' "$commit")
  fields=('author name' 'author email' 'committer name' 'committer email')

  for index in "${!fields[@]}"; do
    value=${identity[$index]}
    if [[ $value =~ $claude_pattern ]]; then
      printf '%s %s: %s\n' "$commit" "${fields[$index]}" "$value" >&2
      failed=1
    fi
  done

  while IFS= read -r trailer; do
    key=${trailer%%:*}
    value=${trailer#*:}
    if [[ $key == 'Co-authored-by' && $value =~ $claude_pattern ]]; then
      printf '%s Co-authored-by: %s\n' "$commit" "${value# }" >&2
      failed=1
    fi
  done < <(git show -s --format='%B' "$commit" | git interpret-trailers --parse)
done <<< "$revision_commits"

if (( failed )); then
  echo 'Commit attribution must identify the responsible human contributor.' >&2
  exit 1
fi

echo "Commit attribution passed for: $revision"
