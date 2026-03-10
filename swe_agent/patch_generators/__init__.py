# swe_agent/patch_generators/__init__.py
from .base import PatchGenerator, PatchResult
from .agentless import AgentlessPatchGenerator
from .swe_agent import SWEAgentPatchGenerator
from .openhands import OpenHandsPatchGenerator
from .openclaw import OpenClawPatchGenerator
from .claude_code import ClaudeCodePatchGenerator

__all__ = [
    "PatchGenerator",
    "PatchResult",
    "AgentlessPatchGenerator",
    "SWEAgentPatchGenerator",
    "OpenHandsPatchGenerator",
    "OpenClawPatchGenerator",
    "ClaudeCodePatchGenerator",
]
