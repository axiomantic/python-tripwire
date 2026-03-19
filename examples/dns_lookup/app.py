"""Service endpoint DNS resolution."""

import socket


def resolve_service_endpoint(service_name, port=443):
    """Resolve a service hostname and return (ip, port) tuple."""
    results = socket.getaddrinfo(service_name, port, socket.AF_INET, socket.SOCK_STREAM)
    if not results:
        raise RuntimeError(f"Could not resolve {service_name}")
    family, socktype, proto, canonname, sockaddr = results[0]
    return sockaddr
