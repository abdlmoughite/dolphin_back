from datetime import timedelta
from decimal import Decimal

from decouple import config
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management.base import BaseCommand
from django.utils import timezone

from commerce.models import (
    AttributeValue,
    Brand,
    Cart,
    CartItem,
    Category,
    Coupon,
    DeliveryZone,
    HomepageBanner,
    Inventory,
    Order,
    Product,
    ProductAttribute,
    ProductVariant,
    Promotion,
)
from commerce.services import checkout, transition_order


class Command(BaseCommand):
    help = "Charge des donnees demo DOLPHIN. Peut etre relance sans dupliquer les donnees principales."

    def handle(self, *args, **options):
        User = get_user_model()
        admin = self.user(User, config("DEMO_SUPERADMIN_EMAIL", default="admin@dolphin.local"), config("DEMO_SUPERADMIN_PASSWORD", default="ChangeMe123!"), User.Role.SUPER_ADMIN)
        manager = self.user(User, config("DEMO_MANAGER_EMAIL", default="manager@dolphin.local"), config("DEMO_MANAGER_PASSWORD", default="ChangeMe123!"), User.Role.MANAGER)
        operator = self.user(User, config("DEMO_OPERATOR_EMAIL", default="orders@dolphin.local"), config("DEMO_OPERATOR_PASSWORD", default="ChangeMe123!"), User.Role.ORDER_OPERATOR)
        demo_customer_email = config("DEMO_CUSTOMER_EMAIL", default="client@dolphin.local")
        demo_customer = User.objects.filter(email=demo_customer_email, role=User.Role.CUSTOMER).first()
        if demo_customer:
            Order.objects.filter(user=demo_customer).update(user=None, guest_email="client.demo@dolphin.local")
            demo_customer.delete()
        Cart.objects.filter(session_key__startswith="demo-order-", user__isnull=True, is_active=True).delete()

        cities = [
            ("Casablanca", "25.00", "24-48h"),
            ("Rabat", "25.00", "24-48h"),
            ("Marrakech", "35.00", "48-72h"),
            ("Tanger", "35.00", "48-72h"),
            ("Fes", "35.00", "48-72h"),
            ("Agadir", "40.00", "48-96h"),
        ]
        zones = [DeliveryZone.objects.update_or_create(city=c, defaults={"shipping_price": Decimal(p), "estimated_delivery_time": e, "free_delivery_threshold": Decimal("600.00")})[0] for c, p, e in cities]

        category_names = [
            ("Electronique", ["Smartphones", "Audio", "Accessoires"]),
            ("Maison", ["Cuisine", "Decoration"]),
            ("Mode", ["Homme", "Femme"]),
            ("Beaute", ["Soins", "Parfums"]),
            ("Sport", ["Fitness", "Outdoor"]),
            ("Jouets", ["Educatif", "Jeux"]),
            ("Epicerie", ["Cafe", "Snacks"]),
            ("Bureau", ["Papeterie", "Organisation"]),
        ]
        categories = []
        for order, (name, children) in enumerate(category_names):
            parent, _ = Category.objects.update_or_create(name=name, defaults={"display_order": order, "description": f"Selection {name.lower()} DOLPHIN."})
            categories.append(parent)
            for child in children:
                Category.objects.update_or_create(name=child, defaults={"parent": parent, "description": f"Rayon {child.lower()}."})

        brands = [Brand.objects.update_or_create(name=name, defaults={})[0] for name in ["Dolphin", "Atlas", "Nour", "CasaTech", "Mogador", "BlueWave"]]
        color_attr, _ = ProductAttribute.objects.update_or_create(name="Couleur", defaults={})
        size_attr, _ = ProductAttribute.objects.update_or_create(name="Taille", defaults={})
        colors = [AttributeValue.objects.update_or_create(attribute=color_attr, value=value, defaults={"color_hex": color})[0] for value, color in [("Bleu", "#0077B6"), ("Noir", "#0B1F33"), ("Blanc", "#FFFFFF")]]
        sizes = [AttributeValue.objects.update_or_create(attribute=size_attr, value=value, defaults={})[0] for value in ["S", "M", "L"]]

        products = []
        names = [
            "Smartphone Atlas X1", "Ecouteurs BlueWave", "Chargeur rapide USB-C", "Montre sport Aqua", "Sac a dos urbain",
            "Blender cuisine 600W", "Lampe de bureau LED", "Tapis de yoga confort", "Cafe moulu premium", "Parfum Oud leger",
            "Creme hydratante", "Chemise coton homme", "Robe fluide ete", "Casque audio sans fil", "Clavier compact",
            "Souris ergonomique", "Set casseroles inox", "Coussin decoratif", "Jeu educatif lettres", "Gourde isotherme",
            "Baskets marche", "Organiseur bureau", "The vert menthe", "Snack amandes miel", "Power bank 10000mAh",
            "Enceinte portable", "Serviette sport", "Trousse de soin", "Puzzle Maroc", "Support telephone voiture",
        ]
        for i, name in enumerate(names):
            category = categories[i % len(categories)]
            price = Decimal("79.00") + Decimal(i * 17)
            product, _ = Product.objects.update_or_create(
                sku=f"DOL-{i+1:04d}",
                defaults={
                    "name": name,
                    "slug": "",
                    "category": category,
                    "brand": brands[i % len(brands)],
                    "regular_price": price,
                    "promotional_price": price - Decimal("20.00") if i % 5 == 0 else None,
                    "short_description": f"{name} selectionne pour le quotidien marocain.",
                    "description": f"{name} avec qualite fiable, livraison configurable par ville et support DOLPHIN.",
                    "status": Product.Status.ACTIVE,
                    "featured": i % 3 == 0,
                    "new_arrival": i % 4 == 0,
                    "bestseller": i % 6 == 0,
                    "source_type": Product.SourceType.DEMO,
                },
            )
            products.append(product)
            variant, _ = ProductVariant.objects.update_or_create(product=product, sku=f"{product.sku}-STD", defaults={"is_active": True})
            variant.values.set([colors[i % len(colors)], sizes[i % len(sizes)]])
            Inventory.objects.update_or_create(variant=variant, defaults={"quantity": 15 + i, "reserved_quantity": 0})

        now = timezone.now()
        coupon, _ = Coupon.objects.update_or_create(
            code="BIENVENUE10",
            defaults={"discount_type": Coupon.DiscountType.PERCENT, "value": Decimal("10.00"), "minimum_amount": Decimal("100.00"), "starts_at": now, "ends_at": now + timedelta(days=90), "is_active": True},
        )
        Promotion.objects.update_or_create(
            name="Offres ocean",
            defaults={"discount_type": Promotion.DiscountType.PERCENT, "value": Decimal("15.00"), "minimum_amount": Decimal("0.00"), "starts_at": now, "ends_at": now + timedelta(days=30), "is_active": True},
        )
        HomepageBanner.objects.update_or_create(
            title="DOLPHIN - Tout ce qu'il vous faut, au meme endroit.",
            defaults={"subtitle": "Categories, promotions et livraison au Maroc.", "cta_label": "Voir le catalogue", "cta_url": "/catalogue"},
        )

        if not Order.objects.filter(guest_email__endswith="@demo.dolphin.local").exists():
            demo_orders = [
                ("client.casablanca@demo.dolphin.local", "Client Casablanca", zones[0], [Order.Status.CONFIRMED]),
                ("client.rabat@demo.dolphin.local", "Client Rabat", zones[1], [Order.Status.CONFIRMED, Order.Status.PREPARING]),
                ("client.tanger@demo.dolphin.local", "Client Tanger", zones[3], [Order.Status.CONFIRMED, Order.Status.PREPARING, Order.Status.SHIPPED]),
                ("client.marrakech@demo.dolphin.local", "Client Marrakech", zones[2], [Order.Status.CONFIRMED, Order.Status.PREPARING, Order.Status.SHIPPED, Order.Status.DELIVERED]),
                ("client.agadir@demo.dolphin.local", "Client Agadir", zones[5], [Order.Status.CANCELLED]),
            ]
            for index, (email, full_name, zone, path) in enumerate(demo_orders):
                cart = Cart.objects.create(session_key=f"demo-order-{index}")
                quantity = 2 if index == 0 else 1 + (index % 2)
                CartItem.objects.create(cart=cart, variant=products[index].variants.first(), quantity=quantity)
                if index == 0:
                    cart.coupon = coupon
                    cart.save(update_fields=["coupon"])
                order = checkout(
                    AnonymousUser(),
                    cart,
                    {
                        "guest_email": email,
                        "delivery_zone_id": zone.id,
                        "payment_method": Order.PaymentMethod.COD,
                        "shipping_full_name": full_name,
                        "shipping_phone": f"+21261234567{index}",
                        "shipping_address": f"{10 + index} Avenue DOLPHIN",
                        "shipping_city": zone.city,
                        "customer_note": "Commande demo sans compte client.",
                    },
                )
                for status in path:
                    transition_order(order, status, operator if status in {Order.Status.SHIPPED, Order.Status.DELIVERED} else manager, "Demo")

        self.stdout.write(self.style.SUCCESS("Donnees demo DOLPHIN chargees."))

    def user(self, User, email, password, role):
        user, created = User.objects.get_or_create(email=email, defaults={"username": email.split("@")[0], "role": role, "is_staff": role != User.Role.CUSTOMER, "is_superuser": role == User.Role.SUPER_ADMIN})
        user.role = role
        user.status = User.Status.ACTIVE
        user.is_staff = role != User.Role.CUSTOMER
        user.is_superuser = role == User.Role.SUPER_ADMIN
        if created or not user.has_usable_password():
            user.set_password(password)
        user.save()
        return user
