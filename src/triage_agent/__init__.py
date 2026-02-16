"""5G TriageAgent - Multi-Agent LangGraph RCA System."""

__version__ = "3.2.0"

from triage_agent.config import get_config
from triage_agent.state import TriageState

__all__ = [
    "get_config",
    "TriageState",
    "__version__",
]
