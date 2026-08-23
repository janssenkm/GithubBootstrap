#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd -- "$script_dir/../.." && pwd)
requirements="$repository_root/.github/governance/requirements.txt"
actionlint_version_file="$repository_root/.github/governance/tools/actionlint.version"
actionlint_checksum_file="$repository_root/.github/governance/tools/actionlint.sha256"

task_dir=$(mktemp -d /tmp/github-governance-tests.XXXXXX)
trap 'rm -rf -- "$task_dir"' EXIT

fail() {
  printf 'governance test setup failed: %s\n' "$1" >&2
  exit 5
}

required_python=$(tr -d '\r\n' < "$repository_root/.python-version")
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] ||
  fail "the locked actionlint artifact requires Linux x86_64"
python_command=""
for candidate in "python${required_python%.*}" python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    [[ $("$candidate" -c 'import platform; print(platform.python_version())') == "$required_python" ]]; then
    python_command=$candidate
    break
  fi
done
[[ -n "$python_command" ]] || fail "CPython $required_python is required"
printf 'Verified CPython %s\n' "$required_python"

offline=${GOVERNANCE_OFFLINE:-0}
[[ "$offline" == 0 || "$offline" == 1 ]] || fail "GOVERNANCE_OFFLINE must be 0 or 1"

if [[ "$offline" == 1 ]]; then
  [[ -n ${GOVERNANCE_WHEELHOUSE:-} && -d "$GOVERNANCE_WHEELHOUSE" ]] ||
    fail "offline mode requires an existing GOVERNANCE_WHEELHOUSE directory"
  [[ -n ${GOVERNANCE_TOOL_CACHE:-} && -d "$GOVERNANCE_TOOL_CACHE" ]] ||
    fail "offline mode requires an existing GOVERNANCE_TOOL_CACHE directory"
fi

"$python_command" -m venv "$task_dir/venv"
venv_python="$task_dir/venv/bin/python"

if [[ -n ${GOVERNANCE_WHEELHOUSE:-} ]]; then
  if [[ "$offline" == 0 ]]; then
    mkdir -p -- "$GOVERNANCE_WHEELHOUSE"
    "$venv_python" -m pip download \
      --disable-pip-version-check \
      --require-hashes \
      --dest "$GOVERNANCE_WHEELHOUSE" \
      -r "$requirements"
  fi
  "$venv_python" -m pip install \
    --disable-pip-version-check \
    --no-index \
    --find-links "$GOVERNANCE_WHEELHOUSE" \
    --require-hashes \
    -r "$requirements"
else
  "$venv_python" -m pip install \
    --disable-pip-version-check \
    --require-hashes \
    -r "$requirements"
fi

actionlint_version=$(tr -d '\r\n' < "$actionlint_version_file")
actionlint_archive="actionlint_${actionlint_version}_linux_amd64.tar.gz"
if [[ -n ${GOVERNANCE_TOOL_CACHE:-} ]]; then
  if [[ "$offline" == 0 ]]; then
    mkdir -p -- "$GOVERNANCE_TOOL_CACHE"
    if [[ ! -f "$GOVERNANCE_TOOL_CACHE/$actionlint_archive" ]]; then
      curl --fail --location --silent --show-error \
        "https://github.com/rhysd/actionlint/releases/download/v${actionlint_version}/${actionlint_archive}" \
        --output "$GOVERNANCE_TOOL_CACHE/$actionlint_archive"
    fi
  fi
  actionlint_archive_path="$GOVERNANCE_TOOL_CACHE/$actionlint_archive"
else
  actionlint_archive_path="$task_dir/$actionlint_archive"
  curl --fail --location --silent --show-error \
    "https://github.com/rhysd/actionlint/releases/download/v${actionlint_version}/${actionlint_archive}" \
    --output "$actionlint_archive_path"
fi

[[ -f "$actionlint_archive_path" ]] || fail "missing cached $actionlint_archive"
expected_checksum=$(awk -v artifact="$actionlint_archive" '$2 == artifact { print $1 }' "$actionlint_checksum_file")
[[ "$expected_checksum" =~ ^[0-9a-f]{64}$ ]] || fail "invalid actionlint checksum lock"
actual_checksum=$(sha256sum "$actionlint_archive_path" | awk '{ print $1 }')
[[ "$actual_checksum" == "$expected_checksum" ]] || fail "actionlint checksum mismatch"
printf 'Verified actionlint archive sha256:%s\n' "$actual_checksum"

mkdir -p -- "$task_dir/actionlint"
tar -xzf "$actionlint_archive_path" -C "$task_dir/actionlint" actionlint
actionlint="$task_dir/actionlint/actionlint"
[[ $("$actionlint" -version) == *"$actionlint_version"* ]] || fail "actionlint version mismatch"
printf 'Verified actionlint %s\n' "$actionlint_version"

mapfile -t workflows < <(find "$repository_root/.github/workflows" -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print | sort)
[[ ${#workflows[@]} -gt 0 ]] || fail "no GitHub Actions workflows found"
"$actionlint" "${workflows[@]}"
printf 'Validated %s workflow files with actionlint\n' "${#workflows[@]}"

cd -- "$repository_root"
export PYTHONDONTWRITEBYTECODE=1
case ${1:-} in
  "")
    "$venv_python" -m pytest -p no:cacheprovider tests/governance
    ;;
  --pytest)
    shift
    [[ $# -gt 0 ]] || fail "--pytest requires at least one path"
    "$venv_python" -m pytest -p no:cacheprovider "$@"
    ;;
  --governance)
    shift
    [[ $# -gt 0 ]] || fail "--governance requires a subcommand"
    PYTHONPATH="$repository_root/.github/scripts/governance" "$venv_python" -m github_governance "$@"
    ;;
  *)
    fail "supported interfaces are --pytest and --governance"
    ;;
esac
