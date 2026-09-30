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
   emails that failed to send.
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
