"""
Foreman Bundle - Conversational autonomous work orchestration.

Provides an orchestrator that coordinates multiple specialized worker bundles
through a shared issue queue, enabling parallel execution and background work
with proactive progress reporting.
"""

from amplifier_bundle_foreman.orchestrator import ForemanOrchestrator, mount

__version__ = "1.0.0"

__all__ = ["ForemanOrchestrator", "mount"]
