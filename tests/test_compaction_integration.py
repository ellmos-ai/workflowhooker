import io
import json
import re
import sys

import pytest
from hook_master.providers.codex import powershell_command

from workflowhooker import cli
from workflowhooker.compaction import COMPACTION_NOTICE_DE
from workflowhooker.providers.codex import CodexProvider
from workflowhooker.providers.shadow import merge_hook_snippets
from workflowhooker.state import SessionState, state_path_for_session

PAYLOAD = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-test"}


def run_hook(tmp_path, monkeypatch, payload, *, enabled=True):
    config = tmp_path / "config.toml"
    config.write_text(
        '[mode]\nchecks = ["closing_gate"]\nmax_messages_per_session = 0\n'
        '[injectors]\npolicy = true\nlocation = true\n'
        f'[compaction]\nenabled = {str(enabled).lower()}\n', encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    return cli.main(["--config", str(config), "--state-dir", str(tmp_path / "state"),
        "--project-dir", str(tmp_path), "hook-run", "SessionStart", "--provider", "codex"])


def assert_notice(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": COMPACTION_NOTICE_DE,
    }}


def test_compact_bypasses_advisory_state(tmp_path, monkeypatch, capsys):
    path = state_path_for_session("compact-test", tmp_path / "state")
    SessionState(messages_sent=999, last_message_ts=9999999999).save(path)
    before = path.read_bytes()
    for _ in range(2):
        assert run_hook(tmp_path, monkeypatch, json.dumps(PAYLOAD)) == 0
        assert_notice(capsys)
        assert path.read_bytes() == before


@pytest.mark.parametrize("enabled", [True, False])
def test_compact_does_not_touch_sources(tmp_path, monkeypatch, capsys, enabled):
    def forbidden(*args, **kwargs):
        pytest.fail("compaction must not read project, advisory state or sources")
    for name in ("canonical_project_root", "state_path_for_session", "state_transaction",
                 "_build_state_source", "_build_session_start_message"):
        monkeypatch.setattr(cli, name, forbidden)
    assert run_hook(tmp_path, monkeypatch, json.dumps(PAYLOAD), enabled=enabled) == 0
    if enabled:
        assert_notice(capsys)
    else:
        assert capsys.readouterr().out == ""


@pytest.mark.parametrize("raw", ["{", "[]", "null", '"text"', "42", ""])
def test_invalid_stdin_silent(tmp_path, monkeypatch, capsys, raw):
    assert run_hook(tmp_path, monkeypatch, raw) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert not (tmp_path / "state").exists()


def test_compaction_snippet_is_narrow_and_idempotent():
    provider = CodexProvider()
    default = provider.hook_snippet()
    snippet = provider.compaction_snippet()
    assert set(snippet["hooks"]) == {"SessionStart"}
    assert len(snippet["hooks"]["SessionStart"]) == 1
    group = snippet["hooks"]["SessionStart"][0]
    assert group["matcher"] == "^compact$"
    assert len(group["hooks"]) == 1
    hook = group["hooks"][0]
    assert hook["timeout"] == 10
    assert hook["commandWindows"] == powershell_command(hook["command"])
    assert hook["command"].endswith("hook-run SessionStart --provider codex")
    assert default == provider.hook_snippet()
    merged = merge_hook_snippets(default, snippet)
    assert merge_hook_snippets(merged, snippet) == merged


def test_install_compaction_cli(tmp_path, capsys):
    assert cli.main(["install-snippet", "--provider", "codex", "--variant", "compaction"]) == 0
    assert json.loads(capsys.readouterr().out) == CodexProvider().compaction_snippet()


def test_compaction_variant_rejects_foreign_provider(capsys):
    assert cli.main(["install-snippet", "--provider", "claude", "--variant", "compaction"]) == 2
    assert capsys.readouterr().out == ""


def test_session_start_and_compaction_snippets_do_not_overlap():
    provider = CodexProvider()
    merged = merge_hook_snippets(provider.session_start_snippet(), provider.compaction_snippet())
    matches = [entry for entry in merged["hooks"]["SessionStart"]
               if re.search(entry.get("matcher", ".*"), "compact")]
    assert len(matches) == 1
    assert matches[0]["matcher"] == "^compact$"
    assert len(merged["hooks"]["SessionStart"]) == 2
    assert merge_hook_snippets(merged, provider.compaction_snippet()) == merged
    assert merge_hook_snippets(merged, provider.session_start_snippet()) == merged


def test_merge_does_not_duplicate_handlers_registered_before_the_exit_code_wrapper():
    """A snippet generated with an older hook-master differs only in commandWindows."""
    provider = CodexProvider()
    current = provider.hook_snippet()
    older = json.loads(json.dumps(current))
    for groups in older["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["commandWindows"] = handler["command"]
    assert older != current
    merged = merge_hook_snippets(older, current)
    assert merged == older
    for groups in merged["hooks"].values():
        assert sum(len(group["hooks"]) for group in groups) == 1


def test_merge_keeps_distinct_handlers_and_windows_only_handlers():
    first = {"hooks": {"Stop": [{"hooks": [{"commandWindows": "a.exe"}]}]}}
    second = {"hooks": {"Stop": [{"hooks": [{"commandWindows": "b.exe"}, {"commandWindows": "a.exe"}]}]}}
    merged = merge_hook_snippets(first, second)
    commands = [h["commandWindows"] for g in merged["hooks"]["Stop"] for h in g["hooks"]]
    assert commands == ["a.exe", "b.exe"]
