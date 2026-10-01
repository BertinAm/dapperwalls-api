"""Attack-style tests for the hardening added in the security audit."""
import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from enquiries.models import Enquiry
from enquiries.validation import clean_text

from .test_api import SECRET, payload

BASE = dict(
    PROXY_SHARED_SECRET=SECRET,
    TRUSTED_IP_HEADER="HTTP_CF_CONNECTING_IP",
    CORS_ALLOWED_ORIGINS=["https://dapperwalls.co.uk"],
    RATE_LIMIT_PER_HOUR=5,
    RATE_LIMIT_PER_DAY=20,
    ENQUIRY_MIN_ELAPSED_MS=2500,
    ENQUIRIES_PER_EMAIL_PER_DAY=100,
    ENQUIRY_NOTIFY_EMAIL=["support@dapperwalls.co.uk"],
    DEFAULT_FROM_EMAIL="DapperWalls <support@dapperwalls.co.uk>",
    REQUIRE_PROXY_TOKEN=False,
)


@override_settings(**BASE)
class SecurityTestCase(TestCase):
    url = "/api/enquiries/"

    def setUp(self):
        cache.clear()

    def post(self, data=None, **headers):
        return self.client.post(
            self.url, json.dumps(payload() if data is None else data), content_type="application/json", **headers
        )


class ProxyTokenRequiredTests(SecurityTestCase):
    @override_settings(REQUIRE_PROXY_TOKEN=True)
    def test_direct_call_without_token_is_refused(self):
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Enquiry.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(REQUIRE_PROXY_TOKEN=True)
    def test_wrong_token_is_refused(self):
        self.assertEqual(self.post(HTTP_X_PROXY_TOKEN="guess").status_code, 403)

    @override_settings(REQUIRE_PROXY_TOKEN=True)
    def test_call_through_the_proxy_is_accepted(self):
        self.assertEqual(self.post(HTTP_X_PROXY_TOKEN=SECRET, HTTP_X_CLIENT_IP="198.51.100.20").status_code, 201)


class SpoofedIpTests(SecurityTestCase):
    def test_fake_cloudflare_header_from_outside_cloudflare_cannot_dodge_the_limit(self):
        # Attacker connects to the origin directly and invents a new IP each time.
        for i in range(5):
            self.assertEqual(
                self.post(REMOTE_ADDR="203.0.113.5", HTTP_CF_CONNECTING_IP=f"198.51.100.{i}").status_code, 201
            )
        blocked = self.post(REMOTE_ADDR="203.0.113.5", HTTP_CF_CONNECTING_IP="198.51.100.99")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(Enquiry.objects.latest("created_at").ip_address, "203.0.113.5")

    def test_cloudflare_header_trusted_from_cloudflare(self):
        self.post(REMOTE_ADDR="162.158.1.1", HTTP_CF_CONNECTING_IP="198.51.100.30")
        self.assertEqual(Enquiry.objects.get().ip_address, "198.51.100.30")


class SanitisingTests(SecurityTestCase):
    def test_header_injection_in_name_is_rejected(self):
        response = self.post(payload(first_name="Ada\r\nBcc: victim@example.com"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("first_name", response.json()["errors"])
        self.assertEqual(len(mail.outbox), 0)

    def test_names_with_markup_links_or_digits_are_rejected(self):
        for bad in ("<b>Ada</b>", "http://spam.example", "Ada123", "=SUM(A1)", "Ada; DROP TABLE"):
            with self.subTest(bad=bad):
                response = self.post(payload(last_name=bad))
                self.assertEqual(response.status_code, 400)

    def test_real_names_are_accepted(self):
        for good in ("O'Donovan", "Smith-Jones", "Nwafor", "Ogunmokun", "José", "St. John", "Müller"):
            with self.subTest(good=good):
                cache.clear()
                self.assertEqual(self.post(payload(last_name=good, email=f"{abs(hash(good))}@example.com")).status_code, 201)

    def test_phone_and_postcode_character_sets(self):
        self.assertEqual(self.post(payload(phone="call me maybe")).status_code, 400)
        self.assertEqual(self.post(payload(postcode="<CH1>")).status_code, 400)
        cache.clear()
        self.assertEqual(self.post(payload(phone="+44 (0)7700 900123", postcode="ch1 2ab")).status_code, 201)

    def test_invisible_and_control_characters_are_removed(self):
        sneaky = "Two rooms‮ to paint​ please\x00\x1b[31m red"
        self.post(payload(message=sneaky, first_name="A​da"))
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.first_name, "Ada")
        self.assertEqual(enquiry.message, "Two rooms to paint please[31m red")

    def test_message_line_endings_and_blank_runs_are_normalised(self):
        self.post(payload(message="Line one\r\n\r\n\r\n\r\nLine two   with   spaces"))
        self.assertEqual(Enquiry.objects.get().message, "Line one\n\nLine two with spaces")

    def test_email_is_lowercased(self):
        self.post(payload(email="Ada.Lovelace@Example.COM"))
        self.assertEqual(Enquiry.objects.get().email, "ada.lovelace@example.com")

    def test_clean_text_single_line_collapses_breaks(self):
        self.assertEqual(clean_text("  a\n\tb  "), "a b")

    def test_too_many_services_rejected(self):
        self.assertEqual(self.post(payload(services=["Not sure yet"] * 11)).status_code, 400)


class SpamTests(SecurityTestCase):
    def test_link_stuffed_message_is_dropped_silently(self):
        body = "Great deals http://a.example http://b.example www.c.example https://d.example"
        response = self.post(payload(message=body))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Enquiry.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_couple_of_links_are_fine(self):
        self.post(payload(message="Like this one: https://example.com/room and www.example.com/wall"))
        self.assertEqual(Enquiry.objects.count(), 1)

    @override_settings(ENQUIRIES_PER_EMAIL_PER_DAY=3, RATE_LIMIT_PER_HOUR=100)
    def test_one_address_cannot_be_flooded_with_confirmations(self):
        for i in range(3):
            self.assertEqual(self.post(payload(), REMOTE_ADDR=f"198.51.100.{i}").status_code, 201)
        blocked = self.post(payload(email="ADA@example.com"), REMOTE_ADDR="198.51.100.50")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(Enquiry.objects.count(), 3)


@override_settings(**BASE, ADMIN_LOGIN_MAX_FAILURES=3, ADMIN_LOGIN_LOCKOUT_SECONDS=900)
class AdminLoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        get_user_model().objects.create_superuser("owner", "owner@example.com", "correct-horse-battery")
        self.login_url = reverse("admin:login")

    def attempt(self, password):
        return self.client.post(self.login_url, {"username": "owner", "password": password, "next": "/"})

    def test_locks_out_after_repeated_failures(self):
        for _ in range(3):
            self.assertEqual(self.attempt("wrong").status_code, 200)
        self.assertEqual(self.attempt("wrong").status_code, 429)
        # Even the right password is refused while locked out.
        self.assertEqual(self.attempt("correct-horse-battery").status_code, 429)

    def test_success_resets_the_counter(self):
        self.attempt("wrong")
        self.attempt("wrong")
        self.assertEqual(self.attempt("correct-horse-battery").status_code, 302)
        self.client.logout()
        for _ in range(3):
            self.assertEqual(self.attempt("wrong").status_code, 200)
