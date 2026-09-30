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


def validate_enquiry(data):
    """Validate the decoded JSON body. Returns (cleaned_data, errors)."""
    errors = {}
    cleaned = {}

    def add(field, message):
        errors.setdefault(field, []).append(message)

    def text(field):
        value = data.get(field, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            add(field, "Enter text.")
            return None
        return value.strip()

    for field, (required, max_length) in TEXT_FIELDS.items():
        value = text(field)
        if value is None:
            continue
        if required and not value:
            add(field, "This field is required.")
        elif len(value) > max_length:
            add(field, f"Keep this to {max_length} characters or fewer.")
        cleaned[field] = value

    if cleaned.get("email") and "email" not in errors:
        try:
            validate_email(cleaned["email"])
        except ValidationError:
            add("email", "Enter a valid email address.")
    if cleaned.get("postcode"):
        cleaned["postcode"] = cleaned["postcode"].upper()

    property_type = text("property_type")
    if property_type is not None:
        property_type = property_type or Enquiry.PropertyType.RESIDENTIAL
        if property_type not in Enquiry.PropertyType.values:
            add("property_type", "Choose Residential or Commercial.")
        cleaned["property_type"] = property_type

    services = data.get("services") or []
    if not isinstance(services, list) or not all(isinstance(s, str) for s in services):
        add("services", "Choose from the listed services.")
    else:
        unknown = [s for s in services if s not in Enquiry.SERVICE_CHOICES]
        if unknown:
            add("services", "Choose from the listed services.")
        cleaned["services"] = [s for s in Enquiry.SERVICE_CHOICES if s in services]

    message = text("message")
    if message is not None:
        if not message:
            add("message", "This field is required.")
        elif len(message) < MESSAGE_MIN:
            add("message", f"Tell us a little more (at least {MESSAGE_MIN} characters).")
        elif len(message) > MESSAGE_MAX:
            add("message", f"Keep this to {MESSAGE_MAX} characters or fewer.")
        cleaned["message"] = message

    return cleaned, errors
