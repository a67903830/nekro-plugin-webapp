"""Validator Service

Provides validation logic for agent outputs, ensuring they meet the contracts (Schema/Interface).
"""

import json
import re
from typing import Dict, Optional, Tuple

from .logger import logger


class Validator:
    @staticmethod
    def validate_json(content: str, _schema: Optional[Dict] = None) -> Tuple[bool, str]:
        """Validate JSON content.

        Args:
            content: The raw JSON string.
            schema: Optional JSON Schema dict.

        Returns:
            (valid, error_message)
        """
        try:
            # 1. Syntax Check
            data = json.loads(content)
            assert data is not None

            # 2. Schema Check (if strictly required)
            # For now, we only ensure valid JSON.
            # Integrating a full 'jsonschema' lib might require extra dependencies.
            # We can do simple structural checks if needed.

        except json.JSONDecodeError as e:
            error_msg = f"JSON Syntax Error:line {e.lineno} column {e.colno}: {e.msg}"
            logger.warning(f"[Validator] ❌ {error_msg}")
            return False, error_msg
        except Exception as e:
            return False, str(e)
        else:
            return True, "Valid JSON"

    @staticmethod
    def validate_typescript(content: str) -> Tuple[bool, str]:
        """Validate TypeScript content (Basic Syntax)."""
        forbidden_patterns = [
            ("```", "Markdown code blocks found in file content"),
            ("<script>", "HTML script tags found in TS/TSX file"),
        ]

        for pat, msg in forbidden_patterns:
            if pat in content:
                return False, msg

        return True, "Basic syntax checks passed"


validator = Validator()
