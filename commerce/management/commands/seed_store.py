from datetime import timedelta
from decimal import Decimal
import hashlib
from io import BytesIO
import os
import textwrap

from decouple import config
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image, ImageDraw, ImageFont

from commerce.models import (
    AttributeValue,
    Brand,
    Cart,
    CartItem,
    Category,
    Coupon,
    CouponUsage,
    CustomerAddress,
    CustomerNotification,
    CustomerProfile,
    DeliveryZone,
    HomepageBanner,
    Inventory,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    Product,
    ProductAttribute,
    ProductImage,
    ProductReview,
    ProductVariant,
    Promotion,
    StockMovement,
    Wishlist,
    WishlistItem,
)


DEMO_SEO_MARK = "Demo DOLPHIN"
DEMO_DOMAIN = "demo.dolphin.local"

CATEGORY_DATA = [
    ("Smartphones", "smartphones", "Telephones Android et iOS adaptes au marche marocain."),
    ("Informatique", "informatique", "PC portables, peripheriques et accessoires de travail."),
    ("Audio & Ecouteurs", "audio-ecouteurs", "Casques, enceintes et ecouteurs pour tous les usages."),
    ("Montres connectees", "montres-connectees", "Wearables pour sport, sante et notifications."),
    ("Accessoires", "accessoires", "Chargeurs, protections, supports et objets pratiques."),
    ("Maison & Cuisine", "maison-cuisine", "Equipements fiables pour la maison et la cuisine."),
    ("Beaute & Soins", "beaute-soins", "Soins quotidiens, grooming et bien-etre."),
    ("Gaming", "gaming", "Consoles, chaises, claviers et equipements gamer."),
    ("Mode", "mode", "Pieces faciles a porter, sacs et accessoires mode."),
    ("Promotions", "promotions", "Selections a prix reduits et offres saisonnieres."),
]

BRANDS = [
    "Dolphin Selection",
    "Atlas Mobile",
    "CasaTech",
    "BlueWave",
    "Mogador Home",
    "Nour Beauty",
    "Sahara Gear",
    "PixelPro",
    "UrbanMode",
    "Amazigh Sound",
]

PRODUCTS = [
    ("smartphones", "Atlas Nova 5G 128Go", "Atlas Mobile", 3299, 2899, "Ecran fluide, autonomie longue duree et double SIM.", "Noir", "128Go"),
    ("smartphones", "CasaTech M12 Pro 256Go", "CasaTech", 4899, 4399, "Smartphone puissant pour photo, video et multitache.", "Bleu", "256Go"),
    ("smartphones", "PixelPro A8 Compact", "PixelPro", 2199, None, "Format compact avec charge rapide et stockage confortable.", "Blanc", "128Go"),
    ("smartphones", "Atlas Kids Secure 64Go", "Atlas Mobile", 1499, 1299, "Telephone simple avec controle parental et coque renforcee.", "Vert", "64Go"),
    ("smartphones", "Dolphin Max Plus 512Go", "Dolphin Selection", 8999, 8199, "Grand ecran AMOLED, photo avancee et finition premium.", "Titane", "512Go"),
    ("informatique", "CasaBook Air 14 i5", "CasaTech", 7499, 6999, "PC portable leger pour bureautique, cours et deplacements.", "Argent", "16Go"),
    ("informatique", "PixelPro Studio 15 Ryzen 7", "PixelPro", 10999, 9999, "Machine rapide pour creation, montage et productivite intensive.", "Gris", "512Go"),
    ("informatique", "Clavier mecanique AZERTY RGB", "Sahara Gear", 699, 599, "Frappe precise, retroeclairage reglable et chassis robuste.", "Noir", "Standard"),
    ("informatique", "Souris ergonomique sans fil", "CasaTech", 249, None, "Prise en main confortable avec batterie longue duree.", "Noir", "M"),
    ("informatique", "Ecran 24 pouces Full HD", "PixelPro", 1699, 1499, "Affichage net pour teletravail, etudes et divertissement.", "Noir", "24 pouces"),
    ("audio-ecouteurs", "BlueWave Buds ANC", "BlueWave", 799, 649, "Ecouteurs avec reduction de bruit et boitier compact.", "Noir", "Standard"),
    ("audio-ecouteurs", "Amazigh Sound Pulse Mini", "Amazigh Sound", 399, 349, "Enceinte portable puissante avec autonomie week-end.", "Bleu", "Mini"),
    ("audio-ecouteurs", "Casque BlueWave Studio", "BlueWave", 1199, 999, "Casque circum-aural confortable pour musique et appels.", "Beige", "Standard"),
    ("audio-ecouteurs", "Micro USB Stream One", "Sahara Gear", 549, None, "Micro clair pour gaming, podcasts et reunions.", "Noir", "USB"),
    ("audio-ecouteurs", "Barre de son Mogador 2.1", "Mogador Home", 1799, 1599, "Son TV immersif avec caisson compact et Bluetooth.", "Noir", "2.1"),
    ("montres-connectees", "Atlas Watch Fit", "Atlas Mobile", 899, 749, "Suivi sport, sommeil et notifications en temps reel.", "Noir", "44mm"),
    ("montres-connectees", "Dolphin Active Pro", "Dolphin Selection", 1299, 1099, "GPS, autonomie solide et cadrans personnalisables.", "Bleu", "46mm"),
    ("montres-connectees", "Nour Wellness Band", "Nour Beauty", 349, 299, "Bracelet leger pour pas, rythme cardiaque et rappels.", "Rose", "S/M"),
    ("montres-connectees", "Sahara Outdoor Watch", "Sahara Gear", 1599, None, "Montre robuste avec modes outdoor et etancheite renforcee.", "Vert", "47mm"),
    ("montres-connectees", "PixelPro Smart Kids", "PixelPro", 699, 599, "Montre connectee enfant avec appels et localisation familiale.", "Bleu", "Kids"),
    ("accessoires", "Chargeur rapide USB-C 65W", "CasaTech", 349, 279, "Charge rapide pour telephone, tablette et laptop compatible.", "Blanc", "65W"),
    ("accessoires", "Power Bank 20000mAh", "Dolphin Selection", 499, 429, "Batterie externe fiable avec double sortie USB.", "Noir", "20000mAh"),
    ("accessoires", "Support voiture magnetique", "Sahara Gear", 149, None, "Support stable pour navigation et appels mains libres.", "Noir", "Standard"),
    ("accessoires", "Coque anti-choc universelle", "Dolphin Selection", 99, 79, "Protection renforcee avec finition sobre et prise agreable.", "Transparent", "M"),
    ("accessoires", "Cable USB-C nylon 2m", "CasaTech", 89, None, "Cable tresse resistant pour charge et transfert.", "Gris", "2m"),
    ("maison-cuisine", "Blender Mogador 800W", "Mogador Home", 699, 599, "Blender puissant pour jus, smoothies et sauces maison.", "Inox", "800W"),
    ("maison-cuisine", "Friteuse sans huile 5L", "Mogador Home", 1299, 1099, "Cuisson croustillante avec peu d'huile et panier familial.", "Noir", "5L"),
    ("maison-cuisine", "Set casseroles inox 6 pieces", "Mogador Home", 899, 749, "Batterie solide compatible gaz, induction et vitroceramique.", "Inox", "6 pieces"),
    ("maison-cuisine", "Aspirateur compact cyclonique", "Mogador Home", 999, None, "Nettoyage efficace avec filtration lavable et accessoires.", "Rouge", "Compact"),
    ("maison-cuisine", "Lampe LED bureau tactile", "CasaTech", 299, 249, "Eclairage reglable avec port USB et design discret.", "Blanc", "Tactile"),
    ("beaute-soins", "Routine visage Nour Hydratation", "Nour Beauty", 349, 299, "Pack nettoyant, serum et creme pour peau hydratee.", "Blanc", "Pack"),
    ("beaute-soins", "Tondeuse precision rechargeable", "Nour Beauty", 399, 329, "Tondeuse pratique pour barbe, contours et entretien quotidien.", "Noir", "Standard"),
    ("beaute-soins", "Brosse lissante ceramique", "Nour Beauty", 499, 429, "Lissage rapide avec temperature controlee et cable rotatif.", "Rose", "Ceramique"),
    ("beaute-soins", "Parfum Oud Leger 50ml", "Nour Beauty", 299, None, "Sillage doux inspire des notes orientales modernes.", "Ambre", "50ml"),
    ("beaute-soins", "Kit manucure electrique", "Nour Beauty", 249, 199, "Accessoires complets pour soin des ongles a domicile.", "Blanc", "Kit"),
    ("gaming", "Console portable RetroWave", "Sahara Gear", 1199, 999, "Console compacte avec ecran net et commandes confortables.", "Violet", "128Go"),
    ("gaming", "Chaise gaming Atlas Pro", "Sahara Gear", 2499, 2199, "Assise reglable, coussins lombaires et structure stable.", "Noir", "XL"),
    ("gaming", "Manette sans fil PixelPro", "PixelPro", 499, 429, "Manette reactive pour PC et console compatible Bluetooth.", "Noir", "Standard"),
    ("gaming", "Tapis souris XXL Speed", "Sahara Gear", 199, 149, "Surface large, base antiderapante et glisse controlee.", "Noir", "XXL"),
    ("gaming", "Casque gamer RGB", "BlueWave", 699, 599, "Son immersif, micro flexible et coussinets confortables.", "Noir", "RGB"),
    ("mode", "Sac a dos UrbanMode 22L", "UrbanMode", 449, 379, "Sac organise avec compartiment laptop et tissu deperlant.", "Noir", "22L"),
    ("mode", "Sneakers marche confort", "UrbanMode", 599, 499, "Chaussures legeres pour ville, marche et usage quotidien.", "Blanc", "42"),
    ("mode", "Chemise coton premium", "UrbanMode", 299, None, "Coupe moderne, coton doux et finitions propres.", "Bleu", "L"),
    ("mode", "Montre classique acier", "UrbanMode", 799, 699, "Montre elegante avec bracelet acier et cadran minimal.", "Argent", "Standard"),
    ("mode", "Pochette voyage organisee", "UrbanMode", 199, 149, "Rangement documents, cartes et accessoires essentiels.", "Beige", "Standard"),
    ("promotions", "Pack rentree connectee", "Dolphin Selection", 1999, 1599, "Bundle clavier, souris, casque et support laptop.", "Noir", "Pack"),
    ("promotions", "Kit maison intelligente", "CasaTech", 1499, 1199, "Ampoules, prise connectee et hub pour demarrer simplement.", "Blanc", "Kit"),
    ("promotions", "Bundle audio nomade", "BlueWave", 999, 799, "Ecouteurs ANC, enceinte mini et cable tresse.", "Bleu", "Bundle"),
    ("promotions", "Pack soins essentiels", "Nour Beauty", 699, 549, "Selection de soins visage et grooming pour routine complete.", "Blanc", "Pack"),
    ("promotions", "Offre gaming weekend", "Sahara Gear", 1699, 1399, "Casque, manette et tapis XXL prets pour jouer.", "Noir", "Bundle"),
]

CUSTOMERS = [
    ("client@dolphin.local", "Client", "Dolphin", "Casablanca"),
    ("amina.bennani", "Amina", "Bennani", "Rabat"),
    ("youssef.elamrani", "Youssef", "El Amrani", "Marrakech"),
    ("salma.ait", "Salma", "Ait Lahcen", "Agadir"),
    ("mehdi.ziani", "Mehdi", "Ziani", "Tanger"),
    ("nora.fassi", "Nora", "Fassi", "Fes"),
    ("karim.lahlou", "Karim", "Lahlou", "Casablanca"),
    ("hajar.ouardi", "Hajar", "Ouardi", "Rabat"),
    ("anas.bouazza", "Anas", "Bouazza", "Marrakech"),
    ("meryem.saidi", "Meryem", "Saidi", "Tanger"),
    ("ilyas.tazi", "Ilyas", "Tazi", "Fes"),
    ("sara.naciri", "Sara", "Naciri", "Agadir"),
]


class Command(BaseCommand):
    help = "Seed professional demo data for the DOLPHIN store. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument("--reset-demo", action="store_true", help="Delete only demo data created by this command before seeding.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset_demo"]:
            self.reset_demo_data()

        admin = self.ensure_staff_user("DEMO_SUPERADMIN_EMAIL", "DEMO_SUPERADMIN_PASSWORD", "admin@dolphin.local", get_user_model().Role.SUPER_ADMIN)
        manager = self.ensure_staff_user("DEMO_MANAGER_EMAIL", "DEMO_MANAGER_PASSWORD", "manager@dolphin.local", get_user_model().Role.MANAGER)
        operator = self.ensure_staff_user("DEMO_OPERATOR_EMAIL", "DEMO_OPERATOR_PASSWORD", "orders@dolphin.local", get_user_model().Role.ORDER_OPERATOR)

        zones = self.seed_delivery_zones()
        categories = self.seed_categories()
        brands = self.seed_brands()
        attributes = self.seed_attributes()
        products = self.seed_products(categories, brands, attributes, admin)
        coupons = self.seed_coupons()
        self.seed_promotions(products, categories)
        self.seed_banners()
        customers = self.seed_customers()
        self.seed_customer_content(customers, products)
        self.seed_orders(customers, products, zones, coupons, manager, operator)
        self.print_summary()

    def reset_demo_data(self):
        demo_products = Product.objects.filter(source_type=Product.SourceType.DEMO)
        demo_orders = (
            Order.objects.filter(guest_email__endswith=f"@{DEMO_DOMAIN}")
            | Order.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}")
            | Order.objects.filter(items__product__in=demo_products)
        ).distinct()
        ProductReview.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        ProductReview.objects.filter(order_item__order__in=demo_orders).delete()
        WishlistItem.objects.filter(wishlist__user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        Wishlist.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        CustomerNotification.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        CustomerAddress.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        CustomerProfile.objects.filter(user__email__endswith=f"@{DEMO_DOMAIN}").delete()
        CouponUsage.objects.filter(order__in=demo_orders).delete()
        Payment.objects.filter(order__in=demo_orders).delete()
        OrderStatusHistory.objects.filter(order__in=demo_orders).delete()
        OrderItem.objects.filter(order__in=demo_orders).delete()
        demo_orders.delete()
        CartItem.objects.filter(cart__session_key__startswith="demo-").delete()
        Cart.objects.filter(session_key__startswith="demo-").delete()
        StockMovement.objects.filter(variant__product__in=demo_products).delete()
        ProductImage.objects.filter(product__in=demo_products).delete()
        Inventory.objects.filter(variant__product__in=demo_products).delete()
        ProductVariant.objects.filter(product__in=demo_products).delete()
        demo_products.delete()
        Promotion.objects.filter(name__startswith="Demo |").delete()
        Coupon.objects.filter(code__in=["WELCOME10", "DOLPHIN20", "CASA50"]).delete()
        HomepageBanner.objects.filter(title__startswith="Demo |").delete()
        get_user_model().objects.filter(email__endswith=f"@{DEMO_DOMAIN}").delete()
        Category.objects.filter(seo_title=DEMO_SEO_MARK, products__isnull=True, secondary_products__isnull=True).delete()
        Brand.objects.filter(products__isnull=True, name__in=BRANDS).delete()

    def ensure_staff_user(self, email_env, password_env, default_email, role):
        User = get_user_model()
        email = config(email_env, default=default_email)
        password = config(password_env, default="")
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email.split("@")[0],
                "first_name": role.replace("_", " ").title(),
                "role": role,
                "status": User.Status.ACTIVE,
                "is_staff": True,
                "is_superuser": role == User.Role.SUPER_ADMIN,
            },
        )
        user.role = role
        user.status = User.Status.ACTIVE
        user.is_staff = True
        user.is_superuser = role == User.Role.SUPER_ADMIN
        if created:
            if password:
                user.set_password(password)
            else:
                user.set_unusable_password()
                self.stdout.write(self.style.WARNING(f"{password_env} is not set; {email} was created without a usable password."))
        user.save()
        return user

    def seed_delivery_zones(self):
        data = [
            ("Casablanca", "25.00", "24-48h", "600.00"),
            ("Rabat", "25.00", "24-48h", "600.00"),
            ("Marrakech", "35.00", "48-72h", "700.00"),
            ("Agadir", "40.00", "48-96h", "800.00"),
            ("Tanger", "35.00", "48-72h", "700.00"),
            ("Fes", "35.00", "48-72h", "700.00"),
            ("Meknes", "35.00", "48-72h", "700.00"),
            ("Oujda", "45.00", "72-96h", "850.00"),
        ]
        return {
            city: DeliveryZone.objects.update_or_create(
                city=city,
                defaults={
                    "shipping_price": Decimal(price),
                    "estimated_delivery_time": eta,
                    "free_delivery_threshold": Decimal(free),
                    "cash_on_delivery_available": True,
                    "is_active": True,
                },
            )[0]
            for city, price, eta, free in data
        }

    def seed_categories(self):
        categories = {}
        for index, (name, slug, description) in enumerate(CATEGORY_DATA, start=1):
            category, _ = Category.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": description,
                    "display_order": index,
                    "is_active": True,
                    "is_archived": False,
                    "seo_title": DEMO_SEO_MARK,
                    "seo_description": description,
                },
            )
            self.attach_image(category.image, f"categories/{slug}.png", name, "Categorie", (1200, 800))
            category.save(update_fields=["image"])
            categories[slug] = category
        return categories

    def seed_brands(self):
        return {name: Brand.objects.update_or_create(name=name, defaults={"is_active": True})[0] for name in BRANDS}

    def seed_attributes(self):
        color_attr, _ = ProductAttribute.objects.update_or_create(name="Couleur", defaults={"slug": "couleur"})
        capacity_attr, _ = ProductAttribute.objects.update_or_create(name="Capacite", defaults={"slug": "capacite"})
        size_attr, _ = ProductAttribute.objects.update_or_create(name="Taille", defaults={"slug": "taille"})
        colors = {
            value: AttributeValue.objects.update_or_create(attribute=color_attr, value=value, defaults={"color_hex": color})[0]
            for value, color in [
                ("Noir", "#111827"),
                ("Bleu", "#0EA5E9"),
                ("Blanc", "#F8FAFC"),
                ("Vert", "#16A34A"),
                ("Titane", "#71717A"),
                ("Argent", "#CBD5E1"),
                ("Gris", "#64748B"),
                ("Beige", "#D6C7A1"),
                ("Rose", "#FB7185"),
                ("Inox", "#94A3B8"),
                ("Rouge", "#DC2626"),
                ("Transparent", "#E0F2FE"),
                ("Violet", "#7C3AED"),
                ("Ambre", "#F59E0B"),
            ]
        }
        return {"color": color_attr, "capacity": capacity_attr, "size": size_attr, "colors": colors}

    def seed_products(self, categories, brands, attributes, actor):
        products = []
        for index, item in enumerate(PRODUCTS, start=1):
            category_slug, name, brand_name, price, promo, short, color, option = item
            sku = f"DEMO-{index:04d}"
            description = self.long_description(name, short)
            product, _ = Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "slug": slugify(name),
                    "short_description": short,
                    "description": description,
                    "category": categories[category_slug],
                    "brand": brands[brand_name],
                    "regular_price": Decimal(price),
                    "promotional_price": Decimal(promo) if promo else None,
                    "cost_price": Decimal(price) * Decimal("0.70"),
                    "tax_rate": Decimal("0.00"),
                    "low_stock_threshold": 6,
                    "weight_grams": 200 + index * 35,
                    "dimensions": self.dimensions_for(category_slug),
                    "status": Product.Status.ACTIVE,
                    "featured": index % 4 == 0 or index in {1, 6, 11, 26, 36, 41},
                    "new_arrival": index % 5 in {0, 1},
                    "bestseller": index % 6 in {0, 2},
                    "seo_title": f"{name} | DOLPHIN",
                    "seo_description": short,
                    "view_count": 40 + index * 9,
                    "sales_count": 3 + index % 11,
                    "source_type": Product.SourceType.DEMO,
                },
            )
            product.subcategories.set([categories["promotions"]] if promo and category_slug != "promotions" else [])
            self.seed_product_images(product, category_slug, index)
            self.seed_variants(product, attributes, color, option, index)
            StockMovement.objects.update_or_create(
                variant=product.variants.first(),
                movement_type=StockMovement.Type.IN,
                reason="Demo initial stock",
                defaults={"quantity": 20 + (index % 23), "actor": actor},
            )
            products.append(product)
        return products

    def seed_product_images(self, product, category_slug, index):
        existing = {image.display_order: image for image in product.images.all()}
        for order in range(3):
            filename = f"products/{product.sku.lower()}-{order + 1}.png"
            image = existing.get(order) or ProductImage(product=product, display_order=order)
            image.alt_text = f"{product.name} - vue {order + 1}"
            image.is_main = order == 0
            self.attach_image(image.image, filename, product.name, category_slug.replace("-", " ").title(), (1200, 1200), seed=f"{product.sku}-{order}")
            image.save()
        product.images.exclude(display_order__in=[0, 1, 2]).delete()

    def seed_variants(self, product, attributes, color, option, index):
        color_value = attributes["colors"].get(color) or next(iter(attributes["colors"].values()))
        option_attr = attributes["capacity"] if any(token in option.lower() for token in ["go", "w", "ml", "l", "pieces", "pouces", "mAh".lower()]) else attributes["size"]
        option_value, _ = AttributeValue.objects.update_or_create(attribute=option_attr, value=option, defaults={})
        variants = [
            (f"{product.sku}-STD", None, [color_value, option_value], 18 + index),
            (f"{product.sku}-ALT", Decimal("50.00") if product.regular_price > Decimal("500.00") else None, [option_value], 9 + index % 12),
        ]
        seen = []
        for sku, price_delta, values, quantity in variants:
            price_override = product.current_price + price_delta if price_delta else None
            variant, _ = ProductVariant.objects.update_or_create(
                product=product,
                sku=sku,
                defaults={"price_override": price_override, "is_active": True},
            )
            variant.values.set(values)
            Inventory.objects.update_or_create(variant=variant, defaults={"quantity": quantity, "reserved_quantity": index % 3})
            seen.append(variant.id)
        product.variants.exclude(id__in=seen).update(is_active=False)

    def seed_coupons(self):
        now = timezone.now()
        data = [
            ("WELCOME10", Coupon.DiscountType.PERCENT, "10.00", "150.00", 500, True),
            ("DOLPHIN20", Coupon.DiscountType.PERCENT, "20.00", "800.00", 200, False),
            ("CASA50", Coupon.DiscountType.FIXED, "50.00", "400.00", 150, False),
        ]
        coupons = {}
        for code, discount_type, value, minimum, max_usage, first_order in data:
            coupons[code] = Coupon.objects.update_or_create(
                code=code,
                defaults={
                    "discount_type": discount_type,
                    "value": Decimal(value),
                    "minimum_amount": Decimal(minimum),
                    "starts_at": now - timedelta(days=1),
                    "ends_at": now + timedelta(days=120),
                    "max_usage": max_usage,
                    "max_usage_per_customer": 2,
                    "first_order_only": first_order,
                    "is_active": True,
                },
            )[0]
        return coupons

    def seed_promotions(self, products, categories):
        now = timezone.now()
        promo, _ = Promotion.objects.update_or_create(
            name="Demo | Offres de lancement",
            defaults={
                "discount_type": Promotion.DiscountType.PERCENT,
                "value": Decimal("15.00"),
                "minimum_amount": Decimal("0.00"),
                "starts_at": now - timedelta(days=1),
                "ends_at": now + timedelta(days=45),
                "is_active": True,
            },
        )
        promo.products.set([product for product in products if product.promotional_price][:20])
        promo.categories.set([categories["promotions"], categories["accessoires"]])

    def seed_banners(self):
        now = timezone.now()
        banners = [
            ("Demo | DOLPHIN, votre marketplace au Maroc", "Smartphones, maison, gaming et beaute avec livraison par ville.", "Decouvrir les produits", "/catalogue"),
            ("Demo | Promotions de la semaine", "Des offres claires sur les essentiels tech, maison et mode.", "Voir les promotions", "/catalogue?promotion=true"),
            ("Demo | Nouveautes selectionnees", "Des produits utiles, bien presentes et prets pour la demo client.", "Explorer", "/nouveautes"),
        ]
        for index, (title, subtitle, cta, url) in enumerate(banners, start=1):
            banner, _ = HomepageBanner.objects.update_or_create(
                title=title,
                defaults={
                    "subtitle": subtitle,
                    "cta_label": cta,
                    "cta_url": url,
                    "starts_at": now - timedelta(days=1),
                    "ends_at": now + timedelta(days=90),
                    "is_active": True,
                },
            )
            self.attach_image(banner.image, f"banners/home-{index}.png", title.replace("Demo | ", ""), "DOLPHIN", (1600, 700), seed=f"banner-{index}")
            banner.save(update_fields=["image"])

    def seed_customers(self):
        User = get_user_model()
        password = config("DEMO_CUSTOMER_PASSWORD", default="")
        if not password:
            self.stdout.write(self.style.WARNING("DEMO_CUSTOMER_PASSWORD is not set; demo customers are created without usable passwords."))
        customers = []
        for index, (email_or_user, first_name, last_name, city) in enumerate(CUSTOMERS, start=1):
            email = email_or_user if "@" in email_or_user else f"{email_or_user}@{DEMO_DOMAIN}"
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "username": email.split("@")[0],
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": f"+2126{index:08d}",
                    "role": User.Role.CUSTOMER,
                    "status": User.Status.ACTIVE,
                },
            )
            user.first_name = first_name
            user.last_name = last_name
            user.phone = user.phone or f"+2126{index:08d}"
            user.role = User.Role.CUSTOMER
            user.status = User.Status.ACTIVE
            if created:
                user.set_password(password) if password else user.set_unusable_password()
            user.save()
            CustomerProfile.objects.update_or_create(user=user, defaults={"marketing_opt_in": index % 2 == 0})
            CustomerAddress.objects.update_or_create(
                user=user,
                label="Maison",
                defaults={
                    "full_name": f"{first_name} {last_name}",
                    "phone": user.phone,
                    "address_line1": f"{12 + index} Avenue Hassan II",
                    "address_line2": "Residence DOLPHIN Demo",
                    "city": city,
                    "postal_code": f"{20000 + index}",
                    "is_default": True,
                },
            )
            Wishlist.objects.get_or_create(user=user)
            customers.append(user)
        return customers

    def seed_customer_content(self, customers, products):
        comments = [
            "Produit conforme, livraison rapide et emballage soigne.",
            "Tres bon rapport qualite prix pour une utilisation quotidienne.",
            "La fiche produit est claire et le produit fonctionne comme prevu.",
            "Commande recue en bon etat, je recommande cette selection.",
            "Service pratique, prix correct et suivi simple.",
        ]
        for index, user in enumerate(customers):
            wishlist, _ = Wishlist.objects.get_or_create(user=user)
            WishlistItem.objects.update_or_create(wishlist=wishlist, product=products[index % len(products)])
            WishlistItem.objects.update_or_create(wishlist=wishlist, product=products[(index + 7) % len(products)])
            for offset in range(2):
                product = products[(index * 3 + offset) % len(products)]
                ProductReview.objects.update_or_create(
                    product=product,
                    user=user,
                    order_item=None,
                    defaults={
                        "rating": 3 + ((index + offset) % 3),
                        "comment": comments[(index + offset) % len(comments)],
                        "status": ProductReview.Status.APPROVED,
                        "verified_purchase": offset == 0,
                    },
                )
            CustomerNotification.objects.update_or_create(
                user=user,
                title="Bienvenue chez DOLPHIN",
                defaults={"message": "Votre espace demo est pret avec favoris, commandes et notifications.", "is_read": index % 2 == 0},
            )

    def seed_orders(self, customers, products, zones, coupons, manager, operator):
        statuses = [
            Order.Status.PENDING,
            Order.Status.CONFIRMED,
            Order.Status.PREPARING,
            Order.Status.SHIPPED,
            Order.Status.OUT_FOR_DELIVERY,
            Order.Status.DELIVERED,
            Order.Status.CANCELLED,
        ]
        cities = list(zones.keys())
        for index in range(24):
            user = customers[index % len(customers)]
            city = cities[index % len(cities)]
            zone = zones[city]
            email = user.email if user.email.endswith(f"@{DEMO_DOMAIN}") else f"guest-{index + 1}@{DEMO_DOMAIN}"
            order_number = f"DEMO-{timezone.now():%Y%m%d}-{index + 1:04d}"
            product_a = products[(index * 2) % len(products)]
            product_b = products[(index * 2 + 9) % len(products)]
            qty_a = 1 + index % 2
            qty_b = 1
            subtotal = product_a.current_price * qty_a + product_b.current_price * qty_b
            coupon = coupons["WELCOME10"] if index % 5 == 0 else None
            discount = (subtotal * Decimal("0.10")).quantize(Decimal("0.01")) if coupon else Decimal("0.00")
            shipping = Decimal("0.00") if subtotal >= (zone.free_delivery_threshold or Decimal("999999.00")) else zone.shipping_price
            total = subtotal - discount + shipping
            status = statuses[index % len(statuses)]
            order, _ = Order.objects.update_or_create(
                order_number=order_number,
                defaults={
                    "user": user if user.email.endswith(f"@{DEMO_DOMAIN}") else None,
                    "guest_email": "" if user.email.endswith(f"@{DEMO_DOMAIN}") else email,
                    "status": status,
                    "payment_method": Order.PaymentMethod.COD,
                    "delivery_zone": zone,
                    "shipping_full_name": f"{user.first_name} {user.last_name}",
                    "shipping_phone": user.phone or "+212612345678",
                    "shipping_address": f"{20 + index} Boulevard DOLPHIN",
                    "shipping_city": city,
                    "subtotal": subtotal,
                    "discount_total": discount,
                    "shipping_total": shipping,
                    "tax_total": Decimal("0.00"),
                    "total": total,
                    "coupon_code": coupon.code if coupon else "",
                    "customer_note": "Commande demo generee pour presentation.",
                    "internal_note": "Demo seed_store",
                    "tracking_number": f"DLF{index + 1:08d}" if status in {Order.Status.SHIPPED, Order.Status.OUT_FOR_DELIVERY, Order.Status.DELIVERED} else "",
                },
            )
            OrderItem.objects.filter(order=order).delete()
            for product, quantity in [(product_a, qty_a), (product_b, qty_b)]:
                variant = product.variants.filter(is_active=True).first()
                values = ", ".join(variant.values.values_list("value", flat=True))
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    variant=variant,
                    product_name=product.name,
                    variant_label=values,
                    sku=variant.sku,
                    unit_price=variant.price,
                    quantity=quantity,
                    total=variant.price * quantity,
                )
            Payment.objects.update_or_create(order=order, defaults={"method": order.payment_method, "status": "PAID" if status == Order.Status.DELIVERED else "PENDING", "amount": total, "reference": f"PAY-DEMO-{index + 1:04d}"})
            OrderStatusHistory.objects.filter(order=order).delete()
            OrderStatusHistory.objects.create(order=order, to_status=Order.Status.PENDING, actor=manager, note="Commande demo creee")
            if status != Order.Status.PENDING:
                OrderStatusHistory.objects.create(order=order, from_status=Order.Status.PENDING, to_status=status, actor=operator, note="Statut demo")
            if coupon:
                CouponUsage.objects.update_or_create(coupon=coupon, order=order, defaults={"user": order.user, "guest_email": order.guest_email})

    def print_summary(self):
        counts = {
            "categories": Category.objects.filter(seo_title=DEMO_SEO_MARK).count(),
            "products": Product.objects.filter(source_type=Product.SourceType.DEMO).count(),
            "customers": get_user_model().objects.filter(email__endswith=f"@{DEMO_DOMAIN}").count() + get_user_model().objects.filter(email="client@dolphin.local").count(),
            "orders": Order.objects.filter(order_number__startswith="DEMO-").count(),
            "images": ProductImage.objects.filter(product__source_type=Product.SourceType.DEMO).count(),
        }
        self.stdout.write(self.style.SUCCESS("DOLPHIN store demo data seeded."))
        self.stdout.write(f"Categories: {counts['categories']} | Products: {counts['products']} | Images: {counts['images']} | Customers: {counts['customers']} | Orders: {counts['orders']}")
        self.stdout.write("Images saved in backend/media/products, backend/media/categories and backend/media/banners.")

    def attach_image(self, image_field, filename, title, subtitle, size, seed=""):
        upload_to = str(image_field.field.upload_to).strip("/")
        storage_name = filename
        if upload_to and storage_name.startswith(f"{upload_to}/"):
            storage_name = storage_name[len(upload_to) + 1 :]
        final_name = f"{upload_to}/{storage_name}" if upload_to else storage_name
        if image_field and image_field.name == final_name and os.path.exists(image_field.path):
            return
        image_field.save(storage_name, ContentFile(self.image_bytes(title, subtitle, size, seed or filename)), save=False)

    def image_bytes(self, title, subtitle, size, seed):
        width, height = size
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        color_a = tuple(int(digest[i : i + 2], 16) for i in (0, 2, 4))
        color_b = tuple(int(digest[i : i + 2], 16) for i in (6, 8, 10))
        img = Image.new("RGB", size, "#f8fafc")
        draw = ImageDraw.Draw(img)
        for y in range(height):
            ratio = y / max(height - 1, 1)
            mixed = tuple(int(color_a[i] * (1 - ratio) + color_b[i] * ratio) for i in range(3))
            draw.line([(0, y), (width, y)], fill=mixed)
        margin = int(width * 0.08)
        box = [margin, margin, width - margin, height - margin]
        draw.rounded_rectangle(box, radius=28, fill=(255, 255, 255), outline=(226, 232, 240), width=3)
        font_large = self.font(max(30, int(width * 0.055)))
        font_small = self.font(max(18, int(width * 0.028)))
        wrapped = textwrap.wrap(title, width=24)
        y = int(height * 0.35)
        for line in wrapped[:3]:
            bbox = draw.textbbox((0, 0), line, font=font_large)
            draw.text(((width - (bbox[2] - bbox[0])) / 2, y), line, fill="#0f172a", font=font_large)
            y += bbox[3] - bbox[1] + 10
        bbox = draw.textbbox((0, 0), subtitle, font=font_small)
        draw.text(((width - (bbox[2] - bbox[0])) / 2, min(y + 10, height - margin * 2)), subtitle, fill="#0369a1", font=font_small)
        output = BytesIO()
        img.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def font(self, size):
        for path in ("arial.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"):
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
        return ImageFont.load_default()

    def long_description(self, name, short):
        return (
            f"{name} est selectionne pour offrir une experience fiable et simple au quotidien. "
            f"{short} Le produit est prepare pour une fiche e-commerce complete avec prix clair, stock visible, variantes disponibles et livraison configuree par ville au Maroc. "
            "Cette fiche demo permet de presenter le catalogue DOLPHIN avec un contenu commercial propre, sans texte generique."
        )

    def dimensions_for(self, category_slug):
        return {
            "smartphones": "16 x 8 x 1 cm",
            "informatique": "35 x 24 x 2 cm",
            "audio-ecouteurs": "18 x 16 x 8 cm",
            "montres-connectees": "5 x 5 x 2 cm",
            "accessoires": "12 x 8 x 3 cm",
            "maison-cuisine": "38 x 28 x 24 cm",
            "beaute-soins": "18 x 8 x 8 cm",
            "gaming": "45 x 35 x 18 cm",
            "mode": "32 x 24 x 10 cm",
            "promotions": "30 x 20 x 12 cm",
        }.get(category_slug, "20 x 15 x 10 cm")
