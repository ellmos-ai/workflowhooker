"""Starter-Pfad (0.3.0): Stop-Block ohne Lock per Sitzungs-Basislinie + Scope-Deny.

Alles laeuft ueber die CLI wie im Plugin (hooks/run.sh): echtes git, echte
stdin-JSONs, eigenes State-Verzeichnis.
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from workflowhooker.cli import main

STARTER = """
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
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "vendor").mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.invalid")
    _git(root, "config", "user.name", "t")
    (root / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "vendor" / "lib.py").write_text("X = 1\n", encoding="utf-8")
    (root / "workflowhooker.toml").write_text(STARTER, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _hook(monkeypatch, capsys, repo: Path, tmp_path: Path, event: str, payload: dict):
    payload = {"session_id": "S1", "cwd": str(repo), **payload}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    code = main([
        "--config", str(repo / "workflowhooker.toml"),
        "--state-dir", str(tmp_path / "state"),
        "hook-run", event, "--provider", "claude",
    ])
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else None)


def _edit(repo: Path, rel: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(repo / rel)}}


def test_stop_blocks_once_for_own_dirty_change_without_any_lock(monkeypatch, capsys, repo, tmp_path):
    _hook(monkeypatch, capsys, repo, tmp_path, "UserPromptSubmit", {"prompt": "fix"})
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    code, first = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert code == 0
    assert first["decision"] == "block"
    assert "calc.py" in first["reason"] and "Nacharbeitsrunde" in first["reason"]

    # Claude arbeitet nach, Stop kommt erneut mit stop_hook_active: keine Schleife.
    _, second = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {"stop_hook_active": True})
    assert "decision" not in second
    assert "Verbleibende Befunde" in second["systemMessage"]

    # Auch ohne stop_hook_active ist die Einmalrunde verbraucht.
    _, third = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert "decision" not in third


def test_stop_is_silent_on_clean_tree(monkeypatch, capsys, repo, tmp_path):
    _hook(monkeypatch, capsys, repo, tmp_path, "UserPromptSubmit", {"prompt": "x"})
    assert _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {}) == (0, None)


def test_stop_ignores_changes_that_predate_the_session(monkeypatch, capsys, repo, tmp_path):
    (repo / "calc.py").write_text("# fremde Vorarbeit\n", encoding="utf-8")
    _hook(monkeypatch, capsys, repo, tmp_path, "UserPromptSubmit", {"prompt": "x"})
    _, out = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert "decision" not in (out or {})


def test_stop_without_baseline_never_blocks(monkeypatch, capsys, repo, tmp_path):
    (repo / "calc.py").write_text("changed\n", encoding="utf-8")
    _, out = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert "decision" not in (out or {})


def test_committed_work_is_closed(monkeypatch, capsys, repo, tmp_path):
    _hook(monkeypatch, capsys, repo, tmp_path, "UserPromptSubmit", {"prompt": "x"})
    (repo / "calc.py").write_text("changed\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "work")
    assert _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {}) == (0, None)


def test_first_pretooluse_also_records_baseline(monkeypatch, capsys, repo, tmp_path):
    # Kein UserPromptSubmit: der erste Edit-Aufruf (vor der Aenderung) setzt die Basislinie.
    assert _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", _edit(repo, "calc.py")) == (0, None)
    (repo / "calc.py").write_text("changed\n", encoding="utf-8")
    _, out = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert out["decision"] == "block"


def test_lock_ownership_default_is_unchanged(monkeypatch, capsys, repo, tmp_path):
    (repo / "workflowhooker.toml").write_text(STARTER.replace('ownership = "session"\n', ""), encoding="utf-8")
    _hook(monkeypatch, capsys, repo, tmp_path, "UserPromptSubmit", {"prompt": "x"})
    (repo / "calc.py").write_text("changed\n", encoding="utf-8")
    _, out = _hook(monkeypatch, capsys, repo, tmp_path, "Stop", {})
    assert "decision" not in out  # strenger Altvertrag: ohne eigenen Lock kein Block


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_protected_path_is_denied_with_reason(monkeypatch, capsys, repo, tmp_path, tool):
    payload = {"tool_name": tool, "tool_input": {"file_path": str(repo / "vendor" / "lib.py")}}
    code, out = _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", payload)
    hso = out["hookSpecificOutput"]
    assert code == 0 and hso["permissionDecision"] == "deny"
    assert "vendor/lib.py" in hso["permissionDecisionReason"]


def test_unprotected_path_and_other_tools_pass(monkeypatch, capsys, repo, tmp_path):
    assert _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", _edit(repo, "calc.py")) == (0, None)
    bash = {"tool_name": "Bash", "tool_input": {"command": "echo hi > vendor/lib.py"}}
    assert _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", bash) == (0, None)


def test_allowed_list_denies_everything_else(monkeypatch, capsys, repo, tmp_path):
    cfg = STARTER.replace('protected = ["vendor/"]', 'allowed = ["src/"]')
    (repo / "workflowhooker.toml").write_text(cfg, encoding="utf-8")
    (repo / "src").mkdir()
    _, deny = _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", _edit(repo, "calc.py"))
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", _edit(repo, "src/a.py")) == (0, None)
    outside = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "elsewhere.py")}}
    _, deny2 = _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", outside)
    assert deny2["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_scope_rules_inactive_without_scope_guard_in_checks(monkeypatch, capsys, repo, tmp_path):
    cfg = STARTER.replace('["closing_gate", "scope_guard"]', '["closing_gate"]')
    (repo / "workflowhooker.toml").write_text(cfg, encoding="utf-8")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(repo / "vendor" / "lib.py")}}
    assert _hook(monkeypatch, capsys, repo, tmp_path, "PreToolUse", payload) == (0, None)


def test_rule_values_must_be_lists(repo):
    from workflowhooker.config import load_config

    (repo / "workflowhooker.toml").write_text(
        STARTER.replace('protected = ["vendor/"]', 'protected = "vendor/"'), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_config(repo / "workflowhooker.toml")
