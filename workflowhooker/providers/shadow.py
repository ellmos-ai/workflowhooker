"""Fail-closed provider payload validation for the opt-in shadow spool.

The provider hook itself always stays fail-open: malformed payloads must not
break a Codex or Claude session.  They must, however, fail closed for evidence
collection and therefore never create a lifecycle job.  Only the explicit
``transcript_path`` supplied by the active runtime is accepted; this module
never searches session archives.
"""

from __future__ import annotations

import hashlib
import re
import shlex
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..candidates import LifecycleController, LifecycleResult

SHADOW_PROVIDERS = ("claude", "codex")
NATIVE_SHADOW_EVENTS = ("SessionStart", "PreCompact", "Stop", "SessionEnd")
SHADOW_EVENTS = ("GoalComplete", *NATIVE_SHADOW_EVENTS)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class ProviderPayloadError(ValueError):
    """The runtime payload is not strong enough to create shadow evidence."""


@dataclass(frozen=True)
class ValidatedShadowEvent:
    provider: str
    event: str
    session_ref: str
    source_anchor: str | None
    goal_ref: str | None
    boundary_epoch: str | None
    horizon_hash: str | None
    observed: dict[str, int | bool]


def default_session_root(provider: str) -> Path:
    if provider == "claude":
        return Path.home() / ".claude" / "projects"
    if provider == "codex":
        return Path.home() / ".codex" / "sessions"
    raise ProviderPayloadError("unsupported-provider")


def _payload_text(payload: dict, *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _session_identity(payload: dict) -> tuple[str, str]:
    session_id = _payload_text(payload, "session_id")
    if session_id is None or _SESSION_ID_RE.fullmatch(session_id) is None:
        raise ProviderPayloadError("invalid-session-id")
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return session_id, f"sha256:{digest}"


def _validated_source_anchor(
    *,
    provider: str,
    event: str,
    session_id: str,
    payload: dict,
    session_root: Path | None,
) -> str | None:
    raw_anchor = _payload_text(payload, "transcript_path")
    if raw_anchor is None:
        raise ProviderPayloadError("transcript-path-required")

    candidate = Path(raw_anchor).expanduser()
    if not candidate.is_absolute() or candidate.suffix.casefold() != ".jsonl":
        raise ProviderPayloadError("invalid-transcript-path")

    root_candidate = (session_root or default_session_root(provider)).expanduser()
    try:
        root = root_candidate.resolve(strict=True)
    except OSError as exc:
        raise ProviderPayloadError("session-root-unavailable") from exc
    if not root.is_dir():
        raise ProviderPayloadError("session-root-unavailable")

    # SessionStart may run before the transcript file is materialized.  Its
    # recovery action needs no transcript, so a valid future path is accepted
    # but is not persisted as evidence until the file exists.
    try:
        resolved = candidate.resolve(strict=event != "SessionStart")
    except OSError as exc:
        raise ProviderPayloadError("transcript-unavailable") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProviderPayloadError("transcript-outside-session-root") from exc
    stem = resolved.stem.casefold()
    expected = session_id.casefold()
    session_bound = (
        stem == expected
        if provider == "claude"
        else stem == expected or stem.endswith(f"-{expected}")
    )
    if not session_bound:
        raise ProviderPayloadError("transcript-session-mismatch")
    if event == "SessionStart" and not resolved.is_file():
        return None
    if not resolved.is_file():
        raise ProviderPayloadError("transcript-unavailable")
    return str(resolved)


def validate_shadow_payload(
    *,
    provider: str,
    event: str,
    payload: dict,
    session_root: Path | None = None,
) -> ValidatedShadowEvent:
    if provider not in SHADOW_PROVIDERS:
        raise ProviderPayloadError("unsupported-provider")
    if event not in SHADOW_EVENTS:
        raise ProviderPayloadError("unsupported-event")
    if payload.get("hook_event_name") != event:
        raise ProviderPayloadError("hook-event-mismatch")

    session_id, session_ref = _session_identity(payload)
    source_anchor = _validated_source_anchor(
        provider=provider,
        event=event,
        session_id=session_id,
        payload=payload,
        session_root=session_root,
    )
    observed: dict[str, int | bool] = {
        "has_transcript_path": source_anchor is not None,
        "has_cwd": isinstance(payload.get("cwd"), str),
    }
    for name in ("message_count", "turn_count"):
        value = payload.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            observed[name] = value
    return ValidatedShadowEvent(
        provider=provider,
        event=event,
        session_ref=session_ref,
        source_anchor=source_anchor,
        goal_ref=_payload_text(payload, "goal_id", "goal_ref"),
        boundary_epoch=_payload_text(payload, "boundary_epoch"),
        horizon_hash=_payload_text(
            payload, "horizon_hash", "last_event_hash", "transcript_hash"
        ),
        observed=observed,
    )


def dispatch_shadow_lifecycle(
    controller: LifecycleController,
    *,
    provider: str,
    event: str,
    payload: dict,
    session_root: Path | None = None,
    now: float | None = None,
) -> LifecycleResult:
    validated = validate_shadow_payload(
        provider=provider,
        event=event,
        payload=payload,
        session_root=session_root,
    )
    return controller.handle(
        event=validated.event,
        provider=validated.provider,
        session_ref=validated.session_ref,
        goal_ref=validated.goal_ref,
        boundary_epoch=validated.boundary_epoch,
        horizon_hash=validated.horizon_hash,
        source_anchor=validated.source_anchor,
        observed=validated.observed,
        now=now,
    )


def build_shadow_command(
    provider: str,
    event: str,
    *,
    python_executable: str = "python",
    module: str = "workflowhooker",
    config_path: str | Path | None = None,
    state_dir: str | Path | None = None,
    session_root: str | Path | None = None,
    windows: bool = False,
) -> str:
    if provider not in SHADOW_PROVIDERS or event not in SHADOW_EVENTS:
        raise ValueError("unsupported shadow provider/event")
    argv = [python_executable, "-m", module]
    if config_path is not None:
        argv.extend(("--config", str(config_path)))
    if state_dir is not None:
        argv.extend(("--state-dir", str(state_dir)))
    argv.extend(("provider-shadow", provider, event))
    if session_root is not None:
        argv.extend(("--session-root", str(session_root)))
    if windows:
        command = subprocess.list2cmdline(argv)
        if provider != "codex":
            return command
        # Lazy: only this branch needs the helper.  A module-level import let a
        # hook_master checkout without it crash every `hook-run` (Stop, PreCompact).
        from hook_master.providers.codex import powershell_command

        return powershell_command(command)
    return shlex.join(argv)


def merge_hook_snippets(existing: dict, addition: dict) -> dict:
    """Return a hooks-only merge, deduplicating commands within each matcher.

    Other settings are intentionally not copied, so a diagnostic merge can be
    printed without exposing unrelated environment or credential settings.
    """

    if not isinstance(existing, dict) or not isinstance(addition, dict):
        raise ValueError("hook snippets must be objects")
    existing_hooks = existing.get("hooks", {})
    addition_hooks = addition.get("hooks", {})
    if not isinstance(existing_hooks, dict) or not isinstance(addition_hooks, dict):
        raise ValueError("hooks must be objects")
    merged = {"hooks": deepcopy(existing_hooks)}
    for event, groups in addition_hooks.items():
        if not isinstance(groups, list):
            raise ValueError("hook event groups must be arrays")
        target_groups = merged["hooks"].setdefault(event, [])
        existing_commands = {
            (group.get("matcher"), handler.get("commandWindows") or handler.get("command"))
            for group in target_groups
            if isinstance(group, dict)
            for handler in group.get("hooks", [])
            if isinstance(handler, dict)
        }
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("hook group must be an object")
            handlers = group.get("hooks", [])
            if not isinstance(handlers, list):
                raise ValueError("hook handlers must be an array")
            fresh_handlers = []
            for handler in handlers:
                if not isinstance(handler, dict):
                    raise ValueError("hook handler must be an object")
                command = handler.get("commandWindows") or handler.get("command")
                signature = (group.get("matcher"), command)
                if command and signature in existing_commands:
                    continue
                fresh_handlers.append(deepcopy(handler))
                if command:
                    existing_commands.add(signature)
            if fresh_handlers:
                fresh_group = deepcopy(group)
                fresh_group["hooks"] = fresh_handlers
                target_groups.append(fresh_group)
    return merged
