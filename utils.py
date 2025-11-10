import ipaddress
from urllib.parse import urlparse


def is_safe_url(url: str) -> bool:
    """
    Validates URL to prevent SSRF attacks.

    Blocks:
    - Non-HTTP(S) schemes
    - localhost and loopback addresses
    - Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Link-local addresses (169.254.0.0/16)

    Args:
        url: The URL to validate

    Returns:
        True if URL is safe to access, False otherwise

    Examples:
        >>> is_safe_url("https://example.com")
        True
        >>> is_safe_url("http://localhost")
        False
        >>> is_safe_url("http://192.168.1.1")
        False
    """
    try:
        parsed = urlparse(url)

        # Only allow http and https schemes
        if parsed.scheme not in ('http', 'https'):
            return False

        # Get hostname
        hostname = parsed.hostname
        if not hostname:
            return False

        # Block localhost variants
        if hostname.lower() in ('localhost', 'localhost.localdomain'):
            return False

        # Try to parse as IP address
        try:
            ip = ipaddress.ip_address(hostname)

            # Block loopback addresses (127.0.0.0/8)
            if ip.is_loopback:
                return False

            # Block private IP ranges
            if ip.is_private:
                return False

            # Block link-local addresses (169.254.0.0/16)
            if ip.is_link_local:
                return False

            # Block reserved addresses
            if ip.is_reserved:
                return False

        except ValueError:
            # Not an IP address, it's a domain name - allow it
            pass

        return True

    except Exception:
        return False
