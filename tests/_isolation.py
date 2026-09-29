"""Imported first by every test module: no test may read the machine's real GitHub credentials.

Credential variables are removed from the process environment, and the one function that asks the `gh` CLI for
a token is replaced by a tripwire, so a test that forgets to mock that path fails instead of reading a real token.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import gh  # noqa: E402

CREDENTIAL_ENV = re.compile(r"^(GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|GITHUB_ENTERPRISE_TOKEN|PT_OWNER_TOKEN|"
                            r"PT_REVIEW_TOKEN|PT_(TEAM|REVIEW)_APP_\w+)$")


class RealCredentialAccess(AssertionError):
    pass


def _tripwire():
    raise RealCredentialAccess("a test reached the real `gh auth token`: mock shutil.which or gh._run_gh_auth_token")


for name in [k for k in os.environ if CREDENTIAL_ENV.match(k)]:
    del os.environ[name]
gh._run_gh_auth_token = _tripwire
gh._token_cache = None
gh._cli_token_cache = None
