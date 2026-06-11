"""Tests for permission parsing — especially the fail-closed behavior on
corrupt stored permissions (must never silently grant a permissive default)."""

from qb_bridge.auth.permissions import DEFAULT_PERMISSIONS, parse_permissions


class TestParsePermissions:
    def test_none_yields_readonly_default(self):
        """An unset value means a brand-new key → read-only default."""
        assert parse_permissions(None) == DEFAULT_PERMISSIONS

    def test_empty_string_yields_default(self):
        assert parse_permissions("") == DEFAULT_PERMISSIONS

    def test_valid_json_parsed(self):
        assert parse_permissions('{"Customer": ["read", "write"]}') == {
            "Customer": ["read", "write"]
        }

    def test_corrupt_json_denies_all(self):
        """Unparseable stored permissions must deny everything, not fall back
        to a permissive default that would hide the corruption."""
        assert parse_permissions("{not valid json") == {}

    def test_non_object_json_denies_all(self):
        """Valid JSON that isn't an object (e.g. a list or null) must deny."""
        assert parse_permissions("[]") == {}
        assert parse_permissions("null") == {}
        assert parse_permissions('"admin"') == {}

    def test_default_is_not_mutated(self):
        """The returned default must be a copy — mutating it must not poison
        the shared constant for later keys."""
        result = parse_permissions(None)
        result["*"] = ["admin"]
        assert DEFAULT_PERMISSIONS == {"*": ["read"]}
