from decimal import Decimal

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
    StockMovement,
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
        cart, _ = Cart.objects.get_or_create(user=request.user, is_active=True, defaults={"session_key": ""})
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
    cart, _ = Cart.objects.get_or_create(session_key=session_key, user__isnull=True, is_active=True)
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


def cart_totals(cart):
    subtotal = Decimal("0.00")
    for item in cart.items.filter(saved_for_later=False).select_related("variant__product"):
        subtotal += item.variant.price * item.quantity
    discount = coupon_discount(cart.coupon, subtotal, cart.user) if cart.coupon else Decimal("0.00")
    return {"subtotal": subtotal, "discount_total": discount, "total": max(subtotal - discount, Decimal("0.00"))}


def coupon_discount(coupon, subtotal, user=None):
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
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        return (subtotal * coupon.value / Decimal("100.00")).quantize(Decimal("0.01"))
    if coupon.discount_type == Coupon.DiscountType.FIXED:
        return min(coupon.value, subtotal)
    return Decimal("0.00")


@transaction.atomic
def add_cart_item(cart, variant, quantity):
    inventory = Inventory.objects.select_for_update().get(variant=variant)
    existing = CartItem.objects.filter(cart=cart, variant=variant, saved_for_later=False).first()
    requested = quantity + (existing.quantity if existing else 0)
    if inventory.available_quantity < requested:
        raise ValidationError({"quantity": "Stock insuffisant pour cette quantite."})
    if existing:
        existing.quantity = requested
        existing.save(update_fields=["quantity"])
        return existing
    return CartItem.objects.create(cart=cart, variant=variant, quantity=quantity)


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
    guest_email = data.get("guest_email", "").strip()
    if not customer_user and not guest_email:
        raise ValidationError({"guest_email": "Email requis pour commander sans compte."})

    items = list(cart.items.filter(saved_for_later=False).select_related("variant__product", "variant__inventory"))
    if not items:
        raise ValidationError({"cart": "Votre panier est vide."})

    zone = DeliveryZone.objects.select_for_update().get(pk=data["delivery_zone_id"], is_active=True)
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

    totals = cart_totals(cart)
    shipping = zone.shipping_price
    if zone.free_delivery_threshold and totals["subtotal"] >= zone.free_delivery_threshold:
        shipping = Decimal("0.00")
    if cart.coupon and cart.coupon.discount_type == Coupon.DiscountType.FREE_DELIVERY:
        shipping = Decimal("0.00")
    total = totals["total"] + shipping

    for item in items:
        inventory = Inventory.objects.select_for_update().get(variant=item.variant)
        if inventory.available_quantity < item.quantity:
            raise ValidationError({"stock": f"Stock insuffisant pour {item.variant.product.name}."})
        inventory.quantity = F("quantity") - item.quantity
        inventory.save(update_fields=["quantity"])
        StockMovement.objects.create(variant=item.variant, movement_type=StockMovement.Type.OUT, quantity=-item.quantity, reason="Commande", actor=customer_user)

    order = Order.objects.create(
        user=customer_user,
        guest_email=guest_email,
        payment_method=data["payment_method"],
        delivery_zone=zone,
        shipping_full_name=(address.full_name if address else data.get("shipping_full_name", f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip())),
        shipping_phone=(address.phone if address else data.get("shipping_phone", getattr(user, "phone", ""))),
        shipping_address=(address.address_line1 if address else data.get("shipping_address", "")),
        shipping_city=zone.city,
        subtotal=totals["subtotal"],
        discount_total=totals["discount_total"],
        shipping_total=shipping,
        tax_total=Decimal("0.00"),
        total=total,
        coupon_code=cart.coupon.code if cart.coupon else "",
        customer_note=data.get("customer_note", ""),
    )
    for item in items:
        values = ", ".join(item.variant.values.values_list("value", flat=True))
        OrderItem.objects.create(
            order=order,
            product=item.variant.product,
            variant=item.variant,
            product_name=item.variant.product.name,
            variant_label=values,
            sku=item.variant.sku,
            unit_price=item.variant.price,
            quantity=item.quantity,
            total=item.variant.price * item.quantity,
        )
        Product.objects.filter(pk=item.variant.product_id).update(sales_count=F("sales_count") + item.quantity)
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
def transition_order(order, new_status, actor, note=""):
    if new_status not in VALID_TRANSITIONS.get(order.status, set()):
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
    delivered = Order.objects.filter(status=Order.Status.DELIVERED)
    today_revenue = delivered.filter(updated_at__date=today).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    month_revenue = delivered.filter(updated_at__date__gte=month_start).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    total_orders = Order.objects.count()
    revenue = delivered.aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    return {
        "revenue_today": today_revenue,
        "revenue_month": month_revenue,
        "total_orders": total_orders,
        "pending_orders": Order.objects.filter(status=Order.Status.PENDING).count(),
        "delivered_orders": delivered.count(),
        "cancelled_orders": Order.objects.filter(status=Order.Status.CANCELLED).count(),
        "average_order_value": revenue / total_orders if total_orders else Decimal("0.00"),
        "low_stock_products": Inventory.objects.filter(quantity__lte=F("variant__product__low_stock_threshold")).count(),
        "out_of_stock_products": Inventory.objects.filter(quantity=0).count(),
    }
