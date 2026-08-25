"""Hermione: a bag you drop everything into, that sorts itself out.

    The Ledger is truth.
    The Clerk is derived from the Ledger.
    The Cortex is derived from the Ledger and the query log.
    Delete either index at any time. Nothing is lost but time.
"""
from .config import Config  # noqa: F401
from .runtime import Bag  # noqa: F401

__version__ = "0.1.0"
__all__ = ["Bag", "Config", "__version__"]
