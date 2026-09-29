import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import gh, ghapp  # noqa: E402

APP_VARS = ("PT_TEAM_APP_ID", "PT_TEAM_APP_KEY", "PT_TEAM_APP_KEY_FILE",
            "PT_REVIEW_APP_ID", "PT_REVIEW_APP_KEY", "PT_REVIEW_APP_KEY_FILE",
            "PT_REVIEW_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "PT_REPO")
OPENSSL = shutil.which("openssl")


def clean_env(**values):
    """os.environ without any identity variable from the machine running the tests, plus `values`."""
    env = {k: v for k, v in os.environ.items() if k not in APP_VARS}
    env.update(values)
    return mock.patch.dict(os.environ, env, clear=True)


def b64url_decode(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        ghapp.reset()
        gh._token_cache = None
        self.addCleanup(ghapp.reset)
        self.addCleanup(setattr, gh, "_token_cache", None)


@unittest.skipUnless(OPENSSL, "openssl is not installed")
class JwtTest(IsolatedTest):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.key = os.path.join(cls.tmp.name, "app.pem")
        cls.pub = os.path.join(cls.tmp.name, "app.pub")
        subprocess.run([OPENSSL, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", cls.key],
                       check=True, capture_output=True)
        subprocess.run([OPENSSL, "pkey", "-in", cls.key, "-pubout", "-out", cls.pub], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def verify(self, token):
        signing_input, _, signature = token.rpartition(".")
        sig_path = os.path.join(self.tmp.name, "sig")
        Path(sig_path).write_bytes(b64url_decode(signature))
        proc = subprocess.run([OPENSSL, "dgst", "-sha256", "-verify", self.pub, "-signature", sig_path],
                              input=signing_input.encode(), capture_output=True)
        return proc.returncode == 0

    def test_header_claims_and_rs256_signature(self):
        with clean_env(PT_TEAM_APP_ID="12345", PT_TEAM_APP_KEY_FILE=self.key):
            token = ghapp.jwt(ghapp.config("team"), now=1_700_000_000)
        header, claims, _ = token.split(".")
        self.assertEqual(json.loads(b64url_decode(header)), {"alg": "RS256", "typ": "JWT"})
        self.assertEqual(json.loads(b64url_decode(claims)),
                         {"iat": 1_700_000_000 - 60, "exp": 1_700_000_000 + 540, "iss": "12345"})
        self.assertNotIn("=", token)
        self.assertTrue(self.verify(token))

    def test_tampered_claims_do_not_verify(self):
        with clean_env(PT_TEAM_APP_ID="12345", PT_TEAM_APP_KEY_FILE=self.key):
            header, _, signature = ghapp.jwt(ghapp.config("team"), now=1_700_000_000).split(".")
        forged = base64.urlsafe_b64encode(b'{"iss":"999"}').rstrip(b"=").decode()
        self.assertFalse(self.verify(f"{header}.{forged}.{signature}"))

    def test_inline_key_base64_and_raw_both_sign_and_leave_no_file(self):
        pem = Path(self.key).read_text()
        for inline in (base64.b64encode(pem.encode()).decode(), pem, pem.replace("\n", "\\n")):
            with self.subTest(form=inline[:12]), clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=inline):
                before = set(os.listdir(tempfile.gettempdir()))
                self.assertTrue(self.verify(ghapp.jwt(ghapp.config("team"))))
                leftovers = {f for f in set(os.listdir(tempfile.gettempdir())) - before if f.startswith("pt-app-")}
                self.assertEqual(leftovers, set())

    def test_a_key_that_is_not_rsa_is_an_actionable_error(self):
        junk = os.path.join(self.tmp.name, "junk.pem")
        Path(junk).write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n")
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE=junk):
            with self.assertRaisesRegex(ghapp.AppError, "could not sign"):
                ghapp.jwt(ghapp.config("team"))


class InlineKeyFileTest(IsolatedTest):
    PEM = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    def test_temp_file_is_0600_while_signing_and_deleted_after(self):
        seen = {}

        def fake_sign(message, path, kind):
            seen.update(path=path, mode=stat.S_IMODE(os.stat(path).st_mode), body=Path(path).read_text())
            return b"sig"

        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=base64.b64encode(self.PEM.encode()).decode()), \
             mock.patch.object(ghapp, "_openssl_sign", side_effect=fake_sign):
            ghapp.sign(b"x", ghapp.config("team"))
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(seen["body"], self.PEM)
        self.assertFalse(os.path.exists(seen["path"]))

    def test_temp_file_is_deleted_when_signing_fails(self):
        seen = {}

        def failing_sign(message, path, kind):
            seen["path"] = path
            raise ghapp.AppError("openssl could not sign")

        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=self.PEM), \
             mock.patch.object(ghapp, "_openssl_sign", side_effect=failing_sign):
            with self.assertRaises(ghapp.AppError):
                ghapp.sign(b"x", ghapp.config("team"))
        self.assertFalse(os.path.exists(seen["path"]))

    def test_config_repr_never_shows_the_key(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=self.PEM):
            self.assertNotIn("PRIVATE", repr(ghapp.config("team")))

    def test_garbage_inline_key_is_rejected_before_openssl(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY="definitely not base64!"), \
             mock.patch.object(ghapp, "_openssl_sign") as signer:
            with self.assertRaisesRegex(ghapp.AppError, "neither a PEM"):
                ghapp.sign(b"x", ghapp.config("team"))
        signer.assert_not_called()

    def test_missing_openssl_is_explained(self):
        with mock.patch.object(ghapp.subprocess, "run", side_effect=FileNotFoundError("openssl")):
            with self.assertRaisesRegex(ghapp.AppError, "`openssl` command is not installed"):
                ghapp._openssl_sign(b"x", "/k.pem", "team")


class ConfigTest(IsolatedTest):
    def test_unset_means_not_configured(self):
        with clean_env():
            self.assertIsNone(ghapp.config("team"))
            self.assertFalse(gh.app_mode())

    def test_half_configured_apps_fail_instead_of_falling_back(self):
        for env, message in (({"PT_TEAM_APP_ID": "1"}, "its key is not"),
                             ({"PT_TEAM_APP_KEY": "x"}, "APP_ID is not"),
                             ({"PT_TEAM_APP_ID": "my-app", "PT_TEAM_APP_KEY_FILE": "/k"}, "numeric id")):
            with self.subTest(env=env), clean_env(**env):
                with self.assertRaisesRegex(ghapp.AppError, message):
                    ghapp.config("team")

    def test_review_app_must_differ_from_team_app(self):
        with clean_env(PT_TEAM_APP_ID="7", PT_TEAM_APP_KEY_FILE="/a", PT_REVIEW_APP_ID="7", PT_REVIEW_APP_KEY_FILE="/b"):
            with self.assertRaisesRegex(ghapp.AppError, "separate GitHub App"):
                ghapp.config("review")

    def test_missing_key_file_is_named(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/nonexistent/app.pem"):
            with self.assertRaisesRegex(ghapp.AppError, "does not point to a file"):
                ghapp.sign(b"x", ghapp.config("team"))


class FakeGitHub:
    """Answers the app endpoints; records what was asked with which credential."""

    def __init__(self, expires_in=3600, installation_error=None):
        self.calls = []
        self.minted = 0
        self.expires_in = expires_in
        self.installation_error = installation_error

    def __call__(self, path, method="GET", fields=None, auth=None):
        self.calls.append((method, path, fields, auth))
        if path.endswith("/installation"):
            if self.installation_error:
                raise gh.GhError(f"GET {path} → HTTP {self.installation_error}: {{}}")
            return {"id": 42}
        if path == "app/installations/42/access_tokens":
            self.minted += 1
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + self.expires_in))
            return {"token": f"ghs_token{self.minted}", "expires_at": stamp}
        if path == "app":
            return {"slug": "acme-team"}
        if path == "users/acme-team[bot]":
            return {"id": 9001, "login": "acme-team[bot]"}
        if path == "repos/o/r":
            return {"owner": {"login": "geeera"}}
        if path == "user":
            raise AssertionError("installation tokens cannot call GET /user")
        raise AssertionError(f"unexpected {method} {path}")


class InstallationTokenTest(IsolatedTest):
    def setUp(self):
        super().setUp()
        env = clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/k.pem", PT_REPO="o/r")
        env.start()
        self.addCleanup(env.stop)
        jwt = mock.patch.object(ghapp, "jwt", return_value="the.jwt")
        jwt.start()
        self.addCleanup(jwt.stop)

    def test_mints_a_token_scoped_to_the_repository_with_the_jwt(self):
        fake = FakeGitHub()
        with mock.patch.object(gh, "api", side_effect=fake):
            self.assertEqual(ghapp.installation_token("team", "o/r"), "ghs_token1")
        self.assertEqual(fake.calls[0], ("GET", "repos/o/r/installation", None, "the.jwt"))
        self.assertEqual(fake.calls[1], ("POST", "app/installations/42/access_tokens", {"repositories": ["r"]}, "the.jwt"))

    def test_cached_until_close_to_expiry(self):
        fake = FakeGitHub()
        with mock.patch.object(gh, "api", side_effect=fake):
            ghapp.installation_token("team", "o/r")
            self.assertEqual(ghapp.installation_token("team", "o/r"), "ghs_token1")
        self.assertEqual(fake.minted, 1)

    def test_renewed_inside_the_refresh_margin(self):
        fake = FakeGitHub(expires_in=ghapp.REFRESH_MARGIN - 10)
        with mock.patch.object(gh, "api", side_effect=fake):
            ghapp.installation_token("team", "o/r")
            self.assertEqual(ghapp.installation_token("team", "o/r"), "ghs_token2")
        self.assertEqual(fake.minted, 2)

    def test_not_installed_and_clock_skew_are_explained(self):
        for code, message in (("404", "not installed on o/r"), ("401", "clock")):
            with self.subTest(code=code), mock.patch.object(gh, "api", side_effect=FakeGitHub(installation_error=code)):
                with self.assertRaisesRegex(ghapp.AppError, message):
                    ghapp.installation_token("team", "o/r")

    def test_identity_is_the_bot_with_a_noreply_email(self):
        with mock.patch.object(gh, "api", side_effect=FakeGitHub()):
            ident = ghapp.identity("team", "o/r")
        self.assertEqual(ident, {"login": "acme-team[bot]", "id": 9001, "name": "acme-team[bot]",
                                 "email": "9001+acme-team[bot]@users.noreply.github.com"})

    def test_app_mode_acts_as_the_bot_not_the_owner(self):
        with mock.patch.object(gh, "api", side_effect=FakeGitHub()):
            self.assertEqual(gh.token_login(), "acme-team[bot]")
            self.assertFalse(gh.acts_as_owner("o/r"))

    def test_app_mode_whose_bot_is_the_owner_is_still_same_account(self):
        # Pathological, but the comparison must stay honest rather than assume an app is always separate.
        fake = FakeGitHub()
        with mock.patch.object(gh, "api", side_effect=lambda p, *a, **k: {"owner": {"login": "acme-team[bot]"}}
                               if p == "repos/o/r" else fake(p, *a, **k)):
            self.assertTrue(gh.acts_as_owner("o/r"))


class TokenPrecedenceTest(IsolatedTest):
    def test_team_app_wins_over_personal_tokens(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/k", GH_TOKEN="personal", PT_REPO="o/r"), \
             mock.patch.object(ghapp, "installation_token", return_value="ghs_app") as minted:
            self.assertEqual(gh.token(), "ghs_app")
        minted.assert_called_once_with("team", "o/r")

    def test_without_the_app_the_personal_chain_is_unchanged(self):
        with clean_env(GH_TOKEN="t1", GITHUB_TOKEN="t2"), mock.patch("shutil.which", return_value=None):
            self.assertEqual(gh.token(), "t1")
        gh._token_cache = None
        with clean_env(GITHUB_TOKEN="t2"), mock.patch("shutil.which", return_value=None):
            self.assertEqual(gh.token(), "t2")

    def test_a_failing_app_never_falls_back_to_the_personal_token(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/k", GH_TOKEN="personal", PT_REPO="o/r"), \
             mock.patch.object(ghapp, "installation_token", side_effect=ghapp.AppError("not installed")):
            with self.assertRaises(gh.GhError):
                gh.token()

    def test_review_token_prefers_the_review_app_then_the_reviewing_account(self):
        with clean_env(PT_REVIEW_APP_ID="2", PT_REVIEW_APP_KEY_FILE="/k", PT_REVIEW_TOKEN="pat", PT_REPO="o/r"), \
             mock.patch.object(ghapp, "installation_token", return_value="ghs_review") as minted:
            self.assertEqual(gh.review_token(), "ghs_review")
        minted.assert_called_once_with("review", "o/r")
        with clean_env(PT_REVIEW_TOKEN="pat"):
            self.assertEqual(gh.review_token(), "pat")
        with clean_env():
            self.assertIsNone(gh.review_token())
            self.assertIsNone(gh.review_login())

    def test_personal_token_without_app_uses_get_user(self):
        with clean_env(GH_TOKEN="t"), mock.patch.object(gh, "api", return_value={"login": "geeera"}) as api:
            self.assertEqual(gh.token_login(), "geeera")
        api.assert_called_once_with("user", auth=None)


class OwnerTokenTest(IsolatedTest):
    def api(self, personal_login):
        def answer(path, method="GET", fields=None, auth=None):
            if path == "repos/o/r":
                return {"owner": {"login": "geeera"}}
            if path == "user":
                self.assertEqual(auth, "personal")
                return {"login": personal_login}
            raise AssertionError(path)
        return answer

    def test_same_account_mode_uses_the_agents_token(self):
        with clean_env(GH_TOKEN="personal"), mock.patch("shutil.which", return_value=None):
            self.assertEqual(gh.owner_token("o/r"), "personal")

    def test_app_mode_uses_the_owners_personal_token(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/k", GH_TOKEN="personal"), \
             mock.patch.object(gh, "api", side_effect=self.api("GeeEra")):
            self.assertEqual(gh.owner_token("o/r"), "personal")

    def test_app_mode_refuses_a_token_that_is_not_the_owners(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="/k", GH_TOKEN="personal"), \
             mock.patch.object(gh, "api", side_effect=self.api("someone-else")):
            with self.assertRaisesRegex(gh.GhError, "geeera's own token"):
                gh.owner_token("o/r")


if __name__ == "__main__":
    unittest.main()
