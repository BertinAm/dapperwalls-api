"""Numbers for the dashboard's overview page.

Aggregated in Python rather than SQL: volumes are small (a local trade
site), and MySQL on shared hosting often has no timezone tables, which
breaks the database's date truncation with USE_TZ.
"""
from collections import Counter
from datetime import datetime, time, timedelta

from django.utils import timezone

from enquiries.models import Enquiry

from .models import LoginAttempt, VisitEvent
from .tracking import CHANNELS

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
EVENT_LABELS = {
    "quote_open": "Opened the quote form",
    "email_click": "Clicked the email address",
    "phone_click": "Clicked the phone number",
    "social_click": "Clicked a social link",
    "checkatrade_click": "Clicked Checkatrade",
}


def _window(days, now=None):
    now = now or timezone.localtime()
    end_day = now.date()
    start_day = end_day - timedelta(days=days - 1)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_day, time.min), tz)
    return start, now, start_day, end_day


def _pct_change(current, previous):
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _kpi(current, previous):
    return {"value": current, "previous": previous, "change": _pct_change(current, previous)}


def _top(counter, n, key):
    return [{key: k, "value": v} for k, v in counter.most_common(n) if k]


def overview(days):
    start, now, start_day, end_day = _window(days)
    prev_start = start - timedelta(days=days)

    events = list(
        VisitEvent.objects.filter(created_at__gte=start).values(
            "created_at", "kind", "name", "label", "visitor", "path", "channel", "source",
            "referrer_host", "utm_source", "utm_campaign", "country", "region", "city",
            "device", "browser", "os",
        )
    )
    enquiries = list(
        Enquiry.objects.filter(created_at__gte=start)
        .exclude(status=Enquiry.Status.SPAM)
        .values("created_at", "status", "services", "property_type", "channel", "source", "utm_campaign", "country", "city")
    )
    logins = list(
        LoginAttempt.objects.filter(created_at__gte=start).values(
            "created_at", "area", "outcome", "username", "ip_address", "country", "city"
        )
    )

    pageviews = [e for e in events if e["kind"] == VisitEvent.Kind.PAGEVIEW]
    entries = [e for e in pageviews if e["channel"]]
    actions = [e for e in events if e["kind"] == VisitEvent.Kind.EVENT]
    visitors = {e["visitor"] for e in pageviews}

    # One row per visitor: their first (entry) page view decides channel etc.
    first_entry = {}
    for e in sorted(entries, key=lambda r: r["created_at"]):
        first_entry.setdefault(e["visitor"], e)

    # --- Daily series ---------------------------------------------------------
    day_list = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]
    daily = {d: {"visitors": set(), "pageviews": 0, "enquiries": 0, "quote_opens": 0} for d in day_list}
    weekday = [{"day": d, "visitors": set(), "enquiries": 0} for d in WEEKDAYS]
    hours = [{"hour": h, "pageviews": 0} for h in range(24)]
    for e in pageviews:
        local = timezone.localtime(e["created_at"])
        row = daily.get(local.date())
        if row is not None:
            row["visitors"].add(e["visitor"])
            row["pageviews"] += 1
        weekday[local.weekday()]["visitors"].add(e["visitor"])
        hours[local.hour]["pageviews"] += 1
    for e in actions:
        if e["name"] == "quote_open":
            row = daily.get(timezone.localtime(e["created_at"]).date())
            if row is not None:
                row["quote_opens"] += 1
    for q in enquiries:
        local = timezone.localtime(q["created_at"])
        row = daily.get(local.date())
        if row is not None:
            row["enquiries"] += 1
        weekday[local.weekday()]["enquiries"] += 1

    daily_series = [
        {
            "date": d.isoformat(),
            "visitors": len(v["visitors"]),
            "pageviews": v["pageviews"],
            "enquiries": v["enquiries"],
            "quote_opens": v["quote_opens"],
        }
        for d, v in daily.items()
    ]
    for w in weekday:
        w["visitors"] = len(w["visitors"])

    # --- Where visitors come from ---------------------------------------------
    channel_visitors = Counter(e["channel"] for e in first_entry.values())
    channel_enquiries = Counter(q["channel"] or "Unknown" for q in enquiries)
    channels = [
        {"channel": c, "visitors": channel_visitors.get(c, 0), "enquiries": channel_enquiries.get(c, 0)}
        for c in CHANNELS
        if channel_visitors.get(c) or channel_enquiries.get(c)
    ]
    referrers = _top(Counter(e["referrer_host"] for e in first_entry.values()), 10, "host")
    sources = _top(Counter(e["source"] for e in first_entry.values()), 10, "source")

    campaign_visitors = Counter(e["utm_campaign"] for e in first_entry.values() if e["utm_campaign"])
    campaign_enquiries = Counter(q["utm_campaign"] for q in enquiries if q["utm_campaign"])
    campaigns = [
        {"campaign": c, "visitors": v, "enquiries": campaign_enquiries.get(c, 0)}
        for c, v in campaign_visitors.most_common(10)
    ]

    visitor_place = {}
    for e in sorted(pageviews, key=lambda r: r["created_at"]):
        visitor_place.setdefault(e["visitor"], e)
    countries = _top(Counter(e["country"] for e in visitor_place.values()), 10, "country")
    cities = [
        {"city": city, "country": country, "value": n}
        for (city, country), n in Counter(
            (e["city"], e["country"]) for e in visitor_place.values() if e["city"]
        ).most_common(10)
    ]
    devices = _top(Counter(e["device"] for e in visitor_place.values()), 5, "device")
    browsers = _top(Counter(e["browser"] for e in visitor_place.values()), 6, "browser")
    operating_systems = _top(Counter(e["os"] for e in visitor_place.values()), 6, "os")
    pages = _top(Counter(e["path"] for e in pageviews), 8, "path")

    # --- Actions and funnel ------------------------------------------------------
    action_counts = Counter(e["name"] for e in actions)
    action_rows = [
        {"name": n, "label": EVENT_LABELS[n], "value": action_counts.get(n, 0)} for n in EVENT_LABELS
    ]
    social_clicks = _top(Counter(e["label"] for e in actions if e["name"] == "social_click"), 6, "network")
    quote_visitors = {e["visitor"] for e in actions if e["name"] == "quote_open"}
    won = sum(1 for q in enquiries if q["status"] == Enquiry.Status.WON)
    funnel = [
        {"step": "Visited the site", "value": len(visitors)},
        {"step": "Opened the quote form", "value": len(quote_visitors)},
        {"step": "Sent an enquiry", "value": len(enquiries)},
        {"step": "Became a job", "value": won},
    ]

    # --- Enquiries ------------------------------------------------------------------
    status_labels = dict(Enquiry.Status.choices)
    status_counts = Counter(q["status"] for q in enquiries)
    enquiry_status = [
        {"status": s, "label": status_labels[s], "value": status_counts.get(s, 0)}
        for s in Enquiry.Status.values
        if s != Enquiry.Status.SPAM
    ]
    service_counts = Counter(s for q in enquiries for s in (q["services"] or []))
    enquiry_services = [{"service": s, "value": service_counts.get(s, 0)} for s in Enquiry.SERVICE_CHOICES]
    property_types = _top(Counter(q["property_type"] for q in enquiries), 3, "type")

    # --- Logins ---------------------------------------------------------------------
    login_daily = {d: {"success": 0, "failed": 0, "blocked": 0} for d in day_list}
    for a in logins:
        row = login_daily.get(timezone.localtime(a["created_at"]).date())
        if row is None:
            continue
        if a["outcome"] == LoginAttempt.Outcome.SUCCESS:
            row["success"] += 1
        elif a["outcome"] == LoginAttempt.Outcome.BLOCKED:
            row["blocked"] += 1
        else:
            row["failed"] += 1
    outcome_labels = dict(LoginAttempt.Outcome.choices)
    area_labels = dict(LoginAttempt.Area.choices)
    recent_logins = [
        {
            "time": a["created_at"].isoformat(),
            "username": a["username"],
            "outcome": a["outcome"],
            "outcome_label": outcome_labels.get(a["outcome"], a["outcome"]),
            "area": area_labels.get(a["area"], a["area"]),
            "ip": _mask_ip(a["ip_address"]),
            "country": a["country"],
            "city": a["city"],
        }
        for a in sorted(logins, key=lambda r: r["created_at"], reverse=True)[:12]
    ]
    login_totals = Counter(
        "success" if a["outcome"] == LoginAttempt.Outcome.SUCCESS else
        "blocked" if a["outcome"] == LoginAttempt.Outcome.BLOCKED else "failed"
        for a in logins
    )

    recent_visits = [
        {
            "time": e["created_at"].isoformat(),
            "channel": e["channel"],
            "source": e["source"],
            "campaign": e["utm_campaign"],
            "country": e["country"],
            "city": e["city"],
            "device": e["device"],
            "path": e["path"],
        }
        for e in sorted(entries, key=lambda r: r["created_at"], reverse=True)[:15]
    ]

    # --- KPIs vs the previous period ---------------------------------------------
    prev = VisitEvent.objects.filter(created_at__gte=prev_start, created_at__lt=start)
    prev_pageviews = prev.filter(kind=VisitEvent.Kind.PAGEVIEW)
    prev_visitors = prev_pageviews.values("visitor").distinct().count()
    prev_enquiries = (
        Enquiry.objects.filter(created_at__gte=prev_start, created_at__lt=start)
        .exclude(status=Enquiry.Status.SPAM)
        .count()
    )
    prev_quote = prev.filter(kind=VisitEvent.Kind.EVENT, name="quote_open").count()
    prev_failed = (
        LoginAttempt.objects.filter(created_at__gte=prev_start, created_at__lt=start)
        .exclude(outcome=LoginAttempt.Outcome.SUCCESS)
        .count()
    )
    conversion = round(len(enquiries) / len(visitors) * 100, 1) if visitors else 0
    prev_conversion = round(prev_enquiries / prev_visitors * 100, 1) if prev_visitors else 0
    failed_logins = login_totals.get("failed", 0) + login_totals.get("blocked", 0)

    return {
        "range": {"days": days, "start": start_day.isoformat(), "end": end_day.isoformat()},
        "kpis": {
            "visitors": _kpi(len(visitors), prev_visitors),
            "pageviews": _kpi(len(pageviews), prev_pageviews.count()),
            "enquiries": _kpi(len(enquiries), prev_enquiries),
            "conversion": _kpi(conversion, prev_conversion),
            "quote_opens": _kpi(action_counts.get("quote_open", 0), prev_quote),
            "failed_logins": _kpi(failed_logins, prev_failed),
        },
        "daily": daily_series,
        "weekday": weekday,
        "hours": hours,
        "channels": channels,
        "sources": sources,
        "referrers": referrers,
        "campaigns": campaigns,
        "countries": countries,
        "cities": cities,
        "devices": devices,
        "browsers": browsers,
        "operating_systems": operating_systems,
        "pages": pages,
        "actions": action_rows,
        "social_clicks": social_clicks,
        "funnel": funnel,
        "enquiry_status": enquiry_status,
        "enquiry_services": enquiry_services,
        "property_types": property_types,
        "logins": {
            "daily": [{"date": d.isoformat(), **v} for d, v in login_daily.items()],
            "totals": {k: login_totals.get(k, 0) for k in ("success", "failed", "blocked")},
            "recent": recent_logins,
        },
        "recent_visits": recent_visits,
        "all_time": {
            "enquiries": Enquiry.objects.exclude(status=Enquiry.Status.SPAM).count(),
            "new_enquiries": Enquiry.objects.filter(status=Enquiry.Status.NEW).count(),
        },
    }


def _mask_ip(ip):
    """Show enough of an IP to spot repeats, not the whole address."""
    if not ip:
        return ""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + ":…"
    parts = ip.split(".")
    return ".".join(parts[:2] + ["x", "x"]) if len(parts) == 4 else ip

