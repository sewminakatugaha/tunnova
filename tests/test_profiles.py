import unittest
from tunnova.profiles import parse_vless_uri, ProfileError, ProfileManager

UUID = "11111111-2222-4333-8444-555555555555"


def make_link(server, name):
    return (
        f"vless://{UUID}@{server}:443"
        f"?type=ws&security=tls&path=%2Fedge&sni=example.com#{name}"
    )


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

    def test_reject_empty_password_component(self):
        with self.assertRaises(ProfileError):
            parse_vless_uri(f"vless://{UUID}:@example.com:443?type=ws&security=tls")

    def test_reject_unknown_parameter_without_leaking_link(self):
        link = (
            f"vless://{UUID}@example.com:443"
            "?type=ws&security=tls&secret=private-value"
        )

        with self.assertRaises(ProfileError) as cm:
            parse_vless_uri(link)

        self.assertNotIn(link, str(cm.exception))
        self.assertNotIn(UUID, str(cm.exception))
        self.assertNotIn("private-value", str(cm.exception))

    def test_accept_encryption_none_and_reject_other_encryption(self):
        profile = parse_vless_uri(
            f"vless://{UUID}@example.com:443?type=ws&security=tls&encryption=none"
        )
        self.assertEqual(profile.server, "example.com")

        with self.assertRaises(ProfileError):
            parse_vless_uri(
                f"vless://{UUID}@example.com:443"
                "?type=ws&security=tls&encryption=aes-128-gcm"
            )

    def test_accept_header_type_none_case_insensitively(self):
        profile = parse_vless_uri(
            f"vless://{UUID}@example.com:443?type=ws&security=tls&headerType=NoNe"
        )
        self.assertEqual(profile.transport, "ws")
        self.assertEqual(profile.security, "tls")

    def test_header_type_is_optional(self):
        profile = parse_vless_uri(
            f"vless://{UUID}@example.com:443?type=ws&security=tls"
        )
        self.assertEqual(profile.server, "example.com")

    def test_reject_unsupported_or_empty_header_type(self):
        for header_type in ("srtp", ""):
            with self.subTest(header_type=header_type):
                link = (
                    f"vless://{UUID}@example.com:443"
                    f"?type=ws&security=tls&headerType={header_type}"
                )
                with self.assertRaises(ProfileError) as cm:
                    parse_vless_uri(link)
                self.assertEqual(str(cm.exception), "Unsupported VLESS header type.")
                self.assertNotIn(UUID, str(cm.exception))

    def test_reject_duplicate_header_type(self):
        with self.assertRaises(ProfileError) as cm:
            parse_vless_uri(
                f"vless://{UUID}@example.com:443"
                "?type=ws&security=tls&headerType=none&headerType=none"
            )
        self.assertEqual(str(cm.exception), "Duplicate VLESS parameters are not supported.")

    def test_reject_encoded_control_characters_in_configuration_fields(self):
        invalid_links = (
            f"vless://{UUID}@example.com:443?type=ws&security=tls&path=%2Fedge%00",
            f"vless://{UUID}@example.com:443?type=ws&security=tls#Demo%0A",
            f"vless://{UUID}@example.com:443?type=ws&security=tls&sni=example.com%7F",
        )

        for link in invalid_links:
            with self.subTest(link=link):
                with self.assertRaises(ProfileError) as cm:
                    parse_vless_uri(link)
                self.assertNotIn(link, str(cm.exception))
                self.assertNotIn(UUID, str(cm.exception))

    def test_no_internet_needed(self):
        profile = parse_vless_uri(f"vless://{UUID}@127.0.0.1:443?type=ws&security=tls")
        self.assertEqual(profile.server, "127.0.0.1")

    def test_accepts_ipv4_ipv6_and_dns_server_addresses(self):
        for server in ("192.0.2.1", "[2001:db8::1]", "vpn.example.com"):
            with self.subTest(server=server):
                profile = parse_vless_uri(
                    f"vless://{UUID}@{server}:443?type=ws&security=tls"
                )
                self.assertEqual(profile.server, server.strip("[]"))


class ProfileManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ProfileManager()

    def test_import_adds_profile_in_memory(self):
        profile = self.manager.import_link(make_link("example.com", "Demo"))

        self.assertEqual(self.manager.profiles, (profile,))
        self.assertIsNone(self.manager.selected_profile)
        self.assertEqual(profile.name, "Demo")

    def test_duplicate_profile_is_rejected_even_with_different_name(self):
        self.manager.import_link(make_link("example.com", "First"))

        with self.assertRaises(ProfileError) as cm:
            self.manager.import_link(make_link("example.com", "Second"))

        self.assertEqual(str(cm.exception), "This profile has already been imported.")
        self.assertEqual(len(self.manager.profiles), 1)
        self.assertNotIn(UUID, str(cm.exception))

    def test_invalid_profile_is_not_added_or_exposed_in_error(self):
        secret = "synthetic-invalid-credential"
        invalid_link = f"vless://{secret}@example.com:443?type=ws&security=tls"

        with self.assertRaises(ProfileError) as cm:
            self.manager.import_link(invalid_link)

        self.assertNotIn(secret, str(cm.exception))
        self.assertEqual(self.manager.profiles, ())

    def test_select_and_remove_profiles_updates_selection(self):
        first = self.manager.import_link(make_link("first.example", "First"))
        second = self.manager.import_link(make_link("second.example", "Second"))
        third = self.manager.import_link(make_link("third.example", "Third"))

        self.assertIs(self.manager.select(1), second)
        self.assertIs(self.manager.remove_selected(), second)
        self.assertEqual(self.manager.profiles, (first, third))
        self.assertEqual(self.manager.selected_index, 1)
        self.assertIs(self.manager.selected_profile, third)

        self.assertIs(self.manager.remove_selected(), third)
        self.assertEqual(self.manager.selected_index, 0)
        self.assertIs(self.manager.selected_profile, first)

        self.assertIs(self.manager.remove_selected(), first)
        self.assertEqual(self.manager.profiles, ())
        self.assertIsNone(self.manager.selected_index)
        self.assertIsNone(self.manager.selected_profile)

    def test_remove_without_selection_is_a_noop(self):
        self.manager.import_link(make_link("example.com", "Demo"))

        self.assertIsNone(self.manager.remove_selected())
        self.assertEqual(len(self.manager.profiles), 1)

    def test_selection_rejects_out_of_range_index(self):
        with self.assertRaises(IndexError):
            self.manager.select(0)


if __name__ == "__main__":
    unittest.main()
