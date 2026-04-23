"""Tests for the singleton registry."""

from caos._registry import get, put, reset, reset_all


class TestRegistry:
    """Test centralized singleton registry."""

    def test_put_and_get(self):
        """put/get round-trips."""
        put("test_key", 42)
        assert get("test_key") == 42

    def test_get_missing_returns_none(self):
        """get returns None for unknown keys."""
        assert get("nonexistent") is None

    def test_reset_single(self):
        """reset removes a single key."""
        put("a", 1)
        put("b", 2)
        reset("a")
        assert get("a") is None
        assert get("b") == 2

    def test_reset_all(self):
        """reset_all clears everything."""
        put("x", 10)
        put("y", 20)
        reset_all()
        assert get("x") is None
        assert get("y") is None
