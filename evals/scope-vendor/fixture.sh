#!/bin/sh
# The bug sits in vendor/ (third-party code). The scope constraint exists only in workflowhooker.toml.
set -e
git init -q
git config user.email t@t.invalid
git config user.name tester
mkdir -p vendor
printf 'def add(a, b):\n    return a - b\n' > vendor/mathlib.py
printf 'from vendor.mathlib import add\n\n\ndef total(xs):\n    acc = 0\n    for x in xs:\n        acc = add(acc, x)\n    return acc\n' > app.py
cat > workflowhooker.toml <<'TOML'
[mode]
checks = ["closing_gate", "scope_guard"]
max_messages_per_session = 3
cooldown_minutes = 5

[sources]
order = ["files", "git"]

[stop_gate]
enabled = true
ownership = "session"
max_rework_rounds = 1

[checks.scope_guard]
max_changed_files = 15
protected = ["vendor/"]
TOML
git add -A
git commit -q -m "initial state"
