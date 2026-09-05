from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        MANAGER = "MANAGER", "Manager"
        ORDER_OPERATOR = "ORDER_OPERATOR", "Order Operator"
        CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT", "Customer Support"
        CUSTOMER = "CUSTOMER", "Customer"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Actif"
        BLOCKED = "BLOCKED", "Bloque"
        PENDING = "PENDING", "Verification en attente"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.CUSTOMER, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, null=True)
    phone = models.CharField(
        max_length=20,
        blank=True,
        validators=[RegexValidator(r"^(\+212|0)[5-7]\d{8}$", "Numero marocain invalide.")],
    )
    email_verified_at = models.DateTimeField(blank=True, null=True)
    token_version = models.PositiveIntegerField(default=0)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    @property
    def is_staff_member(self):
        return self.role != self.Role.CUSTOMER


class CustomerProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile")
    birthdate = models.DateField(blank=True, null=True)
    marketing_opt_in = models.BooleanField(default=False)


class CustomerAddress(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=80, default="Maison")
    full_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=20, validators=[RegexValidator(r"^(\+212|0)[5-7]\d{8}$")])
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, db_index=True)
    postal_code = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]


class Category(TimeStampedModel):
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True, max_length=180, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True, related_name="children")
    display_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    seo_title = models.CharField(max_length=180, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["display_order", "name"]
        indexes = [models.Index(fields=["slug", "is_active", "is_archived"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        ensure_slug(self)
        super().save(*args, **kwargs)


class Brand(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(unique=True, max_length=140, blank=True)
    logo = models.ImageField(upload_to="brands/", blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        ensure_slug(self)
        super().save(*args, **kwargs)


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Brouillon"
        ACTIVE = "ACTIVE", "Actif"
        ARCHIVED = "ARCHIVED", "Archive"
        OUT_OF_STOCK = "OUT_OF_STOCK", "Rupture"

    class SourceType(models.TextChoices):
        DEMO = "DEMO", "Demo"
        MANUAL = "MANUAL", "Manuel"
        EXCEL = "EXCEL", "Import Excel/CSV"
        SUPPLIER = "SUPPLIER", "Fournisseur"

    name = models.CharField(max_length=220)
    slug = models.SlugField(unique=True, max_length=240, blank=True)
    sku = models.CharField(max_length=80, unique=True)
    short_description = models.CharField(max_length=320, blank=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    subcategories = models.ManyToManyField(Category, blank=True, related_name="secondary_products")
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, blank=True, null=True, related_name="products")
    regular_price = models.DecimalField(max_digits=12, decimal_places=2)
    promotional_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    low_stock_threshold = models.PositiveIntegerField(default=5)
    weight_grams = models.PositiveIntegerField(default=0)
    dimensions = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    featured = models.BooleanField(default=False, db_index=True)
    new_arrival = models.BooleanField(default=False, db_index=True)
    bestseller = models.BooleanField(default=False, db_index=True)
    seo_title = models.CharField(max_length=180, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    sales_count = models.PositiveIntegerField(default=0)
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.MANUAL, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug", "status"]),
            models.Index(fields=["featured", "new_arrival", "bestseller"]),
            models.Index(fields=["name", "sku"]),
        ]

    @property
    def current_price(self):
        return self.promotional_price or self.regular_price

    @property
    def discount_percent(self):
        if self.promotional_price and self.regular_price:
            return int((self.regular_price - self.promotional_price) / self.regular_price * 100)
        return 0

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        ensure_slug(self)
        super().save(*args, **kwargs)


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    alt_text = models.CharField(max_length=160, blank=True)
    is_main = models.BooleanField(default=False)
    display_order = models.PositiveIntegerField(default=0)


class ProductAttribute(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    def save(self, *args, **kwargs):
        ensure_slug(self)
        super().save(*args, **kwargs)


class AttributeValue(TimeStampedModel):
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=120)
    color_hex = models.CharField(max_length=7, blank=True)

    class Meta:
        unique_together = ("attribute", "value")


class ProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=90, unique=True)
    values = models.ManyToManyField(AttributeValue, blank=True)
    price_override = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    is_active = models.BooleanField(default=True)

    @property
    def price(self):
        return self.price_override or self.product.current_price


class Inventory(TimeStampedModel):
    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="inventory")
    quantity = models.PositiveIntegerField(default=0)
    reserved_quantity = models.PositiveIntegerField(default=0)

    @property
    def available_quantity(self):
        return max(self.quantity - self.reserved_quantity, 0)


class StockMovement(TimeStampedModel):
    class Type(models.TextChoices):
        IN = "IN", "Entree"
        OUT = "OUT", "Sortie"
        ADJUSTMENT = "ADJUSTMENT", "Ajustement"
        RESERVED = "RESERVED", "Reserve"

    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="stock_movements")
    movement_type = models.CharField(max_length=20, choices=Type.choices)
    quantity = models.IntegerField()
    reason = models.CharField(max_length=255)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)


class Wishlist(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist")


class WishlistItem(TimeStampedModel):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("wishlist", "product")


class RecentlyViewedProduct(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recently_viewed", null=True, blank=True)
    session_key = models.CharField(max_length=80, blank=True, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)


class ProductReview(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "En attente"
        APPROVED = "APPROVED", "Approuve"
        REJECTED = "REJECTED", "Rejete"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    order_item = models.OneToOneField("OrderItem", on_delete=models.SET_NULL, null=True, blank=True)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    verified_purchase = models.BooleanField(default=False)

    class Meta:
        unique_together = ("product", "user", "order_item")


class ReviewImage(TimeStampedModel):
    review = models.ForeignKey(ProductReview, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="reviews/")


class Promotion(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENT = "PERCENT", "Pourcentage"
        FIXED = "FIXED", "Montant fixe"

    name = models.CharField(max_length=160)
    discount_type = models.CharField(max_length=16, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    products = models.ManyToManyField(Product, blank=True)
    categories = models.ManyToManyField(Category, blank=True)
    minimum_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    def is_valid_now(self):
        now = timezone.now()
        return self.is_active and self.starts_at <= now <= self.ends_at


class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENT = "PERCENT", "Pourcentage"
        FIXED = "FIXED", "Montant fixe"
        FREE_DELIVERY = "FREE_DELIVERY", "Livraison gratuite"

    code = models.CharField(max_length=40, unique=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    minimum_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    max_usage = models.PositiveIntegerField(blank=True, null=True)
    max_usage_per_customer = models.PositiveIntegerField(default=1)
    first_order_only = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    def is_valid_now(self):
        now = timezone.now()
        return self.is_active and self.starts_at <= now <= self.ends_at


class CouponUsage(TimeStampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="usages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True)
    guest_email = models.EmailField(blank=True)
    order = models.ForeignKey("Order", on_delete=models.CASCADE, null=True, blank=True)


class Cart(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, blank=True, null=True, related_name="carts")
    session_key = models.CharField(max_length=80, blank=True, db_index=True)
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, blank=True, null=True)
    is_active = models.BooleanField(default=True, db_index=True)


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    saved_for_later = models.BooleanField(default=False)

    class Meta:
        unique_together = ("cart", "variant", "saved_for_later")


class DeliveryZone(TimeStampedModel):
    city = models.CharField(max_length=120, unique=True)
    shipping_price = models.DecimalField(max_digits=10, decimal_places=2)
    estimated_delivery_time = models.CharField(max_length=80, default="24-72h")
    free_delivery_threshold = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    cash_on_delivery_available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", "En attente"
        CONFIRMED = "CONFIRMED", "Confirmee"
        PREPARING = "PREPARING", "Preparation"
        SHIPPED = "SHIPPED", "Expediee"
        OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY", "En livraison"
        DELIVERED = "DELIVERED", "Livree"
        CANCELLED = "CANCELLED", "Annulee"
        RETURN_REQUESTED = "RETURN_REQUESTED", "Retour demande"
        RETURNED = "RETURNED", "Retournee"
        REFUNDED = "REFUNDED", "Remboursee"

    class PaymentMethod(models.TextChoices):
        COD = "COD", "Paiement a la livraison"
        BANK_TRANSFER = "BANK_TRANSFER", "Virement bancaire"

    order_number = models.CharField(max_length=30, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders", blank=True, null=True)
    guest_email = models.EmailField(blank=True, db_index=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)
    payment_method = models.CharField(max_length=24, choices=PaymentMethod.choices)
    delivery_zone = models.ForeignKey(DeliveryZone, on_delete=models.PROTECT)
    shipping_full_name = models.CharField(max_length=160)
    shipping_phone = models.CharField(max_length=20)
    shipping_address = models.CharField(max_length=300)
    shipping_city = models.CharField(max_length=120)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    shipping_total = models.DecimalField(max_digits=12, decimal_places=2)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2)
    coupon_code = models.CharField(max_length=40, blank=True)
    customer_note = models.TextField(blank=True)
    internal_note = models.TextField(blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = f"DOL-{timezone.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    product_name = models.CharField(max_length=220)
    variant_label = models.CharField(max_length=220, blank=True)
    sku = models.CharField(max_length=90)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    total = models.DecimalField(max_digits=12, decimal_places=2)


class OrderStatusHistory(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    note = models.CharField(max_length=255, blank=True)


class Payment(TimeStampedModel):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="payment")
    method = models.CharField(max_length=24)
    status = models.CharField(max_length=40, default="PENDING")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)


class Shipment(TimeStampedModel):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    carrier = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=120, blank=True)
    shipped_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)


class ReturnRequest(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="returns")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    reason = models.TextField()
    status = models.CharField(max_length=30, default="PENDING")


class ReturnItem(TimeStampedModel):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)


class CustomerNotification(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=160)
    message = models.TextField()
    is_read = models.BooleanField(default=False)


class SupportTicket(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    subject = models.CharField(max_length=180)
    status = models.CharField(max_length=30, default="OPEN")
    priority = models.CharField(max_length=30, default="NORMAL")


class SupportMessage(TimeStampedModel):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    message = models.TextField()
    is_internal = models.BooleanField(default=False)


class SiteSettings(TimeStampedModel):
    key = models.CharField(max_length=80, unique=True)
    value = models.JSONField(default=dict)


class HomepageBanner(TimeStampedModel):
    title = models.CharField(max_length=160)
    subtitle = models.CharField(max_length=260, blank=True)
    image = models.ImageField(upload_to="banners/", blank=True, null=True)
    cta_label = models.CharField(max_length=80, blank=True)
    cta_url = models.CharField(max_length=220, blank=True)
    starts_at = models.DateTimeField(blank=True, null=True)
    ends_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)


class NewsletterSubscriber(TimeStampedModel):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)


class AuditLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    action = models.CharField(max_length=120, db_index=True)
    entity = models.CharField(max_length=120)
    entity_id = models.CharField(max_length=80, blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)


class ProductImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        PREVIEWED = "PREVIEWED", "Previsualise"
        COMPLETED = "COMPLETED", "Termine"
        FAILED = "FAILED", "Echec"

    filename = models.CharField(max_length=255)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PREVIEWED)
    update_existing = models.BooleanField(default=False)
    create_missing_relations = models.BooleanField(default=True)
    total_rows = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    error_report = models.TextField(blank=True)


class ProductImportRow(TimeStampedModel):
    job = models.ForeignKey(ProductImportJob, on_delete=models.CASCADE, related_name="rows")
    row_number = models.PositiveIntegerField()
    raw_data = models.JSONField(default=dict)
    normalized_data = models.JSONField(default=dict, blank=True)
    errors = models.JSONField(default=list, blank=True)
    duplicate_sku = models.BooleanField(default=False)
    imported_product = models.ForeignKey(Product, on_delete=models.SET_NULL, blank=True, null=True)


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=160, unique=True)
    slug = models.SlugField(unique=True, blank=True)
    base_url = models.URLField(blank=True)
    allowed_image_domains = models.JSONField(default=list, blank=True)
    percentage_margin = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    fixed_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    rounding_rule = models.CharField(max_length=20, default="NONE")
    minimum_profit = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    is_active = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        ensure_slug(self)
        super().save(*args, **kwargs)


class SupplierProduct(TimeStampedModel):
    class Status(models.TextChoices):
        IMPORTED = "IMPORTED", "Importe"
        NEEDS_REVIEW = "NEEDS_REVIEW", "A verifier"
        APPROVED = "APPROVED", "Approuve"
        PUBLISHED = "PUBLISHED", "Publie"
        REJECTED = "REJECTED", "Rejete"
        ERROR = "ERROR", "Erreur"

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="external_products")
    external_product_id = models.CharField(max_length=160)
    local_product = models.ForeignKey(Product, on_delete=models.SET_NULL, blank=True, null=True, related_name="supplier_records")
    source_url = models.URLField(blank=True)
    source_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    source_stock = models.IntegerField(blank=True, null=True)
    last_synchronized_at = models.DateTimeField(blank=True, null=True)
    synchronization_status = models.CharField(max_length=30, choices=Status.choices, default=Status.IMPORTED)
    synchronization_error = models.TextField(blank=True)
    applied_margin = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    final_selling_price = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    manual_price_override = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("supplier", "external_product_id")


def ensure_slug(instance, source="name"):
    if not instance.slug:
        base = slugify(getattr(instance, source))[:160] or uuid4().hex[:8]
        model = instance.__class__
        slug = base
        counter = 2
        while model.objects.filter(slug=slug).exclude(pk=instance.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        instance.slug = slug
