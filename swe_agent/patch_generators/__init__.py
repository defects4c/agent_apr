# swe_agent/patch_generators/__init__.py
from .base import PatchGenerator, PatchResult
from .agentless import AgentlessPatchGenerator
from .swe_agent import SWEAgentPatchGenerator
from .openhands import OpenHandsPatchGenerator
from .openclaw import OpenClawPatchGenerator
from .claude_code import ClaudeCodePatchGenerator
# Prompting-strategy baselines
from .cot import CoTPatchGenerator
from .reflexion import ReflexionPatchGenerator
from .tot import ToTPatchGenerator
from .self_consistency import SelfConsistencyPatchGenerator
from .got import GoTPatchGenerator
from .standard import StandardPatchGenerator
from .zero_shot_cot import ZeroShotCoTPatchGenerator
from .few_shot_cot import FewShotCoTPatchGenerator
from .react import ReActPatchGenerator
from .pot import PoTPatchGenerator
from .function_calling import FunctionCallingPatchGenerator

__all__ = [
    "PatchGenerator",
    "PatchResult",
    "AgentlessPatchGenerator",
    "SWEAgentPatchGenerator",
    "OpenHandsPatchGenerator",
    "OpenClawPatchGenerator",
    "ClaudeCodePatchGenerator",
    # Prompting-strategy baselines
    "CoTPatchGenerator",
    "ReflexionPatchGenerator",
    "ToTPatchGenerator",
    "SelfConsistencyPatchGenerator",
    "GoTPatchGenerator",
    "StandardPatchGenerator",
    "ZeroShotCoTPatchGenerator",
    "FewShotCoTPatchGenerator",
    "ReActPatchGenerator",
    "PoTPatchGenerator",
    "FunctionCallingPatchGenerator",
]
