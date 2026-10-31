---
description: Show or set up the workflowhooker gates for this project (status, init)
argument-hint: "[status|init]"
---

workflowhooker is silent until the project contains `workflowhooker.toml`.

- **`status`** (default): if `workflowhooker.toml` exists in the project root, run
  `py -3 -m workflowhooker --config workflowhooker.toml check` with `PYTHONPATH` set to `${CLAUDE_PLUGIN_ROOT}` and
  `${CLAUDE_PLUGIN_ROOT}/vendor` (joined with `;` on Windows, `:` elsewhere; `python3` instead of `py -3` outside
  Windows) and report the active checks and their findings. Otherwise say that
  the gates are off and offer `init`.
- **`init`**: create `workflowhooker.toml` in the project root with this starter content, then show it and explain
  each block in one line. Do not overwrite an existing file.

```toml
[mode]
checks = ["closing_gate"]
max_messages_per_session = 3
cooldown_minutes = 5

[sources]
order = ["files", "git"]

[stop_gate]
enabled = true
max_rework_rounds = 1

[checks.drift_warning]
max_touched_dirs = 4

[checks.scope_guard]
max_changed_files = 15
```

Add `"drift_warning"` or `"scope_guard"` to `checks` when the user wants those warnings too.
