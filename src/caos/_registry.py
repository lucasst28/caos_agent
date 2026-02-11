"""Singleton registry for CAOS Agent.

Centralizes all singleton instances so tests can reset state cleanly
via `reset_all()` instead of reaching into private globals.
"""

from typing import Any

# All singletons keyed by name
_singletons: dict[str, Any] = {}


def get(name: str) -> Any:
    """Get a singleton by name (returns None if not set)."""
    return _singletons.get(name)


def put(name: str, instance: Any) -> None:
    """Store a singleton."""
    _singletons[name] = instance


def reset(name: str) -> None:
    """Reset a single singleton."""
    _singletons.pop(name, None)


def reset_all() -> None:
    """Reset all singletons. Call in test fixtures to avoid state leaks."""
    _singletons.clear()
