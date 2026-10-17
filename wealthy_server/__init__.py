"""Wealthy web-server domain foundation.

The package is deliberately separate from :mod:`asset_assistant`: the existing
CLI and Tk application remain usable while the authenticated web service is
introduced.  Financial policy calculations continue to live in
``asset_assistant`` and are not reimplemented here.
"""

from .database import Database
from .strategy_targets import TargetStrategyKind, TargetStrategyMix

__all__ = ["Database", "TargetStrategyKind", "TargetStrategyMix"]
