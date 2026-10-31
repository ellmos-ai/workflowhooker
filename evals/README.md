# Plugin evals

Run with the Claude Code plugin evaluator from the repository root (each case also runs a no-plugin baseline arm and
reports the score delta):

```shell
claude plugin eval . --scaffold --allow-tools Edit Write --model sonnet --runs 2 --max-cost-usd 1 --no-publish --trust-plugin
```

| Case | What it shows | Needs |
|---|---|---|
| `gates-init` | `/workflowhooker:gates init` writes a starter file with the closing gate | `--allow-tools Write` |
| `scope-vendor` | `protected = ["vendor/"]` denies the edit of third-party code that holds the bug; the model reports the denial | `--scaffold`, `--allow-tools Edit Write` |
| `close-dirty-tree` | Starter configuration, no lock file: the first Stop is blocked with the open change as reason, the model names the open state | `--scaffold`, `--allow-tools Edit Write` |

Notes that matter on some machines:

- `--scaffold` runs each case's `fixture.sh` as you; it is needed for `scope-vendor` and `close-dirty-tree`.
- `Edit` and `Write` are gated tools and must be granted with `--allow-tools`, otherwise the model cannot change files.
- **Bash cannot be granted where the evaluator has no sandbox backend** (e.g. Windows without the sandbox feature):
  the run is refused (`sandbox required but unavailable`). The cases therefore avoid Bash. `close-dirty-tree` cannot
  require an actual commit; it measures that the gate's finding reached the model (the final answer names the open
  state or the commit). The mechanism itself is covered deterministically by `tests/test_session_gate.py`.
- Results go to `evals/results/` (ignored by git).

Measured on 2026-10-31 (Windows, `--model sonnet`, 2 runs per arm): `scope-vendor` with 1.00, without 0.00 (delta
+1.00, $0.16); `close-dirty-tree` with 1.00, without 0.50 (delta +0.50, $0.12). The baseline arm fixes the bug in
`vendor/` and finishes without mentioning the open state; with the plugin it does neither.
