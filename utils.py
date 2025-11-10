import ipaddress
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """Validates URL to prevent SSRF attacks"""
    try:
        parsed = urlparse(url)

        # Only allow http and https
        if parsed.scheme not in ('http', 'https'):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Block localhost
        if hostname.lower() in ('localhost', 'localhost.localdomain'):
            return False

        # Block private/reserved IPs
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                return False
        except ValueError:
            # Not an IP, it's a domain - allow it
            pass

        return True

    except Exception:
        return False
