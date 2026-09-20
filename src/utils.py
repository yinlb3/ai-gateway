"""
Shared utility helpers for the ai-gateway scripts.

Only formatting helpers live here: no file writes, no environment
changes, so importing this module is always safe.
"""

# -*- coding: utf-8 -*-
# ==========================================================================
# Founded: 2026-09-21
# Modified: 2026-09-21
# @author yinlb, deepseek-flash
# ==========================================================================


def format_elapsed(seconds: float) -> str:
    """
    Format an elapsed time in seconds as a readable string.

    Args:
        seconds (float): Elapsed time in seconds.

    Returns:
        str: Readable text such as 1.23s, 2m 3s or 1h 2m 3s.
    """
    if seconds < 60:
        return f'{seconds:.2f}s'

    # 1. Split into whole seconds, then into minutes and hours.
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)

    # 2. Omit the hour part when it is zero.
    if hours:
        return f'{hours}h {minutes}m {secs}s'
    return f'{minutes}m {secs}s'
