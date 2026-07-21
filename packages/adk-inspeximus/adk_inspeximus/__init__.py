"""Google ADK memory service backed by inspeximus.

Installing under the name ADK users search for; the implementation lives in the main package, so there
is one copy of the code and one place bugs get fixed.

    from adk_inspeximus import InspeximusMemoryService
    runner = Runner(agent=agent, app_name="app", session_service=...,
                    memory_service=InspeximusMemoryService(path="mem.json"))
"""
from inspeximus.integrations.google_adk import InspeximusMemoryService, register

__all__ = ["InspeximusMemoryService", "register"]
