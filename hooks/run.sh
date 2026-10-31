#!/bin/sh
# Runs `python -m workflowhooker hook-run <event>` from the plugin folder with a real Python 3.10+.
# Silent unless the project has a workflowhooker.toml (the module is silent without configuration by design).
event="$1"
project="${CLAUDE_PROJECT_DIR:-$PWD}"
config="$project/workflowhooker.toml"
[ -f "$config" ] || exit 0
# vendor/ carries hook_master at the commit pinned in pyproject.toml (tools/vendor_hook_master.py).
# Windows Python splits PYTHONPATH on ';', POSIX Python on ':' -- a drive-letter plugin root means Windows.
case "$CLAUDE_PLUGIN_ROOT" in [A-Za-z]:*) sep=';' ;; *) sep=':' ;; esac
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}${sep}${CLAUDE_PLUGIN_ROOT}/vendor${PYTHONPATH:+$sep$PYTHONPATH}"
run() { exec "$@" -m workflowhooker --config "$config" hook-run "$event" --provider claude; }
if [ -n "$ELLMOS_PYTHON" ]; then run "$ELLMOS_PYTHON"; fi
if command -v py >/dev/null 2>&1 && py -3 -c "import sys" >/dev/null 2>&1; then run py -3; fi
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
    run "$candidate"
  fi
done
echo "workflowhooker: no Python 3.10+ found (set ELLMOS_PYTHON); gates are inactive" >&2
exit 0
