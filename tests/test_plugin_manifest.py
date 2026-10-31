"""The Claude Code plugin wiring stays consistent with the CLI."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from workflowhooker.cli import _build_parser

ROOT = Path(__file__).resolve().parent.parent


def test_plugin_hook_events_are_known_to_hook_run():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    parser = _build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    allowed = set(next(a for a in sub.choices["hook-run"]._actions if a.dest == "event").choices)
    assert set(hooks) <= allowed


def test_plugin_manifest_uses_documented_keys_only():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= {"name", "description", "version", "author", "license", "homepage", "repository",
                             "keywords", "icon", "documentationUrl", "supportUrl", "privacyPolicyUrl"}
    assert f'version = "{manifest["version"]}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "icon" in manifest:
        assert (ROOT / manifest["icon"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_vendored_hook_master_matches_pyproject_pin():
    pin = re.search(r"hook-master\.git@([0-9a-f]{40})", (ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert (ROOT / "vendor" / "HOOK_MASTER_PIN").read_text(encoding="utf-8").strip() == pin.group(1)
    assert (ROOT / "vendor" / "hook_master" / "__init__.py").is_file()


def test_plugin_hook_runs_without_installed_packages(tmp_path):
    """A plugin is a bare checkout: hook-run must work from the plugin folder alone (python -S = no site-packages)."""
    config = tmp_path / "workflowhooker.toml"
    config.write_text('[mode]\nchecks = ["closing_gate"]\n\n[sources]\norder = ["files", "git"]\n', encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "vendor")]),
               WORKFLOWHOOKER_STATE_DIR=str(tmp_path / "state"), PYTHONIOENCODING="utf-8")
    payload = json.dumps({"hook_event_name": "Stop", "session_id": "plugin-test", "cwd": str(tmp_path)})
    result = subprocess.run([sys.executable, "-S", "-m", "workflowhooker", "--config", str(config),
                             "hook-run", "Stop", "--provider", "claude"],
                            input=payload, capture_output=True, text=True, env=env, cwd=tmp_path, timeout=60)
    assert result.returncode == 0, result.stderr
