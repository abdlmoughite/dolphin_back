from decimal import Decimal

from django.db import transaction
from django.utils.text import slugify

from commerce.models import (
    AttributeValue,
    Brand,
    Category,
    Inventory,
    Product,
    ProductAttribute,
    ProductImportJob,
    ProductImportRow,
    ProductVariant,
    StockMovement,
)

from .excel_importer import parse_product_file
from .validators import error_report, normalize_row


def preview_import(uploaded_file, user):
    rows = parse_product_file(uploaded_file)
    job = ProductImportJob.objects.create(filename=uploaded_file.name, uploaded_by=user, total_rows=len(rows))
    for number, raw in enumerate(rows, start=2):
        normalized = normalize_row(raw)
        duplicate = Product.objects.filter(sku=normalized["data"]["sku"]).exists() if normalized["data"]["sku"] else False
        ProductImportRow.objects.create(
            job=job,
            row_number=number,
            raw_data={key: "" if value is None else str(value) for key, value in raw.items()},
            normalized_data=normalized["data"],
            errors=normalized["errors"],
            duplicate_sku=duplicate,
        )
    failed = job.rows.exclude(errors=[]).count()
    job.failed_count = failed
    job.skipped_count = job.rows.filter(duplicate_sku=True).count()
    job.summary = {"message": "Previsualisation terminee", "failed": failed}
    job.error_report = error_report(job.rows.all())
    job.save()
    return job


def get_or_create_attribute_value(attribute_name, value):
    if not value:
        return None
    attribute, _ = ProductAttribute.objects.get_or_create(name=attribute_name, defaults={"slug": ""})
    attr_value, _ = AttributeValue.objects.get_or_create(attribute=attribute, value=value)
    return attr_value


@transaction.atomic
def commit_import(job, update_existing=False, skip_duplicates=True, create_missing_relations=True, actor=None):
    created = updated = skipped = failed = 0
    for row in job.rows.select_for_update().order_by("row_number"):
        data = row.normalized_data
        errors = list(row.errors or [])
        existing = Product.objects.filter(sku=data.get("sku")).first()
        if existing and skip_duplicates and not update_existing:
            skipped += 1
            continue
        if errors:
            failed += 1
            continue
        category = Category.objects.filter(name__iexact=data["category"]).first()
        if not category and create_missing_relations:
            category = Category.objects.create(name=data["category"])
        if not category:
            row.errors = errors + ["category: introuvable."]
            row.save(update_fields=["errors"])
            failed += 1
            continue
        subcategory = None
        if data.get("subcategory"):
            subcategory = Category.objects.filter(name__iexact=data["subcategory"], parent=category).first()
            if not subcategory and create_missing_relations:
                subcategory = Category.objects.create(name=data["subcategory"], parent=category)
        brand = None
        if data.get("brand"):
            brand = Brand.objects.filter(name__iexact=data["brand"]).first()
            if not brand and create_missing_relations:
                brand = Brand.objects.create(name=data["brand"])

        product = existing or Product(sku=data["sku"])
        product.name = data["name"]
        product.category = category
        product.brand = brand
        product.short_description = data.get("short_description", "")
        product.description = data.get("description", "")
        product.regular_price = Decimal(data["regular_price"])
        product.promotional_price = Decimal(data["promotional_price"]) if data.get("promotional_price") else None
        product.low_stock_threshold = data.get("low_stock_threshold") or 5
        product.weight_grams = data.get("weight_grams") or 0
        product.featured = bool(data.get("is_featured"))
        product.status = Product.Status.ACTIVE if data.get("is_active") and data.get("image_urls") else Product.Status.DRAFT
        product.source_type = Product.SourceType.EXCEL
        product.save()
        if subcategory:
            product.subcategories.add(subcategory)

        variant, _ = ProductVariant.objects.get_or_create(product=product, sku=f"{product.sku}-DEFAULT")
        values = [
            get_or_create_attribute_value("Couleur", data.get("color")),
            get_or_create_attribute_value("Taille", data.get("size")),
            get_or_create_attribute_value("Capacite", data.get("capacity")),
        ]
        variant.values.set([value for value in values if value])
        inventory, _ = Inventory.objects.get_or_create(variant=variant)
        previous = inventory.quantity
        inventory.quantity = data.get("stock") or 0
        inventory.save(update_fields=["quantity", "updated_at"])
        StockMovement.objects.create(
            variant=variant,
            movement_type=StockMovement.Type.ADJUSTMENT,
            quantity=inventory.quantity - previous,
            reason="Import produits",
            actor=actor,
        )
        row.imported_product = product
        row.save(update_fields=["imported_product"])
        if existing:
            updated += 1
        else:
            created += 1

    job.update_existing = update_existing
    job.create_missing_relations = create_missing_relations
    job.created_count = created
    job.updated_count = updated
    job.skipped_count = skipped
    job.failed_count = failed
    job.status = ProductImportJob.Status.COMPLETED if failed == 0 else ProductImportJob.Status.FAILED
    job.summary = {"created": created, "updated": updated, "skipped": skipped, "failed": failed}
    job.error_report = error_report(job.rows.all())
    job.save()
    return job

