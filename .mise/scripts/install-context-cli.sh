#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
tool_args=(--editable "$task_root" --force)
if [[ -x "$task_root/.venv/bin/python" ]]; then
  tool_args+=(--python "$task_root/.venv/bin/python")
fi
uv tool install "${tool_args[@]}"

# A retired mise installation can leave a shim ahead of ~/.local/bin. Remove
# only that inactive generated shim, so the installed tool is usable in a new
# shell as well as by the shared hook hub.
task_shim="${MISE_DATA_DIR:-$HOME/.local/share/mise}/shims/candystore"
if [[ -L "$task_shim" ]] && command -v mise >/dev/null; then
  task_target="$(readlink -f -- "$task_shim")"
  task_mise="$(readlink -f -- "$(command -v mise)")"
  if [[ "$task_target" == "$task_mise" ]] && ! mise which candystore >/dev/null 2>&1; then
    rm -- "$task_shim"
  fi
fi
"${UV_TOOL_BIN_DIR:-$HOME/.local/bin}/candystore" --help
