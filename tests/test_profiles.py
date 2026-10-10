import unittest
from tunnova.profiles import parse_vless_uri, ProfileError

UUID = "11111111-2222-4333-8444-555555555555"


class ProfileTests(unittest.TestCase):
    def test_supported_link(self):
        p = parse_vless_uri(
            f"vless://{UUID}@example.com:443?type=ws&security=tls&path=%2Fedge&sni=example.com#Demo"
        )
        self.assertEqual(p.server, "example.com")
        self.assertEqual(p.port, 443)
        self.assertEqual(p.ws_path, "/edge")
        self.assertEqual(p.name, "Demo")
        self.assertEqual(p.security, "tls")
        self.assertNotIn(UUID, repr(p))

    def test_reject_unsupported_transport(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}@example.com:443?type=grpc&security=tls")

    def test_reject_no_tls(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}@example.com:443?type=ws&security=none")

    def test_reject_invalid_uuid_without_leaking(self):
        secret = "not-a-uuid"
        with self.assertRaises(ProfileError) as cm:
            parse_vless_uri(f"vless://{secret}@example.com:443?type=ws&security=tls")
        self.assertNotIn(secret, str(cm.exception))

    def test_reject_bad_port(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}@example.com:99999?type=ws&security=tls")

    def test_reject_duplicate_parameter(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}@example.com:443?type=ws&type=ws&security=tls")

    def test_reject_password_component(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}:password@example.com:443?type=ws&security=tls")

    def test_no_internet_needed(self):
        profile = parse_vless_uri(f"vless://{UUID}@127.0.0.1:443?type=ws&security=tls")
        self.assertEqual(profile.server, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
