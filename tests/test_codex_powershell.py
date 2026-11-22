"""Codex consumers inherit the shared PowerShell invocation fix."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hook_master.providers.codex import powershell_command

from workflowhooker.providers.codex import CodexProvider
from workflowhooker.providers.shadow import build_shadow_command


@pytest.mark.parametrize(
    "variant",
    [
        "hook_snippet",
        "session_start_snippet",
        "compaction_snippet",
        "pretooluse_blocker_snippet",
    ],
)
def test_codex_variants_invoke_quoted_python(variant):
    snippet = getattr(CodexProvider(), variant)(python_executable=f'"{sys.executable}"')
    for groups in snippet["hooks"].values():
        handler = groups[0]["hooks"][0]
        assert handler["commandWindows"] == powershell_command(handler["command"])
        assert f'& "{sys.executable}" ' in handler["commandWindows"]
        assert "exit $LASTEXITCODE" in handler["commandWindows"]


def test_codex_shadow_quotes_python_as_powershell_invocation():
    for groups in (
        CodexProvider()
        .shadow_hook_snippet(python_executable="C:/Python Directory/python.exe")[
            "hooks"
        ]
        .values()
    ):
        handler = groups[0]["hooks"][0]
        assert '& "C:/Python Directory/python.exe" ' in handler["commandWindows"]
        assert "exit $LASTEXITCODE" in handler["commandWindows"]
    claude = build_shadow_command(
        "claude",
        "SessionStart",
        python_executable="C:/Python Directory/python.exe",
        windows=True,
    )
    assert not claude.startswith("& ")


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_codex_shadow_command_runs_in_powershell(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is not available")
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys,json; "
        "print(json.dumps({'argv':sys.argv[1:],"
        "'payload':json.load(sys.stdin)}))",
        encoding="utf-8",
    )
    # A real interpreter path with spaces exercises list2cmdline's quoted executable.
    python = str(
        Path(sys.base_prefix)
        / ("python.exe" if sys.platform == "win32" else "bin/python")
    )
    if not Path(python).is_file():
        python = sys.executable
    command = build_shadow_command(
        "codex",
        "SessionStart",
        python_executable=python,
        module="probe",
        config_path="config directory/test.toml",
        windows=True,
    )
    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=tmp_path,
        input='{"source":"compact"}',
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)
    assert output["payload"] == {"source": "compact"}
    assert output["argv"] == [
        "--config",
        "config directory/test.toml",
        "provider-shadow",
        "codex",
        "SessionStart",
    ]
