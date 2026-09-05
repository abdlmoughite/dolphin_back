import csv
import io
import re
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlparse

from django.conf import settings
from rest_framework.exceptions import ValidationError

TEMPLATE_COLUMNS = [
    "name",
    "sku",
    "category",
    "subcategory",
    "brand",
    "short_description",
    "description",
    "regular_price",
    "promotional_price",
    "stock",
    "low_stock_threshold",
    "color",
    "size",
    "capacity",
    "weight",
    "image_urls",
    "is_active",
    "is_featured",
]


def sanitize_description(value):
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return escape(text.strip())


def parse_bool(value):
    return str(value).strip().lower() in {"1", "true", "yes", "oui", "actif", "active"}


def parse_decimal(value, field, errors, required=False):
    if value in ("", None):
        if required:
            errors.append(f"{field}: valeur requise.")
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
        if parsed < 0:
            errors.append(f"{field}: valeur negative interdite.")
        return parsed
    except (InvalidOperation, ValueError):
        errors.append(f"{field}: nombre invalide.")
        return None


def parse_int(value, field, errors, default=0):
    if value in ("", None):
        return default
    try:
        parsed = int(Decimal(str(value).replace(",", ".")))
        if parsed < 0:
            errors.append(f"{field}: valeur negative interdite.")
        return parsed
    except (InvalidOperation, ValueError):
        errors.append(f"{field}: entier invalide.")
        return default


def validate_columns(columns):
    missing = [column for column in TEMPLATE_COLUMNS if column not in columns]
    if missing:
        raise ValidationError({"file": f"Colonnes manquantes: {', '.join(missing)}"})


def validate_image_url(url, allowed_domains=None):
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise ValidationError("URL image invalide.")
    hostname = parsed.hostname or ""
    domains = set(allowed_domains or settings.SUPPLIER_IMAGE_DOMAINS)
    if domains and hostname not in domains:
        raise ValidationError("Domaine image non autorise.")
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0"} or hostname.startswith("10.") or hostname.startswith("192.168."):
        raise ValidationError("URL image interne interdite.")


def normalize_row(row):
    errors = []
    sku = str(row.get("sku") or "").strip()
    name = str(row.get("name") or "").strip()
    category = str(row.get("category") or "").strip()
    regular_price = parse_decimal(row.get("regular_price"), "regular_price", errors, required=True)
    promotional_price = parse_decimal(row.get("promotional_price"), "promotional_price", errors)
    stock = parse_int(row.get("stock"), "stock", errors, 0)
    threshold = parse_int(row.get("low_stock_threshold"), "low_stock_threshold", errors, 5)
    if not sku:
        errors.append("sku: valeur requise.")
    if not name:
        errors.append("name: valeur requise.")
    if not category:
        errors.append("category: valeur requise.")
    if promotional_price and regular_price and promotional_price >= regular_price:
        errors.append("promotional_price: doit etre inferieur au prix normal.")
    image_urls = [u.strip() for u in str(row.get("image_urls") or "").replace("\n", ",").split(",") if u.strip()]
    for url in image_urls:
        try:
            validate_image_url(url)
        except ValidationError as exc:
            errors.append(f"image_urls: {exc.detail[0] if isinstance(exc.detail, list) else exc.detail}")
    return {
        "data": {
            "name": name,
            "sku": sku,
            "category": category,
            "subcategory": str(row.get("subcategory") or "").strip(),
            "brand": str(row.get("brand") or "").strip(),
            "short_description": str(row.get("short_description") or "").strip(),
            "description": sanitize_description(row.get("description")),
            "regular_price": str(regular_price or "0.00"),
            "promotional_price": str(promotional_price) if promotional_price else None,
            "stock": stock,
            "low_stock_threshold": threshold,
            "color": str(row.get("color") or "").strip(),
            "size": str(row.get("size") or "").strip(),
            "capacity": str(row.get("capacity") or "").strip(),
            "weight_grams": parse_int(row.get("weight"), "weight", errors, 0),
            "image_urls": image_urls,
            "is_active": parse_bool(row.get("is_active")),
            "is_featured": parse_bool(row.get("is_featured")),
        },
        "errors": errors,
    }


def error_report(rows):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["row_number", "sku", "errors"])
    for row in rows:
        if row.errors:
            writer.writerow([row.row_number, row.raw_data.get("sku", ""), " | ".join(row.errors)])
    return output.getvalue()

