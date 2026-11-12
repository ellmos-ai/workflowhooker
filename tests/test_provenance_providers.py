
import pytest
from hook_master.providers.codex import powershell_command

from workflowhooker.providers.claude import ClaudeProvider
from workflowhooker.providers.codex import CodexProvider


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


def test_codex_provider_emits_verified_codex_hook_shape():
    provider = CodexProvider()
    assert provider.is_available() is True
    snippet = provider.hook_snippet()
    assert set(snippet["hooks"]) == {"Stop", "PreCompact", "UserPromptSubmit"}
    assert "PreToolUse" not in snippet["hooks"]
    command = snippet["hooks"]["Stop"][0]["hooks"][0]
    assert command["commandWindows"] == powershell_command(command["command"])
    assert command["timeout"] == 10


@pytest.mark.parametrize("provider", [ClaudeProvider(), CodexProvider()])
def test_session_start_injectors_require_explicit_snippet(provider):
    assert "SessionStart" not in provider.hook_snippet()["hooks"]
    snippet = provider.session_start_snippet()
    assert set(snippet["hooks"]) == {"SessionStart"}
    handler = snippet["hooks"]["SessionStart"][0]["hooks"][0]
    assert "hook-run SessionStart" in handler["command"]
    assert f"--provider {provider.name}" in handler["command"]
