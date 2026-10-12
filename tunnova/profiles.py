"""Strict, offline-only parsing for one MVP VLESS configuration format.

The parser never logs or persists VLESS credentials. The app only displays
non-secret profile fields. These objects must not be included in error logs.
"""
from dataclasses import dataclass, field
import unicodedata
from urllib.parse import urlsplit, parse_qs
from urllib.parse import unquote
from uuid import UUID


_SUPPORTED_VLESS_PARAMETERS = frozenset(
    {"type", "security", "encryption", "sni", "host", "path"}
)


class ProfileError(ValueError):
    """A validation error without credential-bearing input text."""


def _contains_control_char(value: str) -> bool:
    return any(
        unicodedata.category(char) in {"Cc", "Cf"}
        for char in value
    )


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
    if any(ch.isspace() for ch in uri) or _contains_control_char(uri):
        raise ProfileError("The VLESS link contains unexpected whitespace or control characters.")
    try:
        parsed = urlsplit(uri)
        if (
            parsed.scheme.lower() != "vless"
            or not parsed.username
            or parsed.password is not None
        ):
            raise ProfileError("Expected a VLESS link with a UUID and server address.")
        uuid = str(UUID(parsed.username))
        host = parsed.hostname
        port = parsed.port
    except (ValueError, TypeError):
        raise ProfileError("The VLESS link is invalid or has an invalid server port.") from None
    if not host or port is None or not (1 <= port <= 65535):
        raise ProfileError("The VLESS link must include a valid server and port.")

    try:
        params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError:
        raise ProfileError("Invalid VLESS link parameters.") from None
    if params.keys() - _SUPPORTED_VLESS_PARAMETERS:
        raise ProfileError("Unsupported VLESS link parameters are not supported.")
    if any(len(v) != 1 for v in params.values()):
        raise ProfileError("Duplicate VLESS parameters are not supported.")
    if any(_contains_control_char(value) for values in params.values() for value in values):
        raise ProfileError("VLESS link parameters cannot contain control characters.")
    if _contains_control_char(unquote(host)):
        raise ProfileError("The VLESS server address cannot contain control characters.")

    transport = params.get("type", ["tcp"])[0].lower()
    security = params.get("security", ["none"])[0].lower()
    if transport != "ws" or security != "tls":
        raise ProfileError("This prototype previews VLESS + WebSocket + TLS links only.")
    if params.get("encryption", ["none"])[0].lower() != "none":
        raise ProfileError("This prototype supports VLESS encryption=none only.")

    server_name = params.get("sni", [""])[0]
    ws_host = params.get("host", [""])[0]
    ws_path = params.get("path", ["/"])[0] or "/"
    if not ws_path.startswith("/") or len(ws_path) > 1024:
        raise ProfileError("The WebSocket path must begin with '/' and be reasonably short.")
    if len(server_name) > 253 or len(ws_host) > 253:
        raise ProfileError("An invalid server name or WebSocket host was supplied.")
    name = unquote(parsed.fragment)
    if _contains_control_char(name):
        raise ProfileError("The profile name cannot contain control characters.")
    return VlessProfile(
        server=host, port=port, user_id=uuid,
        name=name[:80] or "Imported server",
        transport=transport, security=security,
        server_name=server_name, ws_host=ws_host, ws_path=ws_path,
    )


class ProfileManager:
    """Manage imported profiles in memory without persisting credentials."""

    def __init__(self) -> None:
        self._profiles: list[VlessProfile] = []
        self._selected_index: int | None = None

    @property
    def profiles(self) -> tuple[VlessProfile, ...]:
        """Return an immutable view of the imported profiles."""
        return tuple(self._profiles)

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    @property
    def selected_profile(self) -> VlessProfile | None:
        if self._selected_index is None:
            return None
        return self._profiles[self._selected_index]

    def import_link(self, uri: str) -> VlessProfile:
        """Validate and add a link, rejecting an already imported profile."""
        profile = parse_vless_uri(uri)
        if any(self._is_duplicate(profile, existing) for existing in self._profiles):
            raise ProfileError("This profile has already been imported.")
        self._profiles.append(profile)
        return profile

    def select(self, index: int) -> VlessProfile:
        if not 0 <= index < len(self._profiles):
            raise IndexError("Profile index is out of range.")
        self._selected_index = index
        return self._profiles[index]

    def clear_selection(self) -> None:
        self._selected_index = None

    def remove_selected(self) -> VlessProfile | None:
        if self._selected_index is None:
            return None
        index = self._selected_index
        removed = self._profiles.pop(index)
        self._selected_index = min(index, len(self._profiles) - 1) if self._profiles else None
        return removed

    @staticmethod
    def _is_duplicate(left: VlessProfile, right: VlessProfile) -> bool:
        # The UUID is part of profile identity but is never formatted or logged.
        return (
            left.user_id,
            left.server.casefold(),
            left.port,
            left.transport.casefold(),
            left.security.casefold(),
            left.server_name.casefold(),
            left.ws_path,
            left.ws_host.casefold(),
        ) == (
            right.user_id,
            right.server.casefold(),
            right.port,
            right.transport.casefold(),
            right.security.casefold(),
            right.server_name.casefold(),
            right.ws_path,
            right.ws_host.casefold(),
        )
