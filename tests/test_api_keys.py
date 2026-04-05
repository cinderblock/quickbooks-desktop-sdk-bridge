"""Tests for API key generation and hashing."""

from qb_bridge.auth.api_keys import PREFIX, generate_api_key, hash_key


class TestGenerateApiKey:
    def test_format(self):
        key, key_hash = generate_api_key()
        assert key.startswith(PREFIX)
        assert len(key) == len(PREFIX) + 64  # 4 + 64 hex chars
        assert len(key_hash) == 64  # SHA-256 hex digest

    def test_unique(self):
        key1, _ = generate_api_key()
        key2, _ = generate_api_key()
        assert key1 != key2

    def test_hash_matches(self):
        key, expected_hash = generate_api_key()
        assert hash_key(key) == expected_hash

    def test_hash_different_for_different_keys(self):
        key1, hash1 = generate_api_key()
        key2, hash2 = generate_api_key()
        assert hash1 != hash2
