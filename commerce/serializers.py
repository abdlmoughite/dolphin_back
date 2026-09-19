from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError
from django.db.models import Sum
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import (
    AttributeValue,
    AuditLog,
    Brand,
    Cart,
    CartItem,
    Category,
    Coupon,
    CustomerAddress,
    CustomerNotification,
    DeliveryZone,
    Expense,
    HomepageBanner,
    HomeSection,
    Inventory,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    ProductAttribute,
    ProductImage,
    ProductImportJob,
    ProductImportRow,
    ProductVariant,
    Promotion,
    NewsletterSubscriber,
    Refund,
    ReturnHistory,
    ReturnItem,
    ReturnRequest,
    StockMovement,
    Supplier,
    SupplierProduct,
    SupportMessage,
    SupportTicket,
    User,
    Wishlist,
    WishlistItem,
)


class DolphinTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "email"

    def validate(self, attrs):
        user = authenticate(email=attrs.get("email"), password=attrs.get("password"))
        if not user:
            raise serializers.ValidationError({"detail": "Email ou mot de passe incorrect."})
        if user.status != User.Status.ACTIVE:
            raise serializers.ValidationError({"detail": "Ce compte n'est pas actif."})
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data


class UserSerializer(serializers.ModelSerializer):
    page_permissions = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "email", "username", "first_name", "last_name", "phone", "avatar", "role", "status", "page_permissions"]
        read_only_fields = ["role", "status"]

    def get_page_permissions(self, obj):
        return obj.effective_page_permissions


class DeveloperUserSerializer(serializers.ModelSerializer):
    order_count = serializers.IntegerField(read_only=True, default=0)
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, default=0)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True, validators=[validate_password])

    class Meta:
        model = get_user_model()
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "phone",
            "role",
            "status",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "order_count",
            "total_spent",
            "page_permissions",
            "password",
        ]
        read_only_fields = ["is_superuser", "date_joined", "last_login", "order_count", "total_spent"]

    def validate_role(self, value):
        request = self.context.get("request")
        if value == User.Role.CUSTOMER:
            raise serializers.ValidationError("Les comptes staff ne peuvent pas etre des clients.")
        if value == User.Role.SUPER_ADMIN and not (request and request.user.role == User.Role.SUPER_ADMIN):
            raise serializers.ValidationError("Seul un Developer peut attribuer ce role.")
        return value

    def validate_page_permissions(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Les permissions doivent etre une liste.")
        allowed = set(User.ADMIN_PAGE_PERMISSIONS)
        invalid = [item for item in value if item not in allowed]
        if invalid:
            raise serializers.ValidationError(f"Permissions inconnues: {', '.join(invalid)}")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["page_permissions"] = instance.effective_page_permissions
        return data

    def update(self, instance, validated_data):
        password = validated_data.pop("password", "")
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor and actor.pk == instance.pk:
            sensitive = {"role", "status", "is_staff", "page_permissions"} & set(validated_data)
            if sensitive:
                raise serializers.ValidationError("Vous ne pouvez pas modifier vos propres permissions.")
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.is_staff = True
        if instance.role == User.Role.SUPER_ADMIN:
            instance.is_superuser = True
            instance.is_staff = True
            instance.page_permissions = User.ADMIN_PAGE_PERMISSIONS
        if password:
            instance.set_password(password)
        instance.save()
        return instance

    def create(self, validated_data):
        password = validated_data.pop("password", "")
        role = validated_data.get("role", User.Role.MANAGER)
        validated_data["is_staff"] = True
        if role == User.Role.SUPER_ADMIN:
            validated_data["is_superuser"] = True
            validated_data["is_staff"] = True
            validated_data["page_permissions"] = User.ADMIN_PAGE_PERMISSIONS
        user = get_user_model().objects.create_user(password=password or None, **validated_data)
        return user


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = get_user_model()
        fields = ["email", "username", "password", "first_name", "last_name", "phone"]

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User.objects.create_user(role=User.Role.CUSTOMER, **validated_data)
        user.set_password(password)
        user.save()
        Wishlist.objects.get_or_create(user=user)
        return user


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField()
    new_password = serializers.CharField(validators=[validate_password])


class CustomerAddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerAddress
        exclude = ["user"]


class CategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = "__all__"


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = "__all__"


class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ["id", "image", "alt_text", "is_main", "display_order", "created_at"]


class ProductAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAttribute
        fields = "__all__"


class AttributeValueSerializer(serializers.ModelSerializer):
    attribute = ProductAttributeSerializer(read_only=True)

    class Meta:
        model = AttributeValue
        fields = "__all__"


class InventorySerializer(serializers.ModelSerializer):
    available_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = Inventory
        fields = ["quantity", "reserved_quantity", "available_quantity"]


class ProductVariantSerializer(serializers.ModelSerializer):
    values = AttributeValueSerializer(many=True, read_only=True)
    value_ids = serializers.PrimaryKeyRelatedField(queryset=AttributeValue.objects.all(), many=True, write_only=True, source="values", required=False)
    inventory = InventorySerializer(required=False)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ProductVariant
        fields = ["id", "sku", "values", "value_ids", "price_override", "price", "is_active", "inventory"]

    def create(self, validated_data):
        inventory_data = validated_data.pop("inventory", {})
        values = validated_data.pop("values", [])
        variant = ProductVariant.objects.create(**validated_data)
        variant.values.set(values)
        Inventory.objects.create(variant=variant, **inventory_data)
        return variant

    def update(self, instance, validated_data):
        inventory_data = validated_data.pop("inventory", None)
        values = validated_data.pop("values", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if values is not None:
            instance.values.set(values)
        if inventory_data is not None:
            Inventory.objects.update_or_create(variant=instance, defaults=inventory_data)
        return instance


class ProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), source="category", write_only=True)
    brand = BrandSerializer(read_only=True)
    brand_id = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), source="brand", write_only=True, required=False, allow_null=True)
    images = ProductImageSerializer(many=True, read_only=True)
    variants = serializers.SerializerMethodField()
    current_price = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount_percent = serializers.IntegerField(read_only=True)
    class Meta:
        model = Product
        fields = "__all__"
        extra_kwargs = {"cost_price": {"write_only": True, "required": False}}

    def validate(self, attrs):
        regular = attrs.get("regular_price", getattr(self.instance, "regular_price", None))
        promo = attrs.get("promotional_price", getattr(self.instance, "promotional_price", None))
        if promo and regular and promo >= regular:
            raise serializers.ValidationError({"promotional_price": "Le prix promotionnel doit etre inferieur au prix normal."})
        return attrs

    def get_variants(self, product):
        request = self.context.get("request")
        variants = product.variants.all()
        if not (request and request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            variants = variants.filter(is_active=True, product__status=Product.Status.ACTIVE, product__category__is_active=True, product__category__is_archived=False)
        return ProductVariantSerializer(variants, many=True, context=self.context).data


class AdminVariantWriteSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False)
    sku = serializers.CharField(max_length=90)
    price_override = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    is_active = serializers.BooleanField(default=True)
    color = serializers.CharField(required=False, allow_blank=True)
    size = serializers.CharField(required=False, allow_blank=True)
    capacity = serializers.CharField(required=False, allow_blank=True)


class AdminProductWriteSerializer(serializers.ModelSerializer):
    category_id = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), source="category")
    brand_id = serializers.PrimaryKeyRelatedField(queryset=Brand.objects.all(), source="brand", required=False, allow_null=True)
    subcategory_ids = serializers.PrimaryKeyRelatedField(queryset=Category.objects.all(), many=True, required=False, write_only=True)
    variants_payload = AdminVariantWriteSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "sku",
            "short_description",
            "description",
            "category_id",
            "subcategory_ids",
            "brand_id",
            "regular_price",
            "promotional_price",
            "cost_price",
            "low_stock_threshold",
            "weight_grams",
            "dimensions",
            "status",
            "featured",
            "new_arrival",
            "bestseller",
            "seo_title",
            "seo_description",
            "source_type",
            "variants_payload",
        ]
        read_only_fields = ["source_type"]

    def validate(self, attrs):
        regular = attrs.get("regular_price", getattr(self.instance, "regular_price", None))
        promo = attrs.get("promotional_price", getattr(self.instance, "promotional_price", None))
        if promo and regular and promo >= regular:
            raise serializers.ValidationError({"promotional_price": "Le prix promotionnel doit etre inferieur au prix normal."})
        return attrs

    def _attribute_value(self, name, value):
        if not value:
            return None
        attribute, _ = ProductAttribute.objects.get_or_create(name=name, defaults={"slug": ""})
        attr_value, _ = AttributeValue.objects.get_or_create(attribute=attribute, value=value)
        return attr_value

    def _sync_variants(self, product, variants):
        if not variants:
            removed = product.variants.all()
            CartItem.objects.filter(variant__in=removed).delete()
            StockMovement.objects.filter(variant__in=removed).delete()
            removed.delete()
            return
        seen = []
        for item in variants:
            variant_id = item.get("id")
            defaults = {"sku": item["sku"], "price_override": item.get("price_override"), "is_active": item.get("is_active", True)}
            if variant_id:
                try:
                    variant = ProductVariant.objects.get(pk=variant_id, product=product)
                except ProductVariant.DoesNotExist as exc:
                    raise serializers.ValidationError({"variants_payload": f"La variante {variant_id} n'appartient pas a ce produit."}) from exc
                for key, value in defaults.items():
                    setattr(variant, key, value)
                try:
                    variant.save()
                except IntegrityError as exc:
                    raise serializers.ValidationError({"variants_payload": f"SKU variante deja utilise: {item['sku']}."}) from exc
            else:
                try:
                    variant, _ = ProductVariant.objects.update_or_create(product=product, sku=item["sku"], defaults=defaults)
                except IntegrityError as exc:
                    raise serializers.ValidationError({"variants_payload": f"SKU variante deja utilise: {item['sku']}."}) from exc
            values = [
                self._attribute_value("Couleur", item.get("color", "")),
                self._attribute_value("Taille", item.get("size", "")),
                self._attribute_value("Capacite", item.get("capacity", "")),
            ]
            variant.values.set([value for value in values if value])
            seen.append(variant.id)
        removed = product.variants.exclude(id__in=seen)
        CartItem.objects.filter(variant__in=removed).delete()
        StockMovement.objects.filter(variant__in=removed).delete()
        removed.delete()

    def create(self, validated_data):
        variants = validated_data.pop("variants_payload", [])
        subcategories = validated_data.pop("subcategory_ids", [])
        product = Product.objects.create(source_type=Product.SourceType.MANUAL, **validated_data)
        product.subcategories.set(subcategories)
        self._sync_variants(product, variants)
        return product

    def update(self, instance, validated_data):
        variants = validated_data.pop("variants_payload", None)
        subcategories = validated_data.pop("subcategory_ids", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.source_type = instance.source_type or Product.SourceType.MANUAL
        instance.save()
        if subcategories is not None:
            instance.subcategories.set(subcategories)
        if variants is not None:
            self._sync_variants(instance, variants)
        return instance


class ProductImportRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImportRow
        fields = ["id", "row_number", "raw_data", "normalized_data", "errors", "duplicate_sku", "imported_product"]


class ProductImportJobSerializer(serializers.ModelSerializer):
    rows = ProductImportRowSerializer(many=True, read_only=True)
    uploaded_by_email = serializers.CharField(source="uploaded_by.email", read_only=True)

    class Meta:
        model = ProductImportJob
        fields = [
            "id",
            "filename",
            "uploaded_by_email",
            "status",
            "update_existing",
            "create_missing_relations",
            "total_rows",
            "created_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "summary",
            "error_report",
            "rows",
            "created_at",
        ]


class CartItemSerializer(serializers.ModelSerializer):
    variant = ProductVariantSerializer(read_only=True)
    product = ProductSerializer(read_only=True)
    variant_id = serializers.PrimaryKeyRelatedField(queryset=ProductVariant.objects.filter(is_active=True, product__status=Product.Status.ACTIVE, product__category__is_active=True, product__category__is_archived=False), source="variant", write_only=True, required=False)
    product_id = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(status=Product.Status.ACTIVE, category__is_active=True, category__is_archived=False), source="product", write_only=True, required=False)
    line_total = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    product_slug = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ["id", "product", "product_id", "variant", "variant_id", "product_name", "product_slug", "quantity", "saved_for_later", "line_total"]

    def validate(self, attrs):
        variant = attrs.get("variant")
        product = attrs.get("product")
        if not variant and not product:
            raise serializers.ValidationError({"product_id": "Produit requis."})
        if variant and product and variant.product_id != product.id:
            raise serializers.ValidationError({"variant_id": "La variante ne correspond pas au produit."})
        if variant and not product:
            attrs["product"] = variant.product
        return attrs

    def get_line_total(self, obj):
        product = obj.variant.product if obj.variant else obj.product
        if not product:
            return 0
        unit_price = obj.variant.price if obj.variant else product.current_price
        return unit_price * obj.quantity

    def get_product_name(self, obj):
        product = obj.variant.product if obj.variant else obj.product
        return product.name if product else ""

    def get_product_slug(self, obj):
        product = obj.variant.product if obj.variant else obj.product
        return product.slug if product else ""


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    discount_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Cart
        fields = ["id", "items", "coupon", "subtotal", "discount_total", "total"]


class CouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = "__all__"


class PromotionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Promotion
        fields = "__all__"


class DeliveryZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryZone
        fields = "__all__"
        extra_kwargs = {"shipping_price": {"required": False}, "free_delivery_threshold": {"required": False}}

    def validate(self, attrs):
        attrs["shipping_price"] = 0
        attrs["free_delivery_threshold"] = None
        return attrs


class CheckoutSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=80, required=False, allow_blank=True)
    address_id = serializers.IntegerField(required=False)
    guest_email = serializers.EmailField(required=False, allow_blank=True)
    shipping_full_name = serializers.CharField(max_length=160, required=False)
    shipping_phone = serializers.CharField(max_length=20, required=False)
    shipping_address = serializers.CharField(max_length=300, required=False)
    shipping_city = serializers.CharField(max_length=120)
    delivery_zone_id = serializers.IntegerField()
    payment_method = serializers.ChoiceField(choices=Order.PaymentMethod.choices)
    customer_note = serializers.CharField(required=False, allow_blank=True)


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["id", "product", "variant", "product_name", "variant_label", "sku", "unit_price", "quantity", "total"]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.email", read_only=True)

    class Meta:
        model = OrderStatusHistory
        fields = ["id", "from_status", "to_status", "actor_name", "note", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = "__all__"
        read_only_fields = ["order_number", "user", "subtotal", "discount_total", "shipping_total", "tax_total", "total"]


class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductSerializer(read_only=True)
    product_id = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), source="product", write_only=True)

    class Meta:
        model = WishlistItem
        fields = ["id", "product", "product_id", "created_at"]


class CustomerNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerNotification
        fields = "__all__"
        read_only_fields = ["user"]


class SupportMessageSerializer(serializers.ModelSerializer):
    sender_email = serializers.CharField(source="sender.email", read_only=True)

    class Meta:
        model = SupportMessage
        fields = ["id", "sender_email", "message", "is_internal", "created_at"]
        read_only_fields = ["sender"]


class SupportTicketSerializer(serializers.ModelSerializer):
    messages = SupportMessageSerializer(many=True, read_only=True)

    class Meta:
        model = SupportTicket
        fields = "__all__"
        read_only_fields = ["user"]


class ReturnRequestSerializer(serializers.ModelSerializer):
    class ReturnItemWriteSerializer(serializers.Serializer):
        order_item = serializers.PrimaryKeyRelatedField(queryset=OrderItem.objects.select_related("order"))
        quantity = serializers.IntegerField(min_value=1)

    class ReturnItemReadSerializer(serializers.ModelSerializer):
        product_name = serializers.CharField(source="order_item.product_name", read_only=True)
        sku = serializers.CharField(source="order_item.sku", read_only=True)
        ordered_quantity = serializers.IntegerField(source="order_item.quantity", read_only=True)

        class Meta:
            model = ReturnItem
            fields = ["id", "order_item", "product_name", "sku", "ordered_quantity", "quantity"]

    items = ReturnItemReadSerializer(many=True, read_only=True)
    items_payload = ReturnItemWriteSerializer(many=True, write_only=True, required=False)
    history = serializers.SerializerMethodField()

    class Meta:
        model = ReturnRequest
        fields = "__all__"
        read_only_fields = ["user", "status", "admin_decision", "decided_by", "decided_at", "items", "history"]

    def get_history(self, obj):
        return ReturnHistorySerializer(obj.history.select_related("actor").order_by("created_at"), many=True).data

    def validate(self, attrs):
        request = self.context.get("request")
        order = attrs.get("order", getattr(self.instance, "order", None))
        if request and request.user.role == User.Role.CUSTOMER and order and order.user_id != request.user.id:
            raise serializers.ValidationError({"order": "Commande introuvable pour ce client."})
        if request and request.user.role == User.Role.CUSTOMER and order and order.status != Order.Status.DELIVERED:
            raise serializers.ValidationError({"order": "Un retour client est possible uniquement pour une commande livree."})
        items = attrs.get("items_payload", [])
        if not self.instance and not items:
            raise serializers.ValidationError({"items_payload": "Ajoutez au moins un article a retourner."})
        for item in items:
            order_item = item["order_item"]
            if order and order_item.order_id != order.id:
                raise serializers.ValidationError({"items_payload": "Article introuvable dans cette commande."})
            if item["quantity"] > order_item.quantity:
                raise serializers.ValidationError({"items_payload": f"Quantite trop elevee pour {order_item.product_name}."})
        return attrs

    def create(self, validated_data):
        items = validated_data.pop("items_payload", [])
        return_request = super().create(validated_data)
        ReturnItem.objects.bulk_create(
            [ReturnItem(return_request=return_request, order_item=item["order_item"], quantity=item["quantity"]) for item in items]
        )
        return return_request


class ReturnHistorySerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = ReturnHistory
        fields = ["id", "from_status", "to_status", "note", "actor_email", "created_at"]


class RefundSerializer(serializers.ModelSerializer):
    processed_by_email = serializers.EmailField(source="processed_by.email", read_only=True)

    class Meta:
        model = Refund
        fields = "__all__"
        read_only_fields = ["processed_by", "processed_at"]

    def validate(self, attrs):
        order = attrs.get("order", getattr(self.instance, "order", None))
        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if order and amount:
            refunded = order.refunds.exclude(pk=getattr(self.instance, "pk", None)).filter(status__in=[Refund.Status.APPROVED, Refund.Status.PAID]).aggregate(total=Sum("amount"))["total"] or 0
            if amount + refunded > order.total:
                raise serializers.ValidationError({"amount": "Le remboursement depasse le total de la commande."})
        return attrs


class HomepageBannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomepageBanner
        fields = "__all__"


class HomeSectionSerializer(serializers.ModelSerializer):
    products = serializers.SerializerMethodField()
    product_ids = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), many=True, source="products", write_only=True, required=False)

    class Meta:
        model = HomeSection
        fields = ["id", "key", "title", "description", "is_visible", "display_order", "products", "product_ids", "created_at", "updated_at"]
        read_only_fields = ["created_at", "updated_at"]

    def get_products(self, obj):
        products = obj.products.all()
        request = self.context.get("request")
        if not (request and request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            products = products.filter(status=Product.Status.ACTIVE, category__is_active=True, category__is_archived=False)
        return ProductSerializer(products, many=True, context=self.context).data


class NewsletterSubscriberSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsletterSubscriber
        fields = ["id", "email", "is_active", "created_at"]
        read_only_fields = ["is_active", "created_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.EmailField(source="actor.email", read_only=True)

    class Meta:
        model = AuditLog
        fields = ["id", "actor_email", "action", "entity", "entity_id", "before", "after", "ip_address", "created_at"]


class SupplierProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupplierProduct
        fields = "__all__"


class SupplierSerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Supplier
        fields = "__all__"


class ExpenseSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)

    class Meta:
        model = Expense
        fields = "__all__"
        read_only_fields = ["created_by"]
