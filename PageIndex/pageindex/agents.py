"""
agents.py — backward-compatibility shim
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All agent functions have moved to each agent's own agent.py file:

    agents/clerk/agent.py           →  run_clerk()
    agents/verifier/agent.py        →  run_verifier()
    agents/registrar/agent.py       →  run_registrar()
    agents/procedural/agent.py      →  run_procedural_agent()
    agents/devils_advocate/agent.py →  run_devils_advocate()
    agents/judge/agent.py           →  run_judge_on_issue(), run_judge_final_order()
    agents/drafter/agent.py         →  run_drafter()

This file re-exports everything so any code that imports from pageindex.agents
continues to work without changes.
"""

from .agents import (
    run_clerk,
    run_verifier,
    run_registrar,
    run_procedural_agent,
    run_devils_advocate,
    run_judge_on_issue,
    run_judge_final_order,
    run_drafter,
    run_note_builder,
)

__all__ = [
    "run_clerk",
    "run_verifier",
    "run_registrar",
    "run_procedural_agent",
    "run_devils_advocate",
    "run_judge_on_issue",
    "run_judge_final_order",
    "run_drafter",
    "run_note_builder",
]
