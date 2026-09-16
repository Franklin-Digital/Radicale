#!/usr/bin/env bash
# Render the runtime Radicale config from the committed template.
#
# WHY THIS EXISTS: Radicale does NOT expand environment variables inside its
# config file. The DB password therefore has to be substituted at deploy time,
# and it must never be committed -- so the repo holds a template with an empty
# franklin_pg_password and this writes the real one to a gitignored file.
set -euo pipefail

SECRETS=/home/Franklin/secrets/franklin.env
SRC="$(dirname "$0")/../config/config"
OUT="$(dirname "$0")/config/config"

# franklin.env has no `export` lines -- without `set -a` every var reads empty,
# which is a documented trap on this host. An empty password would then be
# rendered silently and every login would fail closed.
set -a
# shellcheck disable=SC1090
. "$SECRETS"
set +a

: "${BENNY_PASSWORD:?BENNY_PASSWORD is empty - refusing to render a config that cannot authenticate}"

mkdir -p "$(dirname "$OUT")"
# Rights file is used as-is.
cp "$(dirname "$0")/../config/rights" "$(dirname "$OUT")/rights"

# Only the one line changes. Using a here-doc-free python call avoids quoting
# the password through a shell or sed, which would break on / & or a newline.
BENNY_PASSWORD="$BENNY_PASSWORD" SRC="$SRC" OUT="$OUT" python3 - <<'PY'
import os, pathlib
src = pathlib.Path(os.environ["SRC"]).read_text()
pw = os.environ["BENNY_PASSWORD"]
needle = "franklin_pg_password ="
lines = src.splitlines(keepends=True)
hits = [i for i, l in enumerate(lines) if l.startswith(needle)]
assert len(hits) == 1, f"expected exactly one {needle!r} line, found {len(hits)}"
lines[hits[0]] = f"franklin_pg_password = {pw}\n"
out = pathlib.Path(os.environ["OUT"])
out.write_text("".join(lines))
out.chmod(0o600)          # contains a live DB password
print(f"rendered {out} (mode 600)")
PY

echo "rights  -> $(dirname "$OUT")/rights"
