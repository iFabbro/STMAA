"""Research protocol identity primitives.

The package intentionally does not define or approve any research protocol.
"""

from .protocol import canonicalize_protocol, protocol_fingerprint

__all__ = ["canonicalize_protocol", "protocol_fingerprint"]
