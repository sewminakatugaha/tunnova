"""Build an in-memory Xray-core client configuration from a validated profile."""
import ipaddress
import re
import unicodedata
from typing import Any
from uuid import UUID

from .profiles import VlessProfile


class XrayConfigError(ValueError):
    """A safe, credential-free configuration validation error."""


_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")


def _contains_control_char(value: str) -> bool:
    return any(unicodedata.category(char) in {"Cc", "Cf"} for char in value)


def _is_address_or_domain(value: str, *, allow_brackets: bool = False) -> bool:
    candidate = value
    if candidate.startswith("[") and candidate.endswith("]"):
        if not allow_brackets:
            return False
        candidate = candidate[1:-1]
        try:
            return ipaddress.ip_address(candidate).version == 6
        except ValueError:
            return False
    if not candidate or "%" in candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass

    try:
        ascii_domain = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_domain) > 253:
        return False
    domain = ascii_domain[:-1] if ascii_domain.endswith(".") else ascii_domain
    labels = domain.split(".")
    return bool(domain) and all(_DNS_LABEL.fullmatch(label) for label in labels)


def _validate_tls_verify_name(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or _contains_control_char(value):
        raise XrayConfigError("The TLS verification hostname is invalid.")
    try:
        ascii_domain = value.encode("idna").decode("ascii")
    except UnicodeError:
        raise XrayConfigError("The TLS verification hostname is invalid.") from None
    domain = ascii_domain[:-1] if ascii_domain.endswith(".") else ascii_domain
    if not domain or len(ascii_domain) > 253:
        raise XrayConfigError("The TLS verification hostname is invalid.")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise XrayConfigError("The TLS verification hostname is invalid.")
    if not all(_DNS_LABEL.fullmatch(label) for label in domain.split(".")):
        raise XrayConfigError("The TLS verification hostname is invalid.")
    return ascii_domain


def _is_websocket_host(value: str) -> bool:
    if not value:
        return True
    if value.startswith("["):
        closing_bracket = value.find("]")
        if closing_bracket == -1:
            return False
        address = value[:closing_bracket + 1]
        suffix = value[closing_bracket + 1:]
        if not _is_address_or_domain(address, allow_brackets=True):
            return False
        return not suffix or _is_port_suffix(suffix)
    if value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        return _is_address_or_domain(host) and _is_port_suffix(f":{port}")
    return _is_address_or_domain(value)


def _is_port_suffix(value: str) -> bool:
    digits = value[1:]
    if not value.startswith(":") or not digits.isascii() or not digits.isdecimal():
        return False
    port = int(digits)
    return 1 <= port <= 65535


def _validate_profile(profile: VlessProfile) -> None:
    if not isinstance(profile, VlessProfile):
        raise XrayConfigError("Expected a validated VLESS profile.")
    if profile.transport != "ws" or profile.security != "tls":
        raise XrayConfigError("Only VLESS + WebSocket + TLS profiles are supported.")
    if not isinstance(profile.port, int) or isinstance(profile.port, bool) or not 1 <= profile.port <= 65535:
        raise XrayConfigError("The VLESS server port is invalid.")
    if not isinstance(profile.user_id, str):
        raise XrayConfigError("The VLESS user ID is invalid.")
    try:
        UUID(profile.user_id)
    except (ValueError, TypeError, AttributeError):
        raise XrayConfigError("The VLESS user ID is invalid.") from None
    if (
        not isinstance(profile.server, str)
        or _contains_control_char(profile.server)
        or not _is_address_or_domain(profile.server)
    ):
        raise XrayConfigError("The VLESS server address is invalid.")

    for value, field_name in (
        (profile.server_name, "TLS server name"),
        (profile.ws_host, "WebSocket host"),
    ):
        if (
            not isinstance(value, str)
            or len(value) > 253
            or _contains_control_char(value)
        ):
            raise XrayConfigError(f"The {field_name} is invalid.")
    if profile.server_name and not _is_address_or_domain(
        profile.server_name, allow_brackets=True
    ):
        raise XrayConfigError("The TLS server name is invalid.")
    if not _is_websocket_host(profile.ws_host):
        raise XrayConfigError("The WebSocket host is invalid.")
    if (
        not isinstance(profile.ws_path, str)
        or not profile.ws_path.startswith("/")
        or len(profile.ws_path) > 1024
        or _contains_control_char(profile.ws_path)
    ):
        raise XrayConfigError("The WebSocket path is invalid.")


def build_xray_config(
    profile: VlessProfile,
    socks_port: int = 10890,
    *,
    tls_verify_name: str | None = None,
) -> dict[str, Any]:
    """Return an Xray JSON-compatible configuration without writing it to disk.

    The returned object contains credentials and must not be logged or printed.
    An explicit TLS verification hostname is independent of profile SNI.
    """
    _validate_profile(profile)
    tls_verify_name = _validate_tls_verify_name(tls_verify_name)
    if (
        not isinstance(socks_port, int)
        or isinstance(socks_port, bool)
        or not 1 <= socks_port <= 65535
    ):
        raise XrayConfigError("The local SOCKS5 port must be an integer from 1 to 65535.")

    tls_settings: dict[str, Any] = {
        "serverName": profile.server_name,
        "allowInsecure": False,
    }
    if tls_verify_name is not None:
        tls_settings["verifyPeerCertByName"] = tls_verify_name

    return {
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth"},
            }
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "address": profile.server,
                    "port": profile.port,
                    "id": profile.user_id,
                    "encryption": "none",
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": tls_settings,
                    "wsSettings": {
                        "path": profile.ws_path,
                        "host": profile.ws_host,
                    },
                },
            }
        ],
    }
