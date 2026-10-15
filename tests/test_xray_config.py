import json
import unittest

from tunnova.profiles import VlessProfile, parse_vless_uri
from tunnova.xray_config import XrayConfigError, build_xray_config


SYNTHETIC_UUID = "11111111-2222-4333-8444-555555555555"


def make_profile(server="vpn.example.com", **parameters):
    uri_server = f"[{server}]" if ":" in server and not server.startswith("[") else server
    return parse_vless_uri(
        f"vless://{SYNTHETIC_UUID}@{uri_server}:443"
        "?type=ws&security=tls"
        f"&sni={parameters.get('sni', 'certificate.example.com')}"
        f"&host={parameters.get('host', 'websocket.example.net')}"
        f"&path={parameters.get('path', '%2Fedge')}"
    )


class XrayConfigTests(unittest.TestCase):
    def test_maps_profile_fields_independently(self):
        profile = make_profile(
            server="gateway.example.org",
            sni="certificate.example.com",
            host="cdn.example.net:8443",
            path="%2Fconnect",
        )

        config = build_xray_config(profile)
        inbound = config["inbounds"][0]
        outbound = config["outbounds"][0]

        self.assertEqual(inbound["listen"], "127.0.0.1")
        self.assertEqual(inbound["port"], 10890)
        self.assertEqual(inbound["protocol"], "socks")
        self.assertEqual(outbound["settings"]["address"], "gateway.example.org")
        self.assertEqual(outbound["settings"]["port"], 443)
        self.assertEqual(outbound["settings"]["id"], SYNTHETIC_UUID)
        stream_settings = outbound["streamSettings"]
        self.assertEqual(stream_settings["network"], "ws")
        self.assertNotIn("method", stream_settings)
        self.assertEqual(stream_settings["tlsSettings"]["serverName"], "certificate.example.com")
        self.assertNotIn("verifyPeerCertByName", stream_settings["tlsSettings"])
        self.assertEqual(stream_settings["wsSettings"]["host"], "cdn.example.net:8443")
        self.assertEqual(stream_settings["wsSettings"]["path"], "/connect")

    def test_explicit_tls_verify_name_is_independent_of_sni_and_websocket_host(self):
        profile = make_profile(
            sni="sni.example.com",
            host="websocket.example.net",
        )

        tls_settings = build_xray_config(
            profile,
            tls_verify_name="trusted-cert.example.org",
        )["outbounds"][0]["streamSettings"]["tlsSettings"]
        ws_settings = build_xray_config(
            profile,
            tls_verify_name="trusted-cert.example.org",
        )["outbounds"][0]["streamSettings"]["wsSettings"]

        self.assertEqual(tls_settings["serverName"], "sni.example.com")
        self.assertEqual(
            tls_settings["verifyPeerCertByName"], "trusted-cert.example.org"
        )
        self.assertIs(tls_settings["allowInsecure"], False)
        self.assertEqual(ws_settings["host"], "websocket.example.net")

    def test_rejects_invalid_tls_verify_names(self):
        for verify_name in (
            "",
            "bad host.example",
            "bad..example.com",
            "bad\n.example.com",
            "first.example.com second.example.com",
            "first.example.com,second.example.com",
            "192.0.2.10",
            42,
        ):
            with self.subTest(verify_name=verify_name):
                with self.assertRaises(XrayConfigError) as cm:
                    build_xray_config(make_profile(), tls_verify_name=verify_name)
                self.assertEqual(
                    str(cm.exception), "The TLS verification hostname is invalid."
                )

    def test_config_is_json_compatible_and_keeps_tls_verification_enabled(self):
        config = build_xray_config(make_profile())

        self.assertIs(config["outbounds"][0]["streamSettings"]["tlsSettings"]["allowInsecure"], False)
        self.assertEqual(json.loads(json.dumps(config)), config)

    def test_configurable_socks_port(self):
        self.assertEqual(build_xray_config(make_profile(), 12345)["inbounds"][0]["port"], 12345)

    def test_rejects_invalid_socks_ports(self):
        for port in (0, 65536, -1, True, 10890.5, "10890", None):
            with self.subTest(port=port):
                with self.assertRaises(XrayConfigError):
                    build_xray_config(make_profile(), port)

    def test_rejects_unsupported_profile_transport_or_security(self):
        for field, value in (("transport", "grpc"), ("security", "none")):
            with self.subTest(field=field):
                profile = VlessProfile(
                    server="vpn.example.com",
                    port=443,
                    user_id=SYNTHETIC_UUID,
                    **{field: value},
                )
                with self.assertRaises(XrayConfigError):
                    build_xray_config(profile)

    def test_rejects_invalid_profile_server_and_credentials(self):
        for profile in (
            VlessProfile(server="not a host", port=443, user_id=SYNTHETIC_UUID),
            VlessProfile(server="vpn.example.com", port=443, user_id="synthetic-secret"),
            VlessProfile(server="vpn.example.com", port=65536, user_id=SYNTHETIC_UUID),
        ):
            with self.subTest(server=profile.server):
                with self.assertRaises(XrayConfigError):
                    build_xray_config(profile)

    def test_rejects_invalid_sni_and_websocket_paths(self):
        for field, value in (
            ("server_name", "bad host"),
            ("ws_host", "bad host"),
            ("ws_host", "bad\u202ehost"),
            ("ws_path", "relative-path"),
            ("ws_path", "/bad\npath"),
        ):
            with self.subTest(field=field):
                profile = VlessProfile(
                    server="vpn.example.com",
                    port=443,
                    user_id=SYNTHETIC_UUID,
                    **{field: value},
                )
                with self.assertRaises(XrayConfigError):
                    build_xray_config(profile)

    def test_accepts_domain_ipv4_and_ipv6_servers(self):
        for server in ("vpn.example.com", "192.0.2.10", "2001:db8::10"):
            with self.subTest(server=server):
                config = build_xray_config(make_profile(server=server))
                self.assertEqual(
                    config["outbounds"][0]["settings"]["address"], server
                )

    def test_rejects_non_profile_input(self):
        with self.assertRaises(XrayConfigError):
            build_xray_config("vless://synthetic")


if __name__ == "__main__":
    unittest.main()
