from decimal import Decimal
from datetime import datetime, time

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import (
    AuditLog,
    Cart,
    CartItem,
    Coupon,
    CouponUsage,
    CustomerAddress,
    CustomerNotification,
    DeliveryZone,
    Inventory,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    Product,
)


VALID_TRANSITIONS = {
    Order.Status.PENDING: {Order.Status.CONFIRMED, Order.Status.CANCELLED},
    Order.Status.CONFIRMED: {Order.Status.PREPARING, Order.Status.CANCELLED},
    Order.Status.PREPARING: {Order.Status.SHIPPED, Order.Status.CANCELLED},
    Order.Status.SHIPPED: {Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED},
    Order.Status.OUT_FOR_DELIVERY: {Order.Status.DELIVERED, Order.Status.RETURN_REQUESTED},
    Order.Status.DELIVERED: {Order.Status.RETURN_REQUESTED},
    Order.Status.RETURN_REQUESTED: {Order.Status.RETURNED, Order.Status.REFUNDED},
    Order.Status.RETURNED: {Order.Status.REFUNDED},
}


def get_or_create_cart(request):
    if request.user.is_authenticated:
        carts = Cart.objects.filter(user=request.user, is_active=True).order_by("-updated_at", "-id")
        cart = carts.first()
        if cart:
            carts.exclude(pk=cart.pk).update(is_active=False)
        else:
            cart = Cart.objects.create(user=request.user, is_active=True, session_key="")
        session_key = request.headers.get("X-Session-Key")
        if session_key:
            anon = Cart.objects.filter(session_key=session_key, user__isnull=True, is_active=True).first()
            if anon:
                merge_carts(anon, cart)
        return cart
    session_key = request.headers.get("X-Session-Key") or request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key
    carts = Cart.objects.filter(session_key=session_key, user__isnull=True, is_active=True).order_by("-updated_at", "-id")
    cart = carts.first()
    if cart:
        carts.exclude(pk=cart.pk).update(is_active=False)
    else:
        cart = Cart.objects.create(session_key=session_key, is_active=True)
    return cart


def merge_carts(source, target):
    for item in source.items.all():
        target_item, created = CartItem.objects.get_or_create(
            cart=target, variant=item.variant, saved_for_later=item.saved_for_later, defaults={"quantity": item.quantity}
        )
        if not created:
            target_item.quantity = F("quantity") + item.quantity
            target_item.save(update_fields=["quantity"])
    source.is_active = False
    source.save(update_fields=["is_active"])


def cart_totals(cart, guest_email=""):
    subtotal = Decimal("0.00")
    for item in cart.items.filter(saved_for_later=False).select_related("product", "variant__product"):
        product = item.variant.product if item.variant else item.product
        if not product:
            continue
        unit_price = item.variant.price if item.variant else product.current_price
        subtotal += unit_price * item.quantity
    discount = coupon_discount(cart.coupon, subtotal, cart.user, guest_email) if cart.coupon else Decimal("0.00")
    return {"subtotal": subtotal, "discount_total": discount, "total": max(subtotal - discount, Decimal("0.00"))}


def coupon_discount(coupon, subtotal, user=None, guest_email=""):
    if not coupon or not coupon.is_valid_now():
        raise ValidationError({"coupon": "Ce coupon n'est plus valide."})
    if subtotal < coupon.minimum_amount:
        raise ValidationError({"coupon": "Le montant minimum du coupon n'est pas atteint."})
    if coupon.max_usage and coupon.usages.count() >= coupon.max_usage:
        raise ValidationError({"coupon": "Ce coupon a atteint sa limite d'utilisation."})
    if user and user.is_authenticated:
        if coupon.first_order_only and Order.objects.filter(user=user).exists():
            raise ValidationError({"coupon": "Ce coupon est reserve a la premiere commande."})
        if coupon.usages.filter(user=user).count() >= coupon.max_usage_per_customer:
            raise ValidationError({"coupon": "Vous avez deja utilise ce coupon."})
    elif guest_email:
        normalized_email = guest_email.strip().lower()
        if coupon.first_order_only and Order.objects.filter(guest_email__iexact=normalized_email).exists():
            raise ValidationError({"coupon": "Ce coupon est reserve a la premiere commande."})
        if coupon.usages.filter(guest_email__iexact=normalized_email).count() >= coupon.max_usage_per_customer:
            raise ValidationError({"coupon": "Vous avez deja utilise ce coupon."})
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        return (subtotal * coupon.value / Decimal("100.00")).quantize(Decimal("0.01"))
    if coupon.discount_type == Coupon.DiscountType.FIXED:
        return min(coupon.value, subtotal)
    return Decimal("0.00")


@transaction.atomic
def add_cart_item(cart, variant=None, product=None, quantity=1):
    if variant and not product:
        product = variant.product
    filters = {"cart": cart, "saved_for_later": False}
    if variant:
        filters["variant"] = variant
    else:
        filters["product"] = product
        filters["variant__isnull"] = True
    existing = CartItem.objects.filter(**filters).first()
    if existing:
        existing.quantity = quantity + existing.quantity
        existing.save(update_fields=["quantity"])
        return existing
    return CartItem.objects.create(cart=cart, product=product, variant=variant, quantity=quantity)


def apply_coupon(cart, code):
    try:
        coupon = Coupon.objects.get(code=code.upper().strip(), is_active=True)
    except Coupon.DoesNotExist:
        raise ValidationError({"coupon": "Coupon introuvable."})
    totals = cart_totals(cart)
    coupon_discount(coupon, totals["subtotal"], cart.user)
    cart.coupon = coupon
    cart.save(update_fields=["coupon"])
    return cart


def authenticated_user(user):
    return user if getattr(user, "is_authenticated", False) else None


@transaction.atomic
def checkout(user, cart, data):
    customer_user = authenticated_user(user)
    guest_email = data.get("guest_email", "").strip().lower()
    idempotency_key = data.get("idempotency_key", "").strip()
    if idempotency_key:
        existing = Order.objects.select_for_update().filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    if not customer_user and not guest_email:
        raise ValidationError({"guest_email": "Email requis pour commander sans compte."})

    items = list(cart.items.filter(saved_for_later=False).select_related("product__category", "variant__product__category"))
    if not items:
        raise ValidationError({"cart": "Votre panier est vide."})
    for item in items:
        product = item.variant.product if item.variant else item.product
        if not product or product.status != Product.Status.ACTIVE or not product.category.is_active or product.category.is_archived:
            raise ValidationError({"cart": f"{product.name if product else 'Produit'} n'est plus disponible."})

    zone_id = data.get("delivery_zone_id")
    if zone_id:
        zone = DeliveryZone.objects.select_for_update().get(pk=zone_id, is_active=True)
    else:
        zone = DeliveryZone.objects.select_for_update().filter(is_active=True).order_by("city", "id").first()
    if not zone:
        zone = DeliveryZone.objects.create(city=data.get("shipping_city", "Autre") or "Autre", shipping_price=Decimal("0.00"), is_active=True)
    if data["payment_method"] == Order.PaymentMethod.COD and not zone.cash_on_delivery_available:
        raise ValidationError({"payment_method": "Paiement a la livraison indisponible dans cette ville."})

    address = None
    if data.get("address_id"):
        if not customer_user:
            raise ValidationError({"address_id": "Les adresses enregistrees sont reservees aux comptes admin."})
        address = CustomerAddress.objects.get(pk=data["address_id"], user=customer_user)
    else:
        missing = [field for field in ("shipping_full_name", "shipping_phone", "shipping_address") if not data.get(field)]
        if missing:
            raise ValidationError({field: "Champ requis pour commander sans adresse enregistree." for field in missing})

    totals = cart_totals(cart, guest_email=guest_email)
    shipping = Decimal("0.00")
    total = totals["total"] + shipping

    order = Order.objects.create(
        user=customer_user,
        guest_email=guest_email,
        payment_method=data["payment_method"],
        delivery_zone=zone,
        shipping_full_name=(address.full_name if address else data.get("shipping_full_name", f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip())),
        shipping_phone=(address.phone if address else data.get("shipping_phone", getattr(user, "phone", ""))),
        shipping_address=(address.address_line1 if address else data.get("shipping_address", "")),
        shipping_city=data.get("shipping_city", zone.city),
        subtotal=totals["subtotal"],
        discount_total=totals["discount_total"],
        shipping_total=shipping,
        tax_total=Decimal("0.00"),
        total=total,
        coupon_code=cart.coupon.code if cart.coupon else "",
        idempotency_key=idempotency_key or None,
        customer_note=data.get("customer_note", ""),
    )
    for item in items:
        product = item.variant.product if item.variant else item.product
        values = ", ".join(item.variant.values.values_list("value", flat=True)) if item.variant else ""
        unit_price = item.variant.price if item.variant else product.current_price
        OrderItem.objects.create(
            order=order,
            product=product,
            variant=item.variant,
            product_name=product.name,
            variant_label=values,
            sku=item.variant.sku if item.variant else product.sku,
            unit_price=unit_price,
            quantity=item.quantity,
            total=unit_price * item.quantity,
        )
        Product.objects.filter(pk=product.id).update(sales_count=F("sales_count") + item.quantity)
    Payment.objects.create(order=order, method=data["payment_method"], amount=total, status="PENDING")
    OrderStatusHistory.objects.create(order=order, to_status=order.status, actor=customer_user, note="Commande creee")
    if cart.coupon:
        CouponUsage.objects.create(coupon=cart.coupon, user=customer_user, guest_email=guest_email, order=order)
    if customer_user:
        CustomerNotification.objects.create(user=customer_user, title="Commande recue", message=f"Votre commande {order.order_number} a ete creee.")
    send_mail("Commande recue", f"Votre commande {order.order_number} a ete creee.", None, [customer_user.email if customer_user else guest_email], fail_silently=True)
    cart.is_active = False
    cart.save(update_fields=["is_active"])
    return order


@transaction.atomic
def transition_order(order, new_status, actor, note="", force=False):
    valid_statuses = {choice[0] for choice in Order.Status.choices}
    if new_status not in valid_statuses:
        raise ValidationError({"status": "Statut de commande inconnu."})
    if not force and new_status not in VALID_TRANSITIONS.get(order.status, set()):
        raise ValidationError({"status": "Transition de statut non autorisee."})
    previous = order.status
    order.status = new_status
    order.save(update_fields=["status", "updated_at"])
    OrderStatusHistory.objects.create(order=order, from_status=previous, to_status=new_status, actor=actor, note=note)
    AuditLog.objects.create(actor=actor, action="ORDER_STATUS_CHANGED", entity="Order", entity_id=str(order.pk), before={"status": previous}, after={"status": new_status})
    if order.user:
        CustomerNotification.objects.create(user=order.user, title="Statut de commande", message=f"{order.order_number}: {order.get_status_display()}")
    return order


def dashboard_metrics():
    today = timezone.localdate()
    month_start = today.replace(day=1)
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    today_end = timezone.make_aware(datetime.combine(today, time.max))
    month_start_dt = timezone.make_aware(datetime.combine(month_start, time.min))
    delivered = Order.objects.filter(status=Order.Status.DELIVERED)
    today_revenue = delivered.filter(updated_at__gte=today_start, updated_at__lte=today_end).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    month_revenue = delivered.filter(updated_at__gte=month_start_dt).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    total_orders = Order.objects.count()
    revenue = delivered.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    return {
        "revenue_today": today_revenue,
        "revenue_month": month_revenue,
        "total_orders": total_orders,
        "pending_orders": Order.objects.filter(status=Order.Status.PENDING).count(),
        "delivered_orders": delivered.count(),
        "cancelled_orders": Order.objects.filter(status=Order.Status.CANCELLED).count(),
        "active_products": Product.objects.filter(status=Product.Status.ACTIVE).count(),
        "average_order_value": revenue / total_orders if total_orders else Decimal("0.00"),
        "low_stock_products": Inventory.objects.filter(quantity__lte=F("variant__product__low_stock_threshold")).count(),
        "out_of_stock_products": Inventory.objects.filter(quantity=0).count(),
    }
