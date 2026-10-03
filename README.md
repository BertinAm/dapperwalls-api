# DapperWalls enquiry API

A small Django app with one job: receive the enquiry form from
[dapperwalls.co.uk](https://dapperwalls.co.uk), store it, and email the team
and the customer. Staff read enquiries in the Django admin.

This repository is public. Configuration and secrets come from environment
variables and are never committed. See `.env.example` for every variable.

## Endpoints

### `POST /api/enquiries/`

JSON body (`Content-Type: application/json`, at most 32 KB):

| Field | Type | Rules |
| --- | --- | --- |
| `first_name` | string | required, up to 80 |
| `last_name` | string | required, up to 80 |
| `email` | string | required, valid email, up to 254 |
| `phone` | string | optional, up to 40 |
| `postcode` | string | optional, up to 12 (stored upper-case) |
| `property_type` | string | `Residential` (default) or `Commercial` |
| `services` | string[] | optional; each one of `Painting & decorating`, `Venetian plaster or another decorative finish`, `Wallpaper installation`, `Not sure yet` |
| `message` | string | required, 10 to 5000 characters |
| `website` | string | honeypot, must be empty |
| `elapsed_ms` | integer | milliseconds since the form opened |

Responses:

- `201 {"ok": true, "reference": "DW-XXXXXX"}` on success.
- `400 {"ok": false, "errors": {"field": ["message"]}}` on validation errors (`__all__` for general errors).
- `403` if an `Origin` header is present and not allowed (and no valid proxy token).
- `405` for other methods, `413` if the body is over 32 KB, `415` if not JSON.
- `429` when rate limited (default 5 attempts per IP per hour, 20 per day).

A filled honeypot, `elapsed_ms` under 2500, or a missing `elapsed_ms` gets a
`201` with a made-up reference, but nothing is saved or emailed.

### `GET /api/health/`

Returns `{"ok": true}` without touching the database. Use it for uptime checks.

### Owner dashboard (`/api/admin/*`)

JSON API for the dashboard at `dapperwalls.co.uk/admin/` (staff accounts only,
Django session cookie plus CSRF token, through the Worker in production):

| Route | What it does |
|---|---|
| `GET admin/session/` | Who is signed in, and a CSRF token |
| `POST admin/login/` | Sign in with username or email; `remember` keeps the session for `DASHBOARD_REMEMBER_DAYS` (14) |
| `POST admin/logout/` | Sign out |
| `POST admin/password-reset/`, `admin/password-reset/confirm/` | Emailed one-hour reset link |
| `GET admin/overview/?days=7\|30\|90\|365` | Every number and chart on the dashboard |
| `GET admin/enquiries/` | Members and Messages: search, filters, paging, read/unread |
| `GET/PATCH/DELETE admin/enquiries/<ref>/` | One enquiry: status, notes, read |
| `GET admin/enquiries/export/` | CSV of the current filter |
| `GET/PUT admin/settings/`, `POST admin/password/` | Profile, notification emails, retention, password |

### `POST /api/collect/`

Cookieless visit counting from the public site (page views, quote form opens,
contact and social clicks). Stores the page, where the visit came from, rough
location from Cloudflare and device type. No IP address is kept: visitors are
a keyed hash of IP and browser that changes daily. Bots, `/admin` pages and
browsers sending Global Privacy Control are ignored. Always answers 204.

## How the proxy fits in

The website calls `/api/*` on its own domain. A Cloudflare Pages Function
forwards those requests to this API and adds two headers:

- `X-Proxy-Token`: the shared secret, which must equal `PROXY_SHARED_SECRET` here.
- `X-Client-IP`: the visitor's IP address.

`X-Client-IP` is only trusted when the token matches. Otherwise the client IP
comes from the header named by `TRUSTED_IP_HEADER` (default
`CF-Connecting-IP`), then the socket address. Rate limits key on that IP.

## Local development

Requires Python 3.10 to 3.12 in production; newer versions work locally.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env        # then set DEBUG=True and a SECRET_KEY
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

With `DEBUG=True`, emails print to the console, and the Next.js dev server at
`http://localhost:3000` can call `http://127.0.0.1:8000/api/...` directly (CORS
is allowed for it). The admin is at `/manage-dw/` unless `ADMIN_URL` says
otherwise.

Try it:

```sh
curl -s http://127.0.0.1:8000/api/health/
curl -s -X POST http://127.0.0.1:8000/api/enquiries/ \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Ada","last_name":"Lovelace","email":"ada@example.com","message":"Hallway repaint please.","elapsed_ms":5000}'
```

## Tests

```sh
python manage.py test
```

The suite includes a run of `manage.py check --deploy` with production-like
settings.

## Deploying (cPanel "Setup Python App")

One-off setup:

1. Create the MySQL database and user in cPanel.
2. Create the Python app. Application root is this repository's directory,
   startup file `passenger_wsgi.py`, entry point `application`.
3. Add the environment variables from `.env.example` on the app's screen. At
   minimum: `SECRET_KEY`, `DATABASE_URL`, `PROXY_SHARED_SECRET`, and the
   `EMAIL_*` settings.
4. Add a cron job every 10 minutes that runs, inside the app's virtualenv and
   directory, `python manage.py send_pending_enquiry_emails`. It retries any
   emails that failed to send. Add a second, nightly, for
   `python manage.py prune_analytics`, which deletes visit data older than the
   retention set in the dashboard.
5. Create an admin user with `python manage.py createsuperuser`.

Each deploy, from the app's virtualenv in the application root:

```sh
git pull && pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput && python manage.py createcachetable
```

Then click **Restart** in Setup Python App, and confirm:

```sh
python manage.py check --deploy
```

The only expected warning is `security.W021` (HSTS preload not enabled). That
is deliberate; set `SECURE_HSTS_PRELOAD=True` only if you decide to submit the
domain to the browser preload list.

Local demo data for the dashboard (refuses to run unless `DEBUG=True`):

```sh
python manage.py seed_demo_analytics --clear --days 90
```

## Security

- **Proxy only in production.** `POST /api/enquiries/` requires the Cloudflare Worker's `X-Proxy-Token` (`REQUIRE_PROXY_TOKEN`, on by default when `DEBUG=False`). Direct calls, including to the server's IP, get 403.
- **Client IP.** `X-Client-IP` is trusted only with a valid proxy token. `CF-Connecting-IP` is trusted only when the connection comes from a Cloudflare edge address, so it can't be forged by hitting the origin directly.
- **Rate limits.** Per IP per hour and per day, plus at most `ENQUIRIES_PER_EMAIL_PER_DAY` (default 3) enquiries per email address, so the form can't flood someone's inbox with confirmations.
- **Input sanitising.** NFC normalisation; control and invisible formatting characters removed; line breaks stripped from single-line fields (they go into email headers); names limited to letters and name punctuation; phone and postcode character sets; messages with more than 3 links are dropped silently as spam. All output is HTML-escaped in templates; CSV export escapes spreadsheet formulas.
- **Bots.** Honeypot field plus a minimum fill time; both get a fake success.
- **Admin.** Custom `ADMIN_URL`; 5 failed logins from one IP lock that IP out for 15 minutes; sessions last 8 hours and end when the browser closes; secure, HTTP-only cookies.
- **Dashboard.** Staff only, proxy token required, CSRF on every change. Login input is validated before it reaches the database; 5 failures per IP (or 10 per account) pause sign-in for 15 minutes and the response says how long. Every attempt (dashboard and Django admin) is stored in `LoginAttempt` and charted. Password reset links last one hour, work once, and the request answers the same whether or not the email is an admin's. Responses are `no-store` and `noindex`.
