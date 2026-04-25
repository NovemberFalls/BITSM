"""Shared SSRF protection utility.

Any service or route that makes outbound HTTP requests to user-supplied URLs
must call _validate_url() first.  This module is the single source of truth
for that check so the logic cannot diverge between call sites.
"""

import ipaddress
import socket
import urllib.parse

# RFC 1918 private ranges + loopback + link-local (AWS metadata 169.254.169.254)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def validate_url(url: str) -> None:
    """Validate that a URL does not point to internal/private network addresses.

    Prevents SSRF attacks by resolving the hostname and checking the resulting
    IP against RFC 1918 ranges, loopback, link-local (AWS metadata), and 0.0.0.0.
    Raises ValueError if the URL targets a blocked address.

    Call this before any outbound HTTP request to a user-supplied URL.
    """
    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no hostname: {url!r}")

    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname!r}")

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)

        if ip == ipaddress.ip_address("0.0.0.0"):
            raise ValueError(f"Blocked request to non-routable address: {ip_str}")

        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise ValueError(f"Blocked request to private/internal address: {ip_str}")
