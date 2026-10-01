import json
import re
import smtplib
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from enquiries.models import Enquiry

URL = "/api/enquiries/"
SECRET = "test-proxy-secret-0123456789abcdefghij"
REFERENCE_RE = re.compile(r"^DW-[A-Z2-9]{6}$")


def payload(**overrides):
    data = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "phone": "07700 900123",
        "postcode": "sw1a 1aa",
        "property_type": "Residential",
        "services": ["Painting & decorating", "Wallpaper installation"],
        "message": "Two bedrooms and a hallway need repainting.",
        "website": "",
        "elapsed_ms": 12000,
    }
    data.update(overrides)
    return data


@override_settings(
    PROXY_SHARED_SECRET=SECRET,
    TRUSTED_IP_HEADER="HTTP_CF_CONNECTING_IP",
    CORS_ALLOWED_ORIGINS=["https://dapperwalls.co.uk", "http://localhost:3000"],
    RATE_LIMIT_PER_HOUR=5,
    RATE_LIMIT_PER_DAY=20,
    ENQUIRY_MIN_ELAPSED_MS=2500,
    ENQUIRIES_PER_EMAIL_PER_DAY=100,
    ENQUIRY_NOTIFY_EMAIL=["support@dapperwalls.co.uk"],
    DEFAULT_FROM_EMAIL="DapperWalls <support@dapperwalls.co.uk>",
)
class EnquiryApiTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def post(self, data=None, **headers):
        body = data if isinstance(data, (str, bytes)) else json.dumps(payload() if data is None else data)
        return self.client.post(URL, body, content_type="application/json", **headers)


class HappyPathTests(EnquiryApiTestCase):
    def test_saves_enquiry_and_sends_both_emails(self):
        response = self.post(HTTP_USER_AGENT="Mozilla/5.0 " + "x" * 400, REMOTE_ADDR="203.0.113.9")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertRegex(body["reference"], REFERENCE_RE)

        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.reference, body["reference"])
        self.assertEqual(enquiry.full_name, "Ada Lovelace")
        self.assertEqual(enquiry.postcode, "SW1A 1AA")
        self.assertEqual(enquiry.services, ["Painting & decorating", "Wallpaper installation"])
        self.assertEqual(enquiry.status, Enquiry.Status.NEW)
        self.assertEqual(enquiry.ip_address, "203.0.113.9")
        self.assertEqual(len(enquiry.user_agent), 300)
        self.assertIsNotNone(enquiry.notification_sent_at)
        self.assertIsNotNone(enquiry.confirmation_sent_at)

        self.assertEqual(len(mail.outbox), 2)
        notification, confirmation = mail.outbox
        self.assertEqual(notification.to, ["support@dapperwalls.co.uk"])
        self.assertEqual(notification.subject, f"New enquiry {enquiry.reference}: Ada Lovelace")
        self.assertEqual(notification.reply_to, ["ada@example.com"])
        self.assertIn("Two bedrooms", notification.body)
        self.assertEqual(notification.alternatives[0][1], "text/html")

        self.assertEqual(confirmation.to, ["ada@example.com"])
        self.assertEqual(confirmation.from_email, "DapperWalls <support@dapperwalls.co.uk>")
        self.assertEqual(confirmation.reply_to, ["support@dapperwalls.co.uk"])
        self.assertIn(enquiry.reference, confirmation.subject)
        self.assertIn(enquiry.reference, confirmation.body)
        self.assertIn("The DapperWalls team", confirmation.body)
        self.assertNotIn("—", confirmation.body)

    def test_optional_fields_can_be_omitted(self):
        data = {
            "first_name": "Bo",
            "last_name": "Li",
            "email": "bo@example.com",
            "message": "Venetian plaster in a lounge please.",
            "elapsed_ms": 5000,
        }
        response = self.post(data)
        self.assertEqual(response.status_code, 201)
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.property_type, "Residential")
        self.assertEqual(enquiry.services, [])

    def test_html_email_escapes_customer_input(self):
        self.post(payload(message="Hello <script>x</script> there, two rooms please."))
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    @override_settings(ENQUIRY_NOTIFY_EMAIL=["support@dapperwalls.co.uk", "owner@example.com"])
    def test_notification_goes_to_every_configured_address(self):
        self.post()
        notification = next(m for m in mail.outbox if m.subject.startswith("New enquiry"))
        self.assertEqual(notification.to, ["support@dapperwalls.co.uk", "owner@example.com"])

    def test_url_without_trailing_slash_works(self):
        response = self.client.post("/api/enquiries", json.dumps(payload()), content_type="application/json")
        self.assertEqual(response.status_code, 201)


class ValidationTests(EnquiryApiTestCase):
    def test_missing_required_fields(self):
        response = self.post({"elapsed_ms": 5000})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(set(body["errors"]), {"first_name", "last_name", "email", "message"})
        self.assertFalse(Enquiry.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_field_rules(self):
        response = self.post(
            payload(
                first_name="x" * 81,
                email="not-an-email",
                phone="1" * 41,
                postcode="x" * 13,
                property_type="Castle",
                services=["Painting & decorating", "Roofing"],
                message="short",
            )
        )
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        for field in ("first_name", "email", "phone", "postcode", "property_type", "services", "message"):
            self.assertIn(field, errors)
            self.assertIsInstance(errors[field], list)

    def test_message_too_long(self):
        response = self.post(payload(message="x" * 5001))
        self.assertIn("message", response.json()["errors"])

    def test_wrong_types(self):
        response = self.post(payload(first_name=123, services="Painting & decorating"))
        errors = response.json()["errors"]
        self.assertIn("first_name", errors)
        self.assertIn("services", errors)

    def test_invalid_json_and_non_object(self):
        self.assertEqual(self.post("{not json").status_code, 400)
        response = self.post("[1, 2]")
        self.assertEqual(response.status_code, 400)
        self.assertIn("__all__", response.json()["errors"])

    def test_not_json_is_415(self):
        response = self.client.post(URL, {"first_name": "Ada"})
        self.assertEqual(response.status_code, 415)
        self.assertFalse(response.json()["ok"])

    def test_other_methods_are_405(self):
        for method in ("get", "put", "delete", "patch"):
            response = getattr(self.client, method)(URL)
            self.assertEqual(response.status_code, 405, method)
            self.assertEqual(response["Allow"], "POST")

    def test_body_over_32kb_is_413(self):
        response = self.post(payload(message="x" * 4000, notes="y" * 33000))
        self.assertEqual(response.status_code, 413)


class SpamTests(EnquiryApiTestCase):
    def assert_silently_dropped(self, response):
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertRegex(body["reference"], REFERENCE_RE)
        self.assertFalse(Enquiry.objects.exists())
        self.assertEqual(mail.outbox, [])

    def test_honeypot(self):
        self.assert_silently_dropped(self.post(payload(website="http://spam.example")))

    def test_too_fast(self):
        self.assert_silently_dropped(self.post(payload(elapsed_ms=1200)))

    def test_missing_elapsed(self):
        data = payload()
        del data["elapsed_ms"]
        self.assert_silently_dropped(self.post(data))

    def test_bot_with_invalid_fields_still_gets_201(self):
        self.assert_silently_dropped(self.post({"website": "x", "email": "nope"}))


class RateLimitTests(EnquiryApiTestCase):
    def test_hourly_limit_per_ip(self):
        for _ in range(5):
            self.assertEqual(self.post(REMOTE_ADDR="198.51.100.1").status_code, 201)
        response = self.post(REMOTE_ADDR="198.51.100.1")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json(),
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        "Too many enquiries. Please try again later or email support@dapperwalls.co.uk."
                    ]
                },
            },
        )
        self.assertEqual(Enquiry.objects.count(), 5)
        # A different visitor is unaffected.
        self.assertEqual(self.post(REMOTE_ADDR="198.51.100.2").status_code, 201)

    def test_bot_and_invalid_attempts_count(self):
        for _ in range(3):
            self.post(payload(website="spam"), REMOTE_ADDR="198.51.100.3")
        for _ in range(2):
            self.post({"elapsed_ms": 5000}, REMOTE_ADDR="198.51.100.3")
        self.assertEqual(self.post(REMOTE_ADDR="198.51.100.3").status_code, 429)

    @override_settings(RATE_LIMIT_PER_HOUR=100, RATE_LIMIT_PER_DAY=3)
    def test_daily_limit(self):
        for _ in range(3):
            self.assertEqual(self.post(REMOTE_ADDR="198.51.100.4").status_code, 201)
        self.assertEqual(self.post(REMOTE_ADDR="198.51.100.4").status_code, 429)

    def test_limit_keys_on_proxied_client_ip(self):
        for i in range(5):
            self.post(HTTP_X_PROXY_TOKEN=SECRET, HTTP_X_CLIENT_IP="192.0.2.50", HTTP_CF_CONNECTING_IP="172.64.0.1")
        blocked = self.post(HTTP_X_PROXY_TOKEN=SECRET, HTTP_X_CLIENT_IP="192.0.2.50", HTTP_CF_CONNECTING_IP="172.64.0.1")
        self.assertEqual(blocked.status_code, 429)
        # Another visitor through the same proxy address is fine.
        other = self.post(HTTP_X_PROXY_TOKEN=SECRET, HTTP_X_CLIENT_IP="192.0.2.51", HTTP_CF_CONNECTING_IP="172.64.0.1")
        self.assertEqual(other.status_code, 201)


class ClientIpTests(EnquiryApiTestCase):
    def saved_ip(self, **headers):
        self.assertEqual(self.post(**headers).status_code, 201)
        return Enquiry.objects.latest("created_at").ip_address

    def test_valid_proxy_token_uses_x_client_ip(self):
        ip = self.saved_ip(
            HTTP_X_PROXY_TOKEN=SECRET,
            HTTP_X_CLIENT_IP="192.0.2.10",
            HTTP_CF_CONNECTING_IP="172.64.0.1",
            REMOTE_ADDR="10.0.0.1",
        )
        self.assertEqual(ip, "192.0.2.10")

    def test_invalid_token_ignores_x_client_ip(self):
        ip = self.saved_ip(
            HTTP_X_PROXY_TOKEN="wrong",
            HTTP_X_CLIENT_IP="192.0.2.10",
            HTTP_CF_CONNECTING_IP="198.51.100.77",
            REMOTE_ADDR="172.64.0.1",
        )
        self.assertEqual(ip, "198.51.100.77")

    def test_missing_token_ignores_x_client_ip(self):
        ip = self.saved_ip(HTTP_X_CLIENT_IP="192.0.2.10", REMOTE_ADDR="10.0.0.2")
        self.assertEqual(ip, "10.0.0.2")

    @override_settings(PROXY_SHARED_SECRET="")
    def test_unset_secret_never_trusts_x_client_ip(self):
        ip = self.saved_ip(HTTP_X_PROXY_TOKEN="", HTTP_X_CLIENT_IP="192.0.2.10", REMOTE_ADDR="10.0.0.3")
        self.assertEqual(ip, "10.0.0.3")

    def test_garbage_ip_falls_through(self):
        ip = self.saved_ip(
            HTTP_X_PROXY_TOKEN=SECRET,
            HTTP_X_CLIENT_IP="not-an-ip",
            HTTP_CF_CONNECTING_IP="2001:db8::1, 10.0.0.9",
            REMOTE_ADDR="162.158.10.10",
        )
        self.assertEqual(ip, "2001:db8::1")

    @override_settings(TRUSTED_IP_HEADER="")
    def test_trusted_header_can_be_disabled(self):
        ip = self.saved_ip(HTTP_CF_CONNECTING_IP="172.64.0.1", REMOTE_ADDR="10.0.0.4")
        self.assertEqual(ip, "10.0.0.4")


class OriginTests(EnquiryApiTestCase):
    def test_unknown_origin_rejected(self):
        response = self.post(HTTP_ORIGIN="https://evil.example")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])
        self.assertFalse(Enquiry.objects.exists())

    def test_unknown_origin_allowed_with_proxy_token(self):
        response = self.post(HTTP_ORIGIN="https://preview.dapperwalls.pages.dev", HTTP_X_PROXY_TOKEN=SECRET)
        self.assertEqual(response.status_code, 201)

    def test_allowed_origin_and_no_origin(self):
        response = self.post(HTTP_ORIGIN="https://dapperwalls.co.uk")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://dapperwalls.co.uk")
        self.assertEqual(self.post().status_code, 201)

    def test_cors_preflight_for_dev_origin(self):
        response = self.client.options(
            URL,
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertIn("POST", response["Access-Control-Allow-Methods"])
        self.assertIn("content-type", response["Access-Control-Allow-Headers"])


class EmailFailureTests(EnquiryApiTestCase):
    def test_email_failure_still_201_and_retry_command_sends_later(self):
        with mock.patch(
            "django.core.mail.backends.locmem.EmailBackend.send_messages",
            side_effect=smtplib.SMTPException("server down"),
        ):
            response = self.post()
        self.assertEqual(response.status_code, 201)
        enquiry = Enquiry.objects.get()
        self.assertEqual(enquiry.reference, response.json()["reference"])
        self.assertIsNone(enquiry.notification_sent_at)
        self.assertIsNone(enquiry.confirmation_sent_at)
        self.assertEqual(mail.outbox, [])

        # Too new: the command leaves it for the next run.
        call_command("send_pending_enquiry_emails", verbosity=0)
        self.assertEqual(mail.outbox, [])

        Enquiry.objects.update(created_at=timezone.now() - timedelta(minutes=10))
        call_command("send_pending_enquiry_emails", verbosity=0)
        enquiry.refresh_from_db()
        self.assertIsNotNone(enquiry.notification_sent_at)
        self.assertIsNotNone(enquiry.confirmation_sent_at)
        self.assertEqual(len(mail.outbox), 2)

        # Nothing left to send.
        call_command("send_pending_enquiry_emails", verbosity=0)
        self.assertEqual(len(mail.outbox), 2)

    def test_retry_only_sends_the_missing_email(self):
        self.post()
        Enquiry.objects.update(confirmation_sent_at=None, created_at=timezone.now() - timedelta(minutes=10))
        mail.outbox.clear()
        call_command("send_pending_enquiry_emails", verbosity=0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ada@example.com"])

    def test_retry_skips_spam_and_old_enquiries(self):
        self.post()
        self.post(payload(email="old@example.com"))
        Enquiry.objects.update(notification_sent_at=None, confirmation_sent_at=None)
        spam, old = Enquiry.objects.order_by("created_at")
        Enquiry.objects.filter(pk=spam.pk).update(
            status=Enquiry.Status.SPAM, created_at=timezone.now() - timedelta(hours=1)
        )
        Enquiry.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))
        mail.outbox.clear()
        call_command("send_pending_enquiry_emails", verbosity=0)
        self.assertEqual(mail.outbox, [])


class HealthTests(TestCase):
    def test_health(self):
        with self.assertNumQueries(0):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_health_rejects_post(self):
        self.assertEqual(self.client.post("/api/health/").status_code, 405)


class AdminTests(EnquiryApiTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_superuser("owner", "owner@example.com", "pw-for-tests-only")
        self.client.force_login(self.user)
        self.post()
        Enquiry.objects.create(
            first_name="=HYPERLINK(1)", last_name="Test", email="b@example.com", message="Spreadsheet formula test."
        )

    def test_changelist_and_change_page_load(self):
        changelist = reverse("admin:enquiries_enquiry_changelist")
        self.assertEqual(self.client.get(changelist).status_code, 200)
        self.assertEqual(self.client.get(changelist, {"q": "Lovelace"}).status_code, 200)
        enquiry = Enquiry.objects.first()
        change = reverse("admin:enquiries_enquiry_change", args=[enquiry.pk])
        self.assertEqual(self.client.get(change).status_code, 200)

    def run_action(self, action):
        return self.client.post(
            reverse("admin:enquiries_enquiry_changelist"),
            {"action": action, "_selected_action": list(Enquiry.objects.values_list("pk", flat=True))},
        )

    def test_mark_actions(self):
        self.run_action("mark_contacted")
        self.assertEqual(set(Enquiry.objects.values_list("status", flat=True)), {"contacted"})
        self.run_action("mark_spam")
        self.assertEqual(set(Enquiry.objects.values_list("status", flat=True)), {"spam"})

    def test_csv_export(self):
        response = self.run_action("export_csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        content = response.content.decode("utf-8-sig")
        self.assertIn("reference,created_at,status", content)
        self.assertIn("Painting & decorating; Wallpaper installation", content)
        self.assertIn("'=HYPERLINK(1)", content)
