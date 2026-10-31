# -*- coding: utf-8 -*-
"""WorkflowHooker -- Hooks, die den Arbeitsablauf eines Agenten steuern.

Siehe README.md und ROADMAP.md.

Abschluss-Gate, Drift-/Umfangswächter und SessionStart-Injektoren
sind ausdrücklich aktivierbar und bleiben standardmäßig ausgeschaltet.
"""

from .config import Config, load_config
from .extractor_consumer import ConsumerResult, ExtractorConsumer, ExtractorRunner
from .injectors import (
    GoalInjector,
    LocationInjector,
    LoopInjector,
    PolicyInjector,
    build_goal_message,
    build_loop_briefing,
)
from .protocol import ProjectState, StateSource

__version__ = "0.3.1"

__all__ = [
    "Config",
    "ConsumerResult",
    "ExtractorConsumer",
    "ExtractorRunner",
    "GoalInjector",
    "LocationInjector",
    "LoopInjector",
    "PolicyInjector",
    "ProjectState",
    "StateSource",
    "__version__",
    "build_goal_message",
    "build_loop_briefing",
    "load_config",
]
