"""
models.py — backward-compatibility shim
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
All Pydantic models have moved to each agent's own schema.py file:

    agents/clerk/schema.py          →  ExtractedFact, CitedLaw, StandardizedPartySubmission
    agents/verifier/schema.py       →  CitationAuditEntry, VerifierFlag, VerifiedPartySubmission
    agents/registrar/schema.py      →  PartyStance, FramedIssue, AdversarialMatrix
    agents/procedural/schema.py     →  ProceduralIssueFlag, ProceduralAnalysis
    agents/devils_advocate/schema.py → IssueVulnerability, IssueStressTest, StressTestedMatrix
    agents/judge/schema.py          →  ReasonedDecision, DraftCourtOrder
    agents/drafter/schema.py        →  FormalCourtOrder

This file re-exports everything so any code that imports from pageindex.models
continues to work without changes.
"""

from .agents.clerk.schema import (
    ExtractedFact,
    CitedLaw,
    StandardizedPartySubmission,
)
from .agents.verifier.schema import (
    CitationAuditEntry,
    VerifierFlag,
    VerifiedPartySubmission,
)
from .agents.registrar.schema import (
    PartyStance,
    FramedIssue,
    AdversarialMatrix,
)
from .agents.procedural.schema import (
    ProceduralIssueFlag,
    ProceduralAnalysis,
)
from .agents.devils_advocate.schema import (
    IssueVulnerability,
    IssueStressTest,
    StressTestedMatrix,
)
from .agents.judge.schema import (
    ReasonedDecision,
    DraftCourtOrder,
)
from .agents.drafter.schema import (
    FormalCourtOrder,
)
from .agents.citation_auditor.schema import (
    CitationCheckResult,
    CitationAuditReport,
)

__all__ = [
    "ExtractedFact", "CitedLaw", "StandardizedPartySubmission",
    "CitationAuditEntry", "VerifierFlag", "VerifiedPartySubmission",
    "CitationCheckResult", "CitationAuditReport",
    "PartyStance", "FramedIssue", "AdversarialMatrix",
    "ProceduralIssueFlag", "ProceduralAnalysis",
    "IssueVulnerability", "IssueStressTest", "StressTestedMatrix",
    "ReasonedDecision", "DraftCourtOrder",
    "FormalCourtOrder",
]
