"""
agents/__init__.py
~~~~~~~~~~~~~~~~~~
Re-exports every pipeline agent function so pipeline.py can do:

    from pageindex.agents import run_clerk, run_registrar, ...

The real implementations live in each agent's subfolder:

    agents/clerk/agent.py             →  run_clerk()
    agents/verifier/agent.py          →  run_verifier()
    agents/citation_auditor/agent.py  →  run_citation_auditor()
    agents/registrar/agent.py         →  run_registrar()
    agents/procedural/agent.py        →  run_procedural_agent()
    agents/devils_advocate/agent.py   →  run_devils_advocate()
    agents/judge/agent.py             →  run_judge_on_issue(), run_judge_final_order()
    agents/drafter/agent.py           →  run_drafter()
"""

from .clerk.agent              import run_clerk
from .verifier.agent           import run_verifier
from .citation_auditor.agent   import run_citation_auditor
from .registrar.agent          import run_registrar
from .procedural.agent         import run_procedural_agent
from .devils_advocate.agent    import run_devils_advocate
from .judge.agent              import run_judge_on_issue, run_judge_final_order
from .drafter.agent            import run_drafter
from .note_builder.agent       import run_note_builder

__all__ = [
    "run_clerk",
    "run_verifier",
    "run_citation_auditor",
    "run_registrar",
    "run_procedural_agent",
    "run_devils_advocate",
    "run_judge_on_issue",
    "run_judge_final_order",
    "run_drafter",
    "run_note_builder",
]
