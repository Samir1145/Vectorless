"""
prompt_loader.py
~~~~~~~~~~~~~~~~
Loads prompt templates from pageindex/prompts/*.md and substitutes
{{variable}} placeholders with provided values.

Usage:
    from .prompt_loader import load_prompt

    prompt = load_prompt("generate_toc_init", part=text)
    prompt = load_prompt("generate_toc_continue", part=text, toc_content=json.dumps(toc))
"""

import re
from pathlib import Path
from functools import lru_cache

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _read_template(name: str) -> str:
    """Read and cache a prompt template from disk."""
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    text = path.read_text(encoding="utf-8")
    # Strip Jinja-style comment header  {# ... #}  (first line metadata)
    text = re.sub(r"^\{#.*?#\}\n", "", text, flags=re.DOTALL)
    return text.strip()


def load_prompt(name: str, **variables) -> str:
    """
    Load a prompt template by name and substitute {{variable}} placeholders.

    Args:
        name:      Filename without extension (e.g. "generate_toc_init")
        **variables: Key-value pairs matching {{key}} placeholders in the template

    Returns:
        The rendered prompt string.

    Raises:
        FileNotFoundError: If the .md file does not exist.
        ValueError: If any {{placeholder}} in the template has no matching variable.
    """
    template = _read_template(name)

    # Find all placeholders in the template
    placeholders = set(re.findall(r"\{\{(\w+)\}\}", template))
    missing = placeholders - set(variables.keys())
    if missing:
        raise ValueError(
            f"Prompt '{name}' requires variables {missing} but they were not provided."
        )

    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))

    return result


def reload_prompts():
    """Clear the template cache so edited .md files are picked up without restart."""
    _read_template.cache_clear()
