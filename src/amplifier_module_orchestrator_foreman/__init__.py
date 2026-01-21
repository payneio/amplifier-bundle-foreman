"""
Foreman Orchestrator Module - Conversational autonomous work coordination.

Provides an orchestrator that coordinates multiple specialized worker bundles
through a shared issue queue, enabling parallel execution and background work
with proactive progress reporting.
"""

__amplifier_module_type__ = "orchestrator"

from amplifier_module_orchestrator_foreman.orchestrator import ForemanOrchestrator, mount

__version__ = "1.0.0"

__all__ = ["ForemanOrchestrator", "mount"]
