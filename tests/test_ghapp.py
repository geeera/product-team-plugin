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
            "PT_REVIEW_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "PT_REPO", "PT_OWNER_TOKEN")
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
        gh._cli_token_cache = ""  # as if no `gh` CLI: the test machine's own login must never leak in
        self.addCleanup(ghapp.reset)
        self.addCleanup(setattr, gh, "_token_cache", None)
        self.addCleanup(setattr, gh, "_cli_token_cache", None)


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

    def test_inline_key_base64_and_raw_both_sign_through_a_pipe(self):
        pem = Path(self.key).read_text()
        for inline in (base64.b64encode(pem.encode()).decode(), pem, pem.replace("\n", "\\n")):
            with self.subTest(form=inline[:12]), clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=inline), \
                 mock.patch.object(tempfile, "mkstemp", side_effect=AssertionError("key written to disk")):
                self.assertTrue(self.verify(ghapp.jwt(ghapp.config("team"))))

    def test_key_file_path_expands_the_home_directory(self):
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE="~/app.pem", HOME=self.tmp.name):
            self.assertTrue(self.verify(ghapp.jwt(ghapp.config("team"))))

    def test_a_key_that_is_not_rsa_is_an_actionable_error(self):
        junk = os.path.join(self.tmp.name, "junk.pem")
        Path(junk).write_text("-----BEGIN PRIVATE KEY-----\nnot a key\n-----END PRIVATE KEY-----\n")
        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY_FILE=junk):
            with self.assertRaisesRegex(ghapp.AppError, "could not sign"):
                ghapp.jwt(ghapp.config("team"))


class InlineKeyFileTest(IsolatedTest):
    PEM = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"

    def test_inline_key_goes_to_openssl_through_a_passed_pipe_that_is_closed_after(self):
        seen = {}

        def fake_sign(message, path, kind, pass_fds=()):
            fd = pass_fds[0]
            seen.update(path=path, fd=fd, body=os.read(fd, 65536).decode(), is_pipe=stat.S_ISFIFO(os.fstat(fd).st_mode))
            return b"sig"

        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=base64.b64encode(self.PEM.encode()).decode()), \
             mock.patch.object(ghapp, "_openssl_sign", side_effect=fake_sign):
            ghapp.sign(b"x", ghapp.config("team"))
        self.assertEqual(seen["path"], f"/dev/fd/{seen['fd']}")
        self.assertTrue(seen["is_pipe"])
        self.assertEqual(seen["body"], self.PEM)
        with self.assertRaises(OSError):
            os.fstat(seen["fd"])

    def test_pipe_is_closed_when_signing_fails(self):
        seen = {}

        def failing_sign(message, path, kind, pass_fds=()):
            seen["fd"] = pass_fds[0]
            raise ghapp.AppError("openssl could not sign")

        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=self.PEM), \
             mock.patch.object(ghapp, "_openssl_sign", side_effect=failing_sign):
            with self.assertRaises(ghapp.AppError):
                ghapp.sign(b"x", ghapp.config("team"))
        with self.assertRaises(OSError):
            os.fstat(seen["fd"])

    def test_openssl_never_inherits_the_identity_secrets(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs["env"])
            return mock.Mock(returncode=0, stdout=b"sig", stderr=b"")

        with clean_env(PT_TEAM_APP_ID="1", PT_TEAM_APP_KEY=self.PEM, PT_OWNER_TOKEN="o", PT_REVIEW_APP_KEY="r"), \
             mock.patch.object(ghapp.subprocess, "run", side_effect=fake_run):
            ghapp.sign(b"x", ghapp.config("team"))
        self.assertFalse({"PT_TEAM_APP_KEY", "PT_OWNER_TOKEN", "PT_REVIEW_APP_KEY"} & set(seen))
        self.assertIn("PT_TEAM_APP_ID", seen)

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
             mock.patch.object(ghapp, "installation_token", return_value="ghs_review") as minted, \
             mock.patch.object(ghapp, "identity", return_value={"login": "acme-review[bot]"}):
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


TEAM_BOT = {"login": "acme-team[bot]", "id": 1, "name": "acme-team[bot]", "email": "1+acme-team[bot]@x"}
APP_ENV = {"PT_TEAM_APP_ID": "1", "PT_TEAM_APP_KEY_FILE": "/k", "PT_REPO": "o/r"}


def github(logins):
    """gh.api that knows the repository owner and which login each credential resolves to."""
    def answer(path, method="GET", fields=None, auth=None):
        if path == "repos/o/r":
            return {"owner": {"login": "geeera"}}
        if path == "user":
            if auth not in logins:
                raise gh.GhError("GET user → HTTP 403")
            return {"login": logins[auth]}
        raise AssertionError(path)
    return answer


class OwnerTokenTest(IsolatedTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(ghapp, "identity", return_value=TEAM_BOT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_same_account_mode_needs_no_separate_token(self):
        with clean_env(GH_TOKEN="personal"):
            self.assertIsNone(gh.owner_token("o/r"))

    def test_app_mode_uses_only_pt_owner_token(self):
        with clean_env(PT_OWNER_TOKEN="owner-pat", GH_TOKEN="gh-pat", **APP_ENV), \
             mock.patch.object(gh, "api", side_effect=github({"owner-pat": "GeeEra", "gh-pat": "geeera"})):
            self.assertEqual(gh.owner_token("o/r"), "owner-pat")

    def test_app_mode_never_borrows_gh_token_or_gh_auth(self):
        gh._cli_token_cache = "cli-pat"
        with clean_env(GH_TOKEN="gh-pat", GITHUB_TOKEN="gha", **APP_ENV), \
             mock.patch.object(gh, "api", side_effect=github({"gh-pat": "geeera", "cli-pat": "geeera"})):
            with self.assertRaisesRegex(gh.GhError, "PT_OWNER_TOKEN"):
                gh.owner_token("o/r")

    def test_app_mode_refuses_a_pt_owner_token_that_is_not_the_owners(self):
        with clean_env(PT_OWNER_TOKEN="other", **APP_ENV), \
             mock.patch.object(gh, "api", side_effect=github({"other": "someone-else"})):
            with self.assertRaisesRegex(gh.GhError, "geeera's own token"):
                gh.owner_token("o/r")


class SameAccountFailClosedTest(IsolatedTest):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(ghapp, "identity", return_value=TEAM_BOT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_app_mode_without_personal_credentials_is_not_same_account(self):
        with clean_env(**APP_ENV), mock.patch.object(gh, "api", side_effect=github({})):
            self.assertFalse(gh.acts_as_owner("o/r"))

    def test_any_owner_credential_in_the_session_fails_closed(self):
        for name in ("PT_OWNER_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
            with self.subTest(name=name), clean_env(**{name: "pat"}, **APP_ENV), \
                 mock.patch.object(gh, "api", side_effect=github({"pat": "geeera"})):
                self.assertTrue(gh.acts_as_owner("o/r"))
        gh._cli_token_cache = "cli-pat"
        with clean_env(**APP_ENV), mock.patch.object(gh, "api", side_effect=github({"cli-pat": "geeera"})):
            self.assertTrue(gh.acts_as_owner("o/r"))

    def test_credentials_of_someone_else_or_actions_do_not_count(self):
        with clean_env(GH_TOKEN="other", GITHUB_TOKEN="actions", **APP_ENV), \
             mock.patch.object(gh, "api", side_effect=github({"other": "someone-else"})):
            self.assertFalse(gh.acts_as_owner("o/r"))


class DistinctBotsTest(IsolatedTest):
    def test_review_app_resolving_to_the_team_bot_is_refused(self):
        # e.g. PT_TEAM_APP_ID=123 and PT_REVIEW_APP_ID=Iv1.abc: different strings, one app.
        with clean_env(PT_REVIEW_APP_ID="Iv1.abc", PT_REVIEW_APP_KEY_FILE="/k", **APP_ENV), \
             mock.patch.object(ghapp, "identity", return_value=TEAM_BOT), \
             mock.patch.object(ghapp, "installation_token", return_value="t") as minted:
            with self.assertRaisesRegex(gh.GhError, "same app"):
                gh.review_login()
            with self.assertRaisesRegex(gh.GhError, "same app"):
                gh.review_token()
        minted.assert_not_called()


class ChildEnvTest(unittest.TestCase):
    def test_identity_secrets_are_stripped(self):
        with clean_env(PT_TEAM_APP_KEY="k", PT_TEAM_APP_KEY_FILE="/k", PT_REVIEW_APP_KEY="r", PT_OWNER_TOKEN="o",
                       PT_REVIEW_TOKEN="p", PT_TEAM_APP_ID="1", PATH="/bin"):
            env = gh.child_env(EXTRA="1")
        self.assertEqual({k for k in env if k.startswith("PT_")}, {"PT_TEAM_APP_ID"})
        self.assertEqual((env["EXTRA"], env["PATH"]), ("1", "/bin"))


if __name__ == "__main__":
    unittest.main()
