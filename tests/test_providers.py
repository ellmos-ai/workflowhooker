from pathlib import Path

import pytest
from hook_master.providers.codex import powershell_command

from workflowhooker.config import ProvidersConfig
from workflowhooker.providers import PROVIDER_REGISTRY, resolve_provider
from workflowhooker.providers.agy import AgyProvider
from workflowhooker.providers.claude import ClaudeProvider
from workflowhooker.providers.codex import CodexProvider
from workflowhooker.providers.git import GitProvider
from workflowhooker.providers.kimi import KimiProvider
from workflowhooker.providers.manual import ManualProvider


def test_default_hook_snippet_never_contains_pretooluse():
    snippet = ClaudeProvider().hook_snippet()
    assert "PreToolUse" not in snippet["hooks"]
    assert set(snippet["hooks"]) == {"Stop", "PreCompact", "UserPromptSubmit"}


def test_pretooluse_blocker_variant_exists_separately_and_is_not_default():
    provider = ClaudeProvider()
    default_snippet = provider.hook_snippet()
    blocker_snippet = provider.pretooluse_blocker_snippet()

    assert "PreToolUse" not in default_snippet["hooks"]
    assert "PreToolUse" in blocker_snippet["hooks"]
    assert (
        blocker_snippet["hooks"]["PreToolUse"][0]["matcher"]
        == "Edit|Write|MultiEdit|NotebookEdit"
    )


def test_codex_provider_emits_verified_codex_hook_shape():
    provider = CodexProvider()
    assert provider.is_available() is True
    snippet = provider.hook_snippet()
    assert set(snippet["hooks"]) == {"Stop", "PreCompact", "UserPromptSubmit"}
    assert "PreToolUse" not in snippet["hooks"]
    command = snippet["hooks"]["Stop"][0]["hooks"][0]
    assert command["commandWindows"] == powershell_command(command["command"])
    assert command["timeout"] == 10


def test_codex_action_guard_variant_only_matches_apply_patch():
    snippet = CodexProvider().pretooluse_blocker_snippet()
    group = snippet["hooks"]["PreToolUse"][0]
    assert group["matcher"] == "^apply_patch$"
    assert "--provider codex" in group["hooks"][0]["command"]


def test_git_remains_stub():
    assert GitProvider().is_available() is False


def test_manual_always_available():
    assert ManualProvider().is_available() is True


def test_resolve_provider_picks_codex():
    config = ProvidersConfig(order=["codex", "git", "manual"])
    assert resolve_provider(config).name == "codex"


def test_resolve_provider_picks_claude_first():
    config = ProvidersConfig(order=["claude", "codex", "git", "manual"])
    assert resolve_provider(config).name == "claude"


def test_registry_has_all_six():
    assert set(PROVIDER_REGISTRY) == {"claude", "codex", "kimi", "agy", "git", "manual"}


def test_kimi_provider_emits_stop_and_userpromptsubmit_in_plain_format():
    """Kimi-Vertrag (Probe 2026-07-28, CLI 0.29.2): Stop- und UserPromptSubmit-
    stdout werden eingespeist; PreCompact ist Beobachtungs-Event und wird
    absichtlich NICHT registriert (stille Falle)."""
    snippet = KimiProvider().hook_snippet()
    events = {h["event"] for h in snippet["hooks"]}
    assert events == {"Stop", "UserPromptSubmit"}
    assert "PreToolUse" not in events
    assert "PreCompact" not in events
    commands = {h["event"]: h["command"] for h in snippet["hooks"]}
    assert "--format plain" in commands["UserPromptSubmit"]
    assert "--block" in commands["Stop"]


def test_kimi_action_guard_variant_only_matches_explicit_path_tools():
    snippet = KimiProvider().pretooluse_blocker_snippet()
    assert len(snippet["hooks"]) == 1
    assert snippet["hooks"][0]["event"] == "PreToolUse"
    assert "WriteFile" in snippet["hooks"][0]["matcher"]
    assert "Bash" not in snippet["hooks"][0]["matcher"]


def test_kimi_provider_availability_mirrors_config_existence():
    expected = (Path.home() / ".kimi-code" / "config.toml").exists()
    assert KimiProvider().is_available() is expected


def test_resolve_provider_picks_kimi_when_ordered_and_available():
    if not KimiProvider().is_available():
        pytest.skip("keine ~/.kimi-code/config.toml auf diesem Host")
    config = ProvidersConfig(order=["kimi", "manual"])
    assert resolve_provider(config).name == "kimi"


def test_agy_provider_never_emits_pretooluse():
    """agy-Vertrag (analog memoryhooker/providers/agy.py, [G 2026-07-25]):
    nur ``PreInvocation`` -> ``UserPromptSubmit`` ist verdrahtet.
    ``PostToolUse`` bleibt bewusst unverdrahtet -- WorkflowHooker hat keinen
    Pro-Tool-Aufruf-Befehl, den es fuettern koennte (anders als MemoryHookers
    record-search-Zaehler)."""
    snippet = AgyProvider().hook_snippet()
    assert set(snippet["hooks"]) == {"PreInvocation"}
    assert "PreToolUse" not in snippet["hooks"]
    assert "PostToolUse" not in snippet["hooks"]
    command = snippet["hooks"]["PreInvocation"][0]["command"]
    assert command.endswith("hook-run UserPromptSubmit")


def test_agy_provider_always_available():
    assert AgyProvider().is_available() is True


def test_resolve_provider_picks_agy_when_ordered():
    config = ProvidersConfig(order=["agy", "manual"])
    assert resolve_provider(config).name == "agy"


def test_provider_rejects_zero_byte_store_alias(tmp_path):
    from workflowhooker.providers.invariants import validate_interpreter

    # Synthetischer 0-Byte-Alias-Kandidat statt "python3": "python3" ist auf
    # POSIX ein legitimer, realer Interpreter (T-20260921-750493182, Runde 3;
    # siehe hook-master#5) -- validate_interpreter() wirft dort korrekt KEINE
    # Exception dafuer. Der 0-Byte-Groessencheck ist dagegen auf jeder
    # Plattform identisch und damit die richtige, plattformunabhaengige Sonde.
    fake_alias = tmp_path / "mock_alias.exe"
    fake_alias.write_bytes(b"")
    with pytest.raises(ValueError, match=r"0-Byte|0-byte|Store|Alias"):
        validate_interpreter(fake_alias)


def test_provider_timeout_must_be_positive():
    from workflowhooker.providers.invariants import validate_timeout

    with pytest.raises(ValueError, match=r"positiv|> 0"):
        validate_timeout(0)
    with pytest.raises(ValueError, match=r"positiv|> 0"):
        validate_timeout(-5)
    assert validate_timeout(10) == 10


def test_provider_self_test_invariants():
    from workflowhooker.providers.invariants import run_self_test

    res = run_self_test()
    assert res["interpreter_valid"] is True
    assert res["timeout_kills"] is True
    assert res["alias_detection_works"] is True
    assert res["ok"] is True


def test_providers_inherit_from_hook_master_bases():
    from hook_master.providers.agy import AgyProvider as BaseAgy
    from hook_master.providers.claude import ClaudeProvider as BaseClaude
    from hook_master.providers.codex import CodexProvider as BaseCodex
    from hook_master.providers.git import GitProvider as BaseGit
    from hook_master.providers.kimi import KimiProvider as BaseKimi
    from hook_master.providers.manual import ManualProvider as BaseManual

    assert issubclass(ClaudeProvider, BaseClaude)
    assert issubclass(CodexProvider, BaseCodex)
    assert issubclass(AgyProvider, BaseAgy)
    assert issubclass(KimiProvider, BaseKimi)
    assert issubclass(GitProvider, BaseGit)
    assert issubclass(ManualProvider, BaseManual)


def test_hook_snippets_delegate_to_hook_master_bases():
    from hook_master.providers.claude import ClaudeProvider as BaseClaude
    from hook_master.providers.codex import CodexProvider as BaseCodex

    claude = ClaudeProvider()
    base_claude = BaseClaude()
    expected_claude = base_claude.hook_snippet(
        module="workflowhooker", events=claude.events, provider_arg=True
    )
    assert claude.hook_snippet() == expected_claude
    expected_blocker = base_claude.pretooluse_blocker_snippet(
        module="workflowhooker", provider_arg=True
    )
    assert claude.pretooluse_blocker_snippet() == expected_blocker

    codex = CodexProvider()
    base_codex = BaseCodex()
    expected_codex = base_codex.hook_snippet(
        module="workflowhooker",
        events=codex.events,
        timeout=10,
        status_prefix="WorkflowHooker",
        provider_arg=True,
    )
    assert codex.hook_snippet() == expected_codex
    expected_codex_blocker = base_codex.pretooluse_blocker_snippet(
        module="workflowhooker",
        matcher="^apply_patch$",
        timeout=10,
        status_message="WorkflowHooker: action guard",
        provider_arg=True,
    )
    assert codex.pretooluse_blocker_snippet() == expected_codex_blocker


def test_invariants_imported_from_hook_master():
    import hook_master.providers.invariants as hm_invariants

    import workflowhooker.providers.invariants as wfl_invariants

    assert wfl_invariants.validate_interpreter is hm_invariants.validate_interpreter
    assert wfl_invariants.validate_timeout is hm_invariants.validate_timeout
    assert wfl_invariants.run_self_test is hm_invariants.run_self_test


