"""Claude-Code-Provider: erzeugt die Hook-KONFIGURATION als Text/JSON.

Wie beim Schwestermodul MemoryHooker schreibt dieses Modul NIEMALS
automatisch in eine echte ``settings.json`` -- Installation bleibt ein
dokumentierter manueller Schritt.

Default-Snippet nutzt ausschliesslich ``Stop``, ``PreCompact`` und
``UserPromptSubmit`` -- niemals ``PreToolUse`` fuer Hinweise. (Das Claude-Code-
Plugin verdrahtet ``PreToolUse`` bewusst selbst, nur fuer die Pfadregeln des
``scope_guard`` und den Aktionsguard -- harte Blocker, keine Hinweise.)

``pretooluse_blocker_snippet()`` existiert als GETRENNTE Methode fuer den
Fall, dass irgendwann ein echter, hart geprueften Blocker gebaut wird
(README: "PreToolUse nur fuer echte Blocker"). ``hook_snippet()`` ruft sie
NICHT auf -- die Default-Installation bleibt PreToolUse-frei.
"""

from __future__ import annotations

from typing import Any

from hook_master.providers.claude import ClaudeProvider as BaseClaudeProvider

FORBIDDEN_DEFAULT_EVENT = "PreToolUse"


class ClaudeProvider(BaseClaudeProvider):
    name = "claude"
    events = ("Stop", "PreCompact", "UserPromptSubmit")

    def hook_snippet(
        self, python_executable: str = "python", module: str = "workflowhooker"
    ) -> dict[str, Any]:
        snippet = super().hook_snippet(
            python_executable=python_executable,
            module=module,
            events=self.events,
            provider_arg=True,
        )
        assert FORBIDDEN_DEFAULT_EVENT not in snippet["hooks"], (
            "Der Default-Snippet darf niemals PreToolUse enthalten (README-Kernregel)."
        )
        return snippet

    def session_start_snippet(
        self, python_executable: str = "python", module: str = "workflowhooker"
    ) -> dict[str, Any]:
        """Explicit opt-in for policy and location injectors."""
        return super().hook_snippet(
            python_executable=python_executable,
            module=module,
            events=("SessionStart",),
            provider_arg=True,
        )

    def pretooluse_blocker_snippet(
        self, python_executable: str = "python", module: str = "workflowhooker"
    ) -> dict[str, Any]:
        """Optionale Blocker-Variante -- bewusst NICHT Teil von
        ``hook_snippet()``. Nur fuer echte, objektiv geprueften Blocker
        gedacht; per Default nicht installiert."""
        return super().pretooluse_blocker_snippet(
            python_executable=python_executable,
            module=module,
            matcher="Edit|Write|MultiEdit|NotebookEdit",
            provider_arg=True,
        )

    def shadow_hook_snippet(
        self,
        python_executable: str = "python",
        module: str = "workflowhooker",
        *,
        config_path=None,
        state_dir=None,
        session_root=None,
    ) -> dict:
        """Generate opt-in S4 wiring without changing real settings.

        ``GoalComplete`` deliberately is not guessed from ``Stop``.  A formal
        goal producer can use :meth:`shadow_goal_command`; native Claude hooks
        provide SessionEnd as the fallback boundary.
        """
        from .shadow import NATIVE_SHADOW_EVENTS, build_shadow_command

        def group(event: str) -> dict:
            return {
                "hooks": [
                    {
                        "type": "command",
                        "command": build_shadow_command(
                            self.name,
                            event,
                            python_executable=python_executable,
                            module=module,
                            config_path=config_path,
                            state_dir=state_dir,
                            session_root=session_root,
                        ),
                        "timeout": 10,
                    }
                ]
            }

        return {"hooks": {event: [group(event)] for event in NATIVE_SHADOW_EVENTS}}


    def shadow_goal_command(self, **kwargs) -> str:
        from .shadow import build_shadow_command

        return build_shadow_command(self.name, "GoalComplete", **kwargs)



__all__ = ["FORBIDDEN_DEFAULT_EVENT", "ClaudeProvider"]
