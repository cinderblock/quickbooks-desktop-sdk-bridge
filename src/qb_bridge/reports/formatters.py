"""Number and date formatting utilities for reports."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def format_currency(value: str, symbol: str = "$") -> str:
    """Format a numeric string as currency."""
    try:
        d = Decimal(value)
        if d < 0:
            return f"-{symbol}{abs(d):,.2f}"
        return f"{symbol}{d:,.2f}"
    except (InvalidOperation, ValueError):
        return value


def format_percent(value: str) -> str:
    """Format a numeric string as a percentage."""
    try:
        d = Decimal(value)
        return f"{d:.2f}%"
    except (InvalidOperation, ValueError):
        return value


def is_numeric(value: str) -> bool:
    """Check if a string looks numeric."""
    if not value:
        return False
    cleaned = re.sub(r"[,$%\s()-]", "", value)
    try:
        float(cleaned)
        return True
    except ValueError:
        return False
