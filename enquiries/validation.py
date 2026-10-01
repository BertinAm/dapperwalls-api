import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import Enquiry

TEXT_FIELDS = {
    # name: (required, max_length)
    "first_name": (True, 80),
    "last_name": (True, 80),
    "email": (True, 254),
    "phone": (False, 40),
    "postcode": (False, 12),
}
MESSAGE_MIN, MESSAGE_MAX = 10, 5000
# More links than this in a message is treated as spam (see views._looks_like_bot).
MAX_LINKS = 3

# Names: letters (any language), spaces and the punctuation real names use.
NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '’.\-])*$")
PHONE_RE = re.compile(r"^\+?[0-9 ()\-]{7,20}$")
POSTCODE_RE = re.compile(r"^[A-Za-z0-9 ]{2,10}$")
LINK_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)


def clean_text(value, multiline=False):
    """Normalise user text before it is stored, shown in the admin or emailed.

    * Unicode NFC, so look-alike encodings compare equal.
    * Removes control characters (NUL, escape codes, etc.) and invisible
      formatting characters such as bidi overrides and zero-width spaces,
      which can disguise text in emails and the admin.
    * Single-line fields lose line breaks (they end up in email headers).
      Multi-line text keeps \\n, with \\r\\n normalised and runs of blank
      lines capped.
    """
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for ch in value:
        if ch == "\n" and multiline:
            out.append(ch)
        elif ch in "\t\n":
            out.append(" ")
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            out.append(ch)
    value = "".join(out)
    if multiline:
        value = "\n".join(re.sub(r"[  ]+", " ", line).strip() for line in value.split("\n"))
        value = re.sub(r"\n{3,}", "\n\n", value)
    else:
        value = re.sub(r"\s+", " ", value)
    return value.strip()


def count_links(text):
    return len(LINK_RE.findall(text))


def validate_enquiry(data):
    """Validate and sanitise the decoded JSON body. Returns (cleaned_data, errors)."""
    errors = {}
    cleaned = {}

    def add(field, message):
        errors.setdefault(field, []).append(message)

    def text(field, multiline=False):
        value = data.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            add(field, "Enter text.")
            return None
        return clean_text(value, multiline=multiline)

    for field, (required, max_length) in TEXT_FIELDS.items():
        value = text(field)
        if value is None:
            continue
        if required and not value:
            add(field, "This field is required.")
        elif len(value) > max_length:
            add(field, f"Keep this to {max_length} characters or fewer.")
        cleaned[field] = value

    for field in ("first_name", "last_name"):
        value = cleaned.get(field)
        if value and field not in errors and not NAME_RE.match(value):
            add(field, "Use letters only (spaces, hyphens and apostrophes are fine).")

    if cleaned.get("email") and "email" not in errors:
        try:
            validate_email(cleaned["email"])
        except ValidationError:
            add("email", "Enter a valid email address.")
        else:
            cleaned["email"] = cleaned["email"].lower()

    if cleaned.get("phone") and "phone" not in errors and not PHONE_RE.match(cleaned["phone"]):
        add("phone", "Enter a phone number using digits (spaces, + and brackets are fine).")

    if cleaned.get("postcode") and "postcode" not in errors:
        if not POSTCODE_RE.match(cleaned["postcode"]):
            add("postcode", "Enter a postcode using letters and numbers.")
        cleaned["postcode"] = cleaned["postcode"].upper()

    property_type = text("property_type")
    if property_type is not None:
        property_type = property_type or Enquiry.PropertyType.RESIDENTIAL
        if property_type not in Enquiry.PropertyType.values:
            add("property_type", "Choose Residential or Commercial.")
        cleaned["property_type"] = property_type

    services = data.get("services") or []
    if not isinstance(services, list) or len(services) > 10 or not all(isinstance(s, str) for s in services):
        add("services", "Choose from the listed services.")
    else:
        unknown = [s for s in services if s not in Enquiry.SERVICE_CHOICES]
        if unknown:
            add("services", "Choose from the listed services.")
        cleaned["services"] = [s for s in Enquiry.SERVICE_CHOICES if s in services]

    message = text("message", multiline=True)
    if message is not None:
        if not message:
            add("message", "This field is required.")
        elif len(message) < MESSAGE_MIN:
            add("message", f"Tell us a little more (at least {MESSAGE_MIN} characters).")
        elif len(message) > MESSAGE_MAX:
            add("message", f"Keep this to {MESSAGE_MAX} characters or fewer.")
        cleaned["message"] = message

    return cleaned, errors
