"""RFC 1918 private IP enforcement."""

from __future__ import annotations

import ipaddress

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
]


def is_private_ip(ip_str: str) -> bool:
    """Return True if *ip_str* belongs to a private / loopback range.

    Dual-stack sockets present IPv4 connections as IPv4-mapped IPv6 addresses
    (e.g. ``::ffff:10.255.0.18``).  We unwrap those before checking so that
    IPv4 private ranges are still recognised correctly.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
        # Unwrap ::ffff:x.x.x.x → plain IPv4 address
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        return any(addr in network for network in PRIVATE_NETWORKS)
    except ValueError:
        return False
