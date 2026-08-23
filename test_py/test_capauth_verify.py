import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services.capauth_verify import _signature_matches_issuer


FINGERPRINT = "D9150E6ADCBA46551BCB294FC26A88C5DFDCECA3"


class Payload:
    issuer = FINGERPRINT

    def model_dump_json(self):
        return '{"safe":"aggregate"}'


class CapAuthVerifierTest(unittest.TestCase):
    def test_validsig_must_name_declared_issuer(self):
        token = SimpleNamespace(signature="signed", payload=Payload())
        valid = SimpleNamespace(
            returncode=0,
            stdout=f"[GNUPG:] VALIDSIG {FINGERPRINT} 2026-08-23 0 4 0 22 10 00 {FINGERPRINT}\n",
        )
        with patch("services.capauth_verify.subprocess.run", return_value=valid):
            self.assertTrue(_signature_matches_issuer(token))

        wrong = SimpleNamespace(
            returncode=0,
            stdout="[GNUPG:] VALIDSIG 0000000000000000000000000000000000000000 2026-08-23\n",
        )
        with patch("services.capauth_verify.subprocess.run", return_value=wrong):
            self.assertFalse(_signature_matches_issuer(token))

    def test_missing_or_failed_signature_fails_closed(self):
        self.assertFalse(_signature_matches_issuer(SimpleNamespace(signature=None, payload=Payload())))
        failed = SimpleNamespace(returncode=2, stdout="")
        with patch("services.capauth_verify.subprocess.run", return_value=failed):
            self.assertFalse(_signature_matches_issuer(SimpleNamespace(signature="bad", payload=Payload())))


if __name__ == "__main__":
    unittest.main()
