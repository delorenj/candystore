#!/usr/bin/env bash
# Apply rules.txt: archive-then-delete each event type past its retention.
set -euo pipefail
desk="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$desk/../.." && pwd)"
uv="${UV:-$(command -v uv || echo /home/delorenj/.local/share/mise/installs/uv/latest/.mise-bins/uv)}"
cd "$repo"
status=0
while read -r type days _; do
  [[ -z "${type:-}" || "$type" == \#* ]] && continue
  echo "== $type older than ${days}d"
  "$uv" run --frozen candystore prune --type "$type" --older-than "$days" --batch 2000 || status=1
done < "$desk/rules.txt"
exit "$status"
