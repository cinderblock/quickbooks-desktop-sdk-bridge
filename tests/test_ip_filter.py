"""Tests for IP filter."""

from qb_bridge.auth.ip_filter import is_private_ip


class TestIsPrivateIP:
    def test_loopback(self):
        assert is_private_ip("127.0.0.1") is True

    def test_10_range(self):
        assert is_private_ip("10.0.0.1") is True
        assert is_private_ip("10.255.0.18") is True
        assert is_private_ip("10.255.255.255") is True

    def test_172_range(self):
        assert is_private_ip("172.16.0.1") is True
        assert is_private_ip("172.31.255.255") is True

    def test_192_range(self):
        assert is_private_ip("192.168.1.1") is True
        assert is_private_ip("192.168.0.100") is True

    def test_public_rejected(self):
        assert is_private_ip("8.8.8.8") is False
        assert is_private_ip("1.1.1.1") is False
        assert is_private_ip("203.0.113.1") is False

    def test_172_outside_range(self):
        assert is_private_ip("172.32.0.1") is False
        assert is_private_ip("172.15.255.255") is False

    def test_ipv6_loopback(self):
        assert is_private_ip("::1") is True

    def test_invalid(self):
        assert is_private_ip("not-an-ip") is False
        assert is_private_ip("") is False
