"""
shared/llm.py
~~~~~~~~~~~~~
Low-level LLM utilities shared by all pipeline agents.

Public API:
    _chat(model, prompt, ResponseModel, *, system=None, temperature=0, _label="")
    _resolve_model(tier, fallback) -> str
    _agent_temperature(agent_name) -> float
    _truncate_doc(text, model) -> str
    load_file_prompt(path, **variables) -> str
    load_skills_file(path) -> str
    get_metrics() -> dict
    reset_metrics() -> None
"""

from __future__ import annotations

import logging
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

import instructor
import litellm
import yaml

log = logging.getLogger(__name__)

_client = instructor.from_litellm(litellm.completion)


# ---------------------------------------------------------------------------
# Metrics accumulator  (thread-safe)
# ---------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: dict = {
    "total_calls":             0,
    "total_prompt_tokens":     0,
    "total_completion_tokens": 0,
    "total_cost_usd":          0.0,
    "total_latency_ms":        0.0,
    "by_model":                {},   # model → same shape as top-level
}


def get_metrics() -> dict:
    """Return a snapshot of the accumulated LLM metrics."""
    with _metrics_lock:
        import copy
        return copy.deepcopy(_metrics)


def reset_metrics() -> None:
    """Zero all accumulated LLM metrics."""
    with _metrics_lock:
        _metrics["total_calls"]             = 0
        _metrics["total_prompt_tokens"]     = 0
        _metrics["total_completion_tokens"] = 0
        _metrics["total_cost_usd"]          = 0.0
        _metrics["total_latency_ms"]        = 0.0
        _metrics["by_model"].clear()


def _update_metrics(model: str, prompt_tokens: int, completion_tokens: int,
                    cost_usd: float, latency_ms: float) -> None:
    with _metrics_lock:
        _metrics["total_calls"]             += 1
        _metrics["total_prompt_tokens"]     += prompt_tokens
        _metrics["total_completion_tokens"] += completion_tokens
        _metrics["total_cost_usd"]          += cost_usd
        _metrics["total_latency_ms"]        += latency_ms

        bucket = _metrics["by_model"].setdefault(model, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "latency_ms": 0.0,
        })
        bucket["calls"]             += 1
        bucket["prompt_tokens"]     += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["cost_usd"]          += cost_usd
        bucket["latency_ms"]        += latency_ms


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _pipeline_cfg() -> dict:
    """Load and cache the 'pipeline' section of config.yaml."""
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    try:
        with open(cfg_path) as f:
            return yaml.safe_load(f).get("pipeline", {})
    except Exception:
        return {}


@lru_cache(maxsize=1)
def _monitoring_cfg() -> dict:
    """Load and cache the 'monitoring' section of config.yaml."""
    cfg_path = Path(__file__).parent.parent / "config.yaml"
    try:
        with open(cfg_path) as f:
            return yaml.safe_load(f).get("monitoring", {})
    except Exception:
        return {}


def _compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute USD cost using the prefix-matched cost table from config.yaml.

    Returns 0.0 if the model is not in the table (no crash).
    """
    table: dict = _monitoring_cfg().get("cost_per_1m_tokens", {})
    # Longest prefix match
    best_key = ""
    for key in table:
        if model.startswith(key) and len(key) > len(best_key):
            best_key = key
    if not best_key:
        return 0.0
    rates = table[best_key]
    input_rate  = float(rates.get("input",  0))
    output_rate = float(rates.get("output", 0))
    return (prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000


def _resolve_model(tier: str, fallback: str) -> str:
    """Return the model for a tier ('fast'|'balanced'|'powerful'), or fallback."""
    return _pipeline_cfg().get(f"model_{tier}") or fallback


def _agent_temperature(agent: str) -> float:
    """Return the configured temperature for a named agent (default 0)."""
    return float(_pipeline_cfg().get("temperature", {}).get(agent, 0))


def _max_doc_tokens() -> int:
    return int(_pipeline_cfg().get("max_doc_tokens", 80000))


def _truncate_doc(text: str, model: str) -> str:
    """Truncate document text to max_doc_tokens if needed.

    Uses LiteLLM's token counter; falls back to character budget if counting fails.
    """
    if not text:
        return text
    budget = _max_doc_tokens()
    try:
        if litellm.token_counter(model=model, text=text) <= budget:
            return text
    except Exception:
        if len(text) <= budget * 4:
            return text
    char_budget = budget * 4
    log.warning("Document text truncated to ~%d tokens (%d chars) | model=%s", budget, char_budget, model)
    return text[:char_budget] + f"\n\n[... document truncated at {budget} tokens — remaining text omitted ...]"


# ---------------------------------------------------------------------------
# Prompt file loading
# ---------------------------------------------------------------------------

def load_file_prompt(path: Path, **variables) -> str:
    """Load a .md prompt file and substitute {{variable}} placeholders.

    Args:
        path:      Absolute path to the .md file.
        **variables: Key-value pairs matching {{key}} placeholders in the file.

    Returns:
        Rendered prompt string.
    """
    template = path.read_text(encoding="utf-8")
    # Strip Jinja-style comment header  {# ... #}  if present
    template = re.sub(r"^\{#.*?#\}\n", "", template, flags=re.DOTALL).strip()
    for key, value in variables.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def load_skills_file(path: Path) -> str:
    """Load a skills .md file, returning empty string if not found."""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------
# Core LLM call
# ---------------------------------------------------------------------------

def _chat(
    model: str,
    prompt: str,
    response_model,
    *,
    system: str | None = None,
    temperature: float = 0,
    _label: str = "",
):
    """Single-turn structured completion via Instructor.

    Args:
        model:          LiteLLM model string.
        prompt:         User-turn content (task instructions + data).
        response_model: Pydantic model class for structured output validation.
        system:         Optional system message (agent skills/persona).
                        Loaded from each agent's skills.md file.
        temperature:    Sampling temperature. 0 = fully deterministic.
                        Devil's Advocate uses 0.4; Drafter uses 0.2.
        _label:         Human-readable agent name for metrics/logs (e.g. "clerk").
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    t0 = time.perf_counter()
    result, completion = _client.chat.completions.create_with_completion(
        model=model,
        messages=messages,
        response_model=response_model,
        temperature=temperature,
        max_retries=3,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    # Extract token usage from the raw completion object
    usage = getattr(completion, "usage", None)
    prompt_tokens     = int(getattr(usage, "prompt_tokens",     0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost_usd          = _compute_cost(model, prompt_tokens, completion_tokens)

    _update_metrics(model, prompt_tokens, completion_tokens, cost_usd, latency_ms)

    agent_tag = f" agent={_label}" if _label else ""
    log.info(
        "[llm]%s model=%s latency=%.0fms in=%d out=%d cost=$%.5f",
        agent_tag, model, latency_ms, prompt_tokens, completion_tokens, cost_usd,
    )

    return result
