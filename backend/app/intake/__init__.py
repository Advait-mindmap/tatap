"""Brief intake: free text / documents -> structured Brief with citations and questions.

PRODUCT_SPEC.md section 3.1 and SIMULATION_AND_REASONING.md section 6. Anything the brief does
not state becomes a question, never an assumed value.
"""

from __future__ import annotations

from backend.app.intake.extractor import (
    REQUIRED_FIELDS,
    build_result,
    conf_threshold,
    extract_brief,
    normalise_mode,
    normalise_tier,
    quote_is_grounded,
)
from backend.app.intake.prompt import (
    DELIVERY_MODES,
    PROMPT_VERSION,
    TARGET_DISCIPLINES,
    TARGET_FIELDS,
    build_user_prompt,
    extraction_schema,
)

__all__ = [
    'DELIVERY_MODES',
    'PROMPT_VERSION',
    'REQUIRED_FIELDS',
    'TARGET_DISCIPLINES',
    'TARGET_FIELDS',
    'build_result',
    'build_user_prompt',
    'conf_threshold',
    'extract_brief',
    'extraction_schema',
    'normalise_mode',
    'normalise_tier',
    'quote_is_grounded',
]
