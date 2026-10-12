import unittest

from tunnova.profiles import ProfileError, parse_vless_uri


class UnicodeControlTests(unittest.TestCase):
    def test_encoded_bidi_control_in_profile_name(self):
        uri = (
            "vless://11111111-2222-4333-8444-555555555555"
            "@example.com:443?type=ws&security=tls"
            "&path=%2Fedge#Demo%E2%80%AEName"
        )

        with self.assertRaises(ProfileError):
            parse_vless_uri(uri)


if __name__ == "__main__":
    unittest.main()
