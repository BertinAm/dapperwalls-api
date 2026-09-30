import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from enquiries.checks import proxy_secret_check

BASE_DIR = Path(settings.BASE_DIR)


def prod_env(tmp_db, **overrides):
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR"}
    }
    env.update(
        {
            "DEBUG": "False",
            "SECRET_KEY": secrets.token_urlsafe(50)[:50],
            "PROXY_SHARED_SECRET": secrets.token_urlsafe(40),
            "DATABASE_URL": f"sqlite:///{tmp_db}",
            # Keep a developer's local .env from leaking into these runs.
            "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        }
    )
    env.update(overrides)
    return env


def run_manage(*args, env):
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


class DeployCheckTests(SimpleTestCase):
    def setUp(self):
        self.tmp_db = BASE_DIR / f".test-deploy-check-{os.getpid()}.sqlite3"
        self.addCleanup(lambda: self.tmp_db.unlink(missing_ok=True))

    def test_check_deploy_passes_with_production_env(self):
        result = run_manage("check", "--deploy", "--fail-level", "ERROR", env=prod_env(self.tmp_db))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # The only acceptable warning is the HSTS preload opt-in (security.W021).
        warnings = [line for line in result.stderr.splitlines() if "?: (" in line]
        self.assertTrue(all("security.W021" in line for line in warnings), result.stderr)

    def test_check_fails_without_proxy_secret(self):
        result = run_manage("check", env=prod_env(self.tmp_db, PROXY_SHARED_SECRET="too-short"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("enquiries.E001", result.stderr)

    def test_settings_refuse_to_load_without_secret_key(self):
        result = run_manage("check", env=prod_env(self.tmp_db, SECRET_KEY=""))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SECRET_KEY must be set", result.stderr)


class ProxySecretCheckTests(SimpleTestCase):
    @override_settings(IS_PRODUCTION=True, PROXY_SHARED_SECRET="")
    def test_missing_secret_is_an_error(self):
        self.assertEqual([e.id for e in proxy_secret_check(None)], ["enquiries.E001"])

    @override_settings(IS_PRODUCTION=True, PROXY_SHARED_SECRET="x" * 31)
    def test_short_secret_is_an_error(self):
        self.assertEqual(len(proxy_secret_check(None)), 1)

    @override_settings(IS_PRODUCTION=True, PROXY_SHARED_SECRET="x" * 32)
    def test_long_secret_is_fine(self):
        self.assertEqual(proxy_secret_check(None), [])

    @override_settings(IS_PRODUCTION=False, PROXY_SHARED_SECRET="")
    def test_not_checked_in_development(self):
        self.assertEqual(proxy_secret_check(None), [])
