#!/bin/sh
# Starter configuration from /workflowhooker:gates init; no lock file, no handmade state.
set -e
git init -q
git config user.email t@t.invalid
git config user.name tester
printf 'def add(a, b):\n    return a - b\n' > calc.py
cat > workflowhooker.toml <<'TOML'
[mode]
checks = ["closing_gate"]
max_messages_per_session = 3
cooldown_minutes = 5

[sources]
order = ["files", "git"]

[stop_gate]
enabled = true
ownership = "session"
max_rework_rounds = 1

[checks.drift_warning]
max_touched_dirs = 4

[checks.scope_guard]
max_changed_files = 15
TOML
git add -A
git commit -q -m "initial state"
