"""Every study this codebase knows about, whatever its status.

A study is added here once and never removed: a retired study's definition is
the record of what its data means.
"""

from __future__ import annotations

from . import comparison_2026, example

ALL = (
    comparison_2026.STUDY,
    example.STUDY,
)
