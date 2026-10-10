"""Strict, offline-only parsing for one MVP VLESS configuration format.

The parser never logs or persists VLESS credentials. The app only displays
non-secret profile fields. These objects must not be included in error logs.
"""
from dataclasses import dataclass, field
from urllib.parse import urlsplit, parse_qs
from uuid import UUID


class ProfileError(ValueError):
    """A validation error without credential-bearing input text."""


@dataclass(frozen=True, repr=False)
class VlessProfile:
    server: str
    port: int
    user_id: str = field(repr=False)
    name: str = "Imported server"
    transport: str = "ws"
    security: str = "tls"
    server_name: str = ""
    ws_path: str = "/"
    ws_host: str = ""

    def __repr__(self) -> str:
        return f"VlessProfile(server={self.server!r}, port={self.port!r}, user_id=<redacted>)"


def parse_vless_uri(uri: str) -> VlessProfile:
    """Parse VLESS + WebSocket + TLS for preview only (no connection).

    Deliberately reject unsupported modes instead of silently misconfiguring a
    networking engine. VLESS links must contain a valid UUID and destination.
    """
    if not isinstance(uri, str) or not uri or len(uri) > 8192:
        raise ProfileError("Provide a valid VLESS link (maximum 8192 characters).")
    if any(ch.isspace() for ch in uri):
        raise ProfileError("The VLESS link contains unexpected whitespace.")
    try:
        parsed = urlsplit(uri)
        if parsed.scheme.lower() != "vless" or not parsed.username or parsed.password:
            raise ProfileError("Expected a VLESS link with a UUID and server address.")
        uuid = str(UUID(parsed.username))
        host = parsed.hostname
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise ProfileError("The VLESS link is invalid or has an invalid server port.") from None
    if not host or port is None or not (1 <= port <= 65535):
        raise ProfileError("The VLESS link must include a valid server and port.")

    try:
        params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise ProfileError("Invalid VLESS link parameters.") from None
    if any(len(v) != 1 for v in params.values()):
        raise ProfileError("Duplicate VLESS parameters are not supported.")

    transport = params.get("type", ["tcp"])[0].lower()
    security = params.get("security", ["none"])[0].lower()
    if transport != "ws" or security != "tls":
        raise ProfileError("This prototype previews VLESS + WebSocket + TLS links only.")

    server_name = params.get("sni", [""])[0]
    ws_host = params.get("host", [""])[0]
    ws_path = params.get("path", ["/"])[0] or "/"
    if not ws_path.startswith("/") or len(ws_path) > 1024:
        raise ProfileError("The WebSocket path must begin with '/' and be reasonably short.")
    for value in (server_name, ws_host):
        if len(value) > 253 or "\n" in value or "\r" in value:
            raise ProfileError("An invalid server name or WebSocket host was supplied.")
    return VlessProfile(
        server=host, port=port, user_id=uuid,
        name=parsed.fragment[:80] or "Imported server",
        transport=transport, security=security,
        server_name=server_name, ws_host=ws_host, ws_path=ws_path,
    )
