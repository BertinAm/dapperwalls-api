import json

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from dashboard.models import DashboardSettings, LoginAttempt, VisitEvent
from dashboard.tracking import classify, parse_user_agent
from enquiries.models import Enquiry
from enquiries.tests.test_api import SECRET, payload

UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
PROXY = {"HTTP_X_PROXY_TOKEN": SECRET, "HTTP_X_CLIENT_IP": "198.51.100.7", "HTTP_USER_AGENT": UA}

BASE = dict(
    PROXY_SHARED_SECRET=SECRET,
    REQUIRE_PROXY_TOKEN=True,
    ADMIN_LOGIN_MAX_FAILURES=3,
    ENQUIRY_NOTIFY_EMAIL=["support@dapperwalls.co.uk"],
    ENQUIRIES_PER_EMAIL_PER_DAY=100,
    RATE_LIMIT_PER_HOUR=100,
    RATE_LIMIT_PER_DAY=100,
)


@override_settings(**BASE)
class DashboardTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        User = get_user_model()
        self.staff = User.objects.create_user("emmanuel", "e@example.com", "a-long-Pass-phrase-42", is_staff=True)
        self.customer = User.objects.create_user("someone", "s@example.com", "a-long-Pass-phrase-42")

    def api(self, method, url, data=None, csrf=None, **extra):
        headers = {**PROXY, **extra}
        if csrf:
            headers["HTTP_X_CSRFTOKEN"] = csrf
        body = json.dumps(data) if data is not None else None
        return getattr(self.client, method)(url, body, content_type="application/json", **headers)

    def csrf(self):
        return self.api("get", "/api/admin/session/").json()["csrfToken"]

    def sign_in(self, username="emmanuel", password="a-long-Pass-phrase-42"):
        token = self.csrf()
        response = self.api("post", "/api/admin/login/", {"username": username, "password": password}, csrf=token)
        return response, response.json().get("csrfToken", token)

    def collect(self, data, **extra):
        return self.api("post", "/api/collect/", data, **extra)


class CollectTests(DashboardTestCase):
    def test_pageview_records_channel_location_and_device(self):
        r = self.collect(
            {"type": "pageview", "path": "/", "referrer": "https://www.google.com/", "utm_campaign": ""},
            HTTP_X_CLIENT_COUNTRY="GB",
            HTTP_X_CLIENT_CITY="Chester",
        )
        self.assertEqual(r.status_code, 204)
        v = VisitEvent.objects.get()
        self.assertEqual((v.channel, v.source, v.country, v.city, v.device), ("Search", "google", "GB", "Chester", "Mobile"))
        self.assertEqual(len(v.visitor), 32)

    def test_gclid_is_google_ads(self):
        self.collect({"type": "pageview", "path": "/", "referrer": "", "gclid": True})
        self.assertEqual(VisitEvent.objects.get().channel, "Google Ads")

    def test_internal_navigation_is_not_an_entry(self):
        self.collect({"type": "pageview", "path": "/privacy/", "referrer": "https://dapperwalls.co.uk/"})
        self.assertEqual(VisitEvent.objects.get().channel, "")

    def test_events_are_allow_listed(self):
        self.collect({"type": "event", "name": "quote_open", "path": "/"})
        self.collect({"type": "event", "name": "<script>", "path": "/"})
        self.assertEqual(list(VisitEvent.objects.values_list("name", flat=True)), ["quote_open"])

    def test_bots_admin_pages_and_bad_input_are_ignored(self):
        self.collect({"type": "pageview", "path": "/"}, HTTP_USER_AGENT="Googlebot/2.1")
        self.collect({"type": "pageview", "path": "/admin/members/"})
        self.collect({"type": "pageview", "path": "https://evil.example/"})
        self.collect({"type": "pageview", "path": 42})
        self.api("post", "/api/collect/", None)
        self.assertEqual(VisitEvent.objects.count(), 0)

    def test_without_the_proxy_token_nothing_is_stored(self):
        r = self.client.post("/api/collect/", json.dumps({"type": "pageview", "path": "/"}), content_type="application/json", HTTP_USER_AGENT=UA)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(VisitEvent.objects.count(), 0)

    def test_fake_location_headers_without_token_are_ignored(self):
        from dashboard.tracking import get_location
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_X_CLIENT_COUNTRY="FR", REMOTE_ADDR="203.0.113.9")
        self.assertEqual(get_location(request), ("", "", ""))

    def test_enquiry_gets_the_visitors_source(self):
        self.collect({"type": "pageview", "path": "/", "referrer": "https://www.instagram.com/"}, HTTP_X_CLIENT_COUNTRY="GB")
        r = self.api("post", "/api/enquiries/", payload(), HTTP_X_CLIENT_COUNTRY="GB", HTTP_X_CLIENT_CITY="Chester")
        self.assertEqual(r.status_code, 201)
        e = Enquiry.objects.get()
        self.assertEqual((e.channel, e.source, e.country, e.city), ("Social", "Instagram", "GB", "Chester"))
        self.assertIn("Found us:  Social (Instagram)", mail.outbox[0].body)


class ClassifyTests(TestCase):
    def test_channels(self):
        self.assertEqual(classify("https://l.facebook.com/x")[0], "Social")
        self.assertEqual(classify("https://www.checkatrade.com/trades/x")[:2], ("Referral", "checkatrade.com"))
        self.assertEqual(classify("", "newsletter", "email")[0], "Email")
        self.assertEqual(classify("", "google", "cpc")[0], "Google Ads")
        self.assertEqual(classify("")[0], "Direct")

    def test_user_agent(self):
        self.assertEqual(parse_user_agent(UA), ("Mobile", "Safari", "iOS"))
        desktop = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
        self.assertEqual(parse_user_agent(desktop), ("Desktop", "Chrome", "Windows"))


class AuthTests(DashboardTestCase):
    def test_staff_can_sign_in_and_out(self):
        r, token = self.sign_in()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.api("get", "/api/admin/session/").json()["authenticated"])
        self.assertEqual(self.api("get", "/api/admin/overview/").status_code, 200)
        self.assertEqual(LoginAttempt.objects.get().outcome, "success")
        self.api("post", "/api/admin/logout/", {}, csrf=token)
        self.assertEqual(self.api("get", "/api/admin/overview/").status_code, 401)

    def test_login_needs_csrf(self):
        r = self.api("post", "/api/admin/login/", {"username": "emmanuel", "password": "a-long-Pass-phrase-42"})
        self.assertEqual(r.status_code, 403)

    def test_non_staff_cannot_use_the_dashboard(self):
        r, _ = self.sign_in("someone")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.api("get", "/api/admin/enquiries/").status_code, 401)
        self.assertEqual(LoginAttempt.objects.get().outcome, "not-staff")

    def test_repeated_failures_are_blocked_and_recorded(self):
        for _ in range(3):
            self.assertEqual(self.sign_in(password="wrong")[0].status_code, 400)
        r, _ = self.sign_in()  # even the right password is refused while locked
        self.assertEqual(r.status_code, 429)
        self.assertEqual(LoginAttempt.objects.filter(outcome="failed").count(), 3)
        self.assertEqual(LoginAttempt.objects.filter(outcome="blocked").count(), 1)

    def test_hostile_login_input_is_sanitised(self):
        token = self.csrf()
        cases = [
            {"username": "emmanuel\x00\r\nFAKE LOG LINE", "password": "x"},
            {"username": "' OR 1=1 --", "password": "x"},
            {"username": "<script>alert(1)</script>", "password": "x"},
            {"username": ["emmanuel"], "password": "a-long-Pass-phrase-42"},
            {"username": "emmanuel", "password": {"$ne": ""}},
            {"username": "emmanuel", "password": "x" * 5000},
            {"username": "", "password": ""},
        ]
        for body in cases:
            r = self.api("post", "/api/admin/login/", body, csrf=token)
            self.assertIn(r.status_code, (400, 429), body)
            self.assertNotIn("sessionid", r.cookies)
        for name in LoginAttempt.objects.values_list("username", flat=True):
            self.assertRegex(name, r"^([\w.@+-]*|\(invalid\))$")

    def test_api_refuses_calls_that_bypass_the_proxy(self):
        self.sign_in()
        r = self.client.get("/api/admin/overview/")
        self.assertEqual(r.status_code, 403)

    def test_responses_are_not_cached_or_indexed(self):
        r = self.api("get", "/api/admin/overview/")
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertIn("noindex", r["X-Robots-Tag"])

    @override_settings(ADMIN_URL="manage-dw/")
    def test_django_admin_logins_are_recorded(self):
        client = Client()
        client.post("/manage-dw/login/", {"username": "emmanuel", "password": "nope"})
        self.assertEqual(LoginAttempt.objects.get().area, "django-admin")


class LoginExtrasTests(DashboardTestCase):
    def test_sign_in_with_email(self):
        r, _ = self.sign_in("E@Example.com")
        self.assertEqual(r.status_code, 200)

    def test_remember_me_keeps_the_session(self):
        token = self.csrf()
        self.api("post", "/api/admin/login/", {"username": "emmanuel", "password": "a-long-Pass-phrase-42", "remember": True}, csrf=token)
        self.assertGreater(self.client.session.get_expiry_age(), 13 * 86400)
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_without_remember_me_the_session_ends_with_the_browser(self):
        self.sign_in()
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_lockout_says_how_long_to_wait(self):
        for _ in range(3):
            self.sign_in(password="wrong")
        r, _ = self.sign_in()
        self.assertEqual(r.status_code, 429)
        self.assertTrue(0 < r.json()["retry_after"] <= 15 * 60)
        self.assertEqual(r["Retry-After"], str(r.json()["retry_after"]))


class PasswordResetTests(DashboardTestCase):
    def request_reset(self, email):
        return self.api("post", "/api/admin/password-reset/", {"email": email}, csrf=self.csrf())

    def link(self):
        import re

        return re.search(r"uid=([\w-]+)&token=([\w-]+)", mail.outbox[-1].body).groups()

    def test_full_reset_flow(self):
        self.assertEqual(self.request_reset("e@example.com").status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["e@example.com"])
        self.assertIn("/admin/reset/?uid=", mail.outbox[-1].body)
        html = mail.outbox[-1].alternatives[0][0]
        self.assertIn("checkatrade.com/trades/dapperwallsltd", html)
        self.assertIn("Walls that tell a story.", html)
        uid, token = self.link()
        r = self.api("post", "/api/admin/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "Brand-new-pass-77"}, csrf=self.csrf())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sign_in(password="Brand-new-pass-77")[0].status_code, 200)
        # The link only works once.
        again = self.api("post", "/api/admin/password-reset/confirm/", {"uid": uid, "token": token, "new_password": "Another-pass-88x"}, csrf=self.csrf())
        self.assertEqual(again.status_code, 400)

    def test_unknown_and_non_staff_addresses_look_the_same_and_send_nothing(self):
        a = self.request_reset("nobody@example.com")
        b = self.request_reset("s@example.com")  # exists, but not staff
        self.assertEqual((a.status_code, a.json()), (b.status_code, b.json()))
        self.assertEqual(len(mail.outbox), 0)

    def test_bad_tokens_and_weak_passwords_are_refused(self):
        self.request_reset("e@example.com")
        uid, token = self.link()
        csrf = self.csrf()
        url = "/api/admin/password-reset/confirm/"
        self.assertEqual(self.api("post", url, {"uid": uid, "token": "nope", "new_password": "Brand-new-pass-77"}, csrf=csrf).status_code, 400)
        self.assertEqual(self.api("post", url, {"uid": "!!", "token": token, "new_password": "Brand-new-pass-77"}, csrf=csrf).status_code, 400)
        self.assertEqual(self.api("post", url, {"uid": uid, "token": token, "new_password": "123"}, csrf=csrf).status_code, 400)
        self.assertEqual(self.api("post", url, {"uid": uid, "token": token, "new_password": ["x"]}, csrf=csrf).status_code, 400)

    def test_requests_are_rate_limited_per_address(self):
        for _ in range(5):
            self.request_reset("e@example.com")
        self.assertEqual(len(mail.outbox), 3)


class MembersTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        self.a = Enquiry.objects.create(**{k: v for k, v in payload().items() if k not in ("website", "elapsed_ms")})
        self.b = Enquiry.objects.create(
            **{**{k: v for k, v in payload(first_name="Grace", email="grace@example.com").items() if k not in ("website", "elapsed_ms")}},
            status=Enquiry.Status.SPAM,
        )
        _, self.token = self.sign_in()

    def test_list_search_and_spam_hidden_by_default(self):
        data = self.api("get", "/api/admin/enquiries/").json()
        self.assertEqual([r["reference"] for r in data["results"]], [self.a.reference])
        self.assertEqual(self.api("get", "/api/admin/enquiries/?status=all").json()["count"], 2)
        self.assertEqual(self.api("get", "/api/admin/enquiries/?q=grace&status=all").json()["count"], 1)

    def test_update_status_and_notes(self):
        url = f"/api/admin/enquiries/{self.a.reference}/"
        r = self.api("patch", url, {"status": "quoted", "notes": "Visit on Friday"}, csrf=self.token)
        self.assertEqual(r.status_code, 200)
        self.a.refresh_from_db()
        self.assertEqual((self.a.status, self.a.notes), ("quoted", "Visit on Friday"))
        self.assertEqual(self.api("patch", url, {"status": "bogus"}, csrf=self.token).status_code, 400)
        self.assertEqual(self.api("patch", url, {"status": "won"}).status_code, 403)  # no CSRF

    def test_read_and_unread(self):
        data = self.api("get", "/api/admin/enquiries/?read=unread").json()
        self.assertEqual((data["count"], data["unread"]), (1, 1))
        url = f"/api/admin/enquiries/{self.a.reference}/"
        r = self.api("patch", url, {"read": True}, csrf=self.token)
        self.assertTrue(r.json()["enquiry"]["read"])
        self.assertEqual(self.api("get", "/api/admin/enquiries/?read=unread").json()["count"], 0)
        self.assertEqual(self.api("patch", url, {"read": "yes"}, csrf=self.token).status_code, 400)
        self.api("patch", url, {"read": False}, csrf=self.token)
        self.assertEqual(self.api("get", "/api/admin/enquiries/").json()["unread"], 1)

    def test_delete(self):
        url = f"/api/admin/enquiries/{self.a.reference}/"
        self.assertEqual(self.api("delete", url, csrf=self.token).status_code, 200)
        self.assertFalse(Enquiry.objects.filter(pk=self.a.pk).exists())

    def test_csv_export_neutralises_formulas(self):
        self.a.message = "=HYPERLINK(\"http://evil\")"
        self.a.save()
        r = self.api("get", "/api/admin/enquiries/export/")
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        body = r.content.decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", body)
        self.assertIn(self.a.reference, body)


class SettingsTests(DashboardTestCase):
    def setUp(self):
        super().setUp()
        _, self.token = self.sign_in()

    def test_notification_recipients_come_from_settings(self):
        r = self.api("put", "/api/admin/settings/", {"notify_emails": ["owner@example.com", "dapperwalls0@gmail.com"]}, csrf=self.token)
        self.assertEqual(r.status_code, 200)
        self.api("post", "/api/enquiries/", payload())
        self.assertEqual(mail.outbox[0].to, ["owner@example.com", "dapperwalls0@gmail.com"])

    def test_bad_values_are_rejected(self):
        r = self.api("put", "/api/admin/settings/", {"notify_emails": ["not-an-email"], "analytics_retention_days": 5}, csrf=self.token)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(DashboardSettings.load().notify_list, [])

    def test_change_password(self):
        bad = self.api("post", "/api/admin/password/", {"current_password": "x", "new_password": "Another-long-pass-99"}, csrf=self.token)
        self.assertEqual(bad.status_code, 400)
        weak = self.api("post", "/api/admin/password/", {"current_password": "a-long-Pass-phrase-42", "new_password": "123"}, csrf=self.token)
        self.assertEqual(weak.status_code, 400)
        ok = self.api("post", "/api/admin/password/", {"current_password": "a-long-Pass-phrase-42", "new_password": "Another-long-pass-99"}, csrf=self.token)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.api("get", "/api/admin/settings/").status_code, 200)  # still signed in


class OverviewTests(DashboardTestCase):
    def test_overview_shape(self):
        self.collect({"type": "pageview", "path": "/", "referrer": "https://www.google.com/"}, HTTP_X_CLIENT_COUNTRY="GB")
        self.collect({"type": "event", "name": "quote_open", "path": "/"})
        self.api("post", "/api/enquiries/", payload())
        self.sign_in()
        data = self.api("get", "/api/admin/overview/?days=7").json()
        self.assertEqual(data["kpis"]["visitors"]["value"], 1)
        self.assertEqual(data["kpis"]["enquiries"]["value"], 1)
        self.assertEqual(data["kpis"]["quote_opens"]["value"], 1)
        self.assertEqual(data["kpis"]["conversion"]["value"], 100.0)
        self.assertEqual(len(data["daily"]), 7)
        self.assertEqual(data["channels"][0]["channel"], "Search")
        self.assertEqual(data["countries"][0], {"country": "GB", "value": 1})
        self.assertEqual(data["logins"]["totals"]["success"], 1)
        self.assertEqual([f["value"] for f in data["funnel"]], [1, 1, 1, 0])
