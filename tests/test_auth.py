import time
import unittest

from tg_event.auth import issue_token, verify_token


class AuthTest(unittest.TestCase):
    def test_valid_token_roundtrip(self):
        token = issue_token("admin", "s3cr3t")
        self.assertEqual(verify_token(token, "s3cr3t"), "admin")

    def test_wrong_secret_rejected(self):
        token = issue_token("admin", "s3cr3t")
        self.assertIsNone(verify_token(token, "other"))

    def test_tampered_body_rejected(self):
        token = issue_token("admin", "s3cr3t")
        body, signature = token.split(".", 1)
        tampered = body[:-1] + ("A" if body[-1] != "A" else "B") + "." + signature
        self.assertIsNone(verify_token(tampered, "s3cr3t"))

    def test_tampered_signature_rejected(self):
        token = issue_token("admin", "s3cr3t")
        body, _signature = token.split(".", 1)
        wrong_sig = issue_token("admin", "other-secret").split(".", 1)[1]
        tampered = body + "." + wrong_sig
        self.assertIsNone(verify_token(tampered, "s3cr3t"))

    def test_expired_token_rejected(self):
        token = issue_token("admin", "s3cr3t", ttl_seconds=-10)
        self.assertIsNone(verify_token(token, "s3cr3t"))

    def test_garbage_token_rejected(self):
        self.assertIsNone(verify_token("not.a.token", "s3cr3t"))

    def test_default_secret_when_empty(self):
        token = issue_token("admin", "")
        self.assertEqual(verify_token(token, ""), "admin")
        self.assertIsNone(verify_token(token, "nonempty"))


if __name__ == "__main__":
    unittest.main()