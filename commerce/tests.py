from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from .importers.validators import TEMPLATE_COLUMNS
from .models import Brand, Cart, CartItem, Category, Coupon, DeliveryZone, Inventory, Order, Product, ProductImportJob, ProductImage, ProductVariant


class CommerceApiTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(email="admin@test.local", username="admin", password="Password123!", role=User.Role.SUPER_ADMIN, is_staff=True, is_superuser=True)
        self.customer = User.objects.create_user(email="client@test.local", username="client", password="Password123!", role=User.Role.CUSTOMER)
        self.category = Category.objects.create(name="Electronique")
        self.brand = Brand.objects.create(name="Dolphin")
        self.product = Product.objects.create(
            name="Telephone test",
            sku="TST-001",
            category=self.category,
            brand=self.brand,
            regular_price=Decimal("100.00"),
            status=Product.Status.ACTIVE,
        )
        self.variant = ProductVariant.objects.create(product=self.product, sku="TST-001-BLEU")
        Inventory.objects.create(variant=self.variant, quantity=3)
        self.zone = DeliveryZone.objects.create(city="Casablanca", shipping_price=Decimal("25.00"))
        self.coupon = Coupon.objects.create(
            code="TEST10",
            discount_type=Coupon.DiscountType.PERCENT,
            value=Decimal("10.00"),
            minimum_amount=Decimal("50.00"),
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        self.client = APIClient()

    def login(self, user):
        self.client.force_authenticate(user=user)

    def test_customer_registration_disabled_and_admin_login_works(self):
        response = self.client.post("/api/v1/auth/register/", {"email": "new@test.local", "username": "new", "password": "Password123!", "first_name": "New", "last_name": "Client"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(email="new@test.local").exists())
        response = self.client.post("/api/v1/auth/login/", {"email": "admin@test.local", "password": "Password123!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_customer_cannot_create_category(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/categories/", {"name": "Interdit"})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_category(self):
        self.login(self.admin)
        response = self.client.post("/api/v1/categories/", {"name": "Maison"})
        self.assertEqual(response.status_code, 201)

    def test_admin_product_crud_variants_and_duplicate_sku(self):
        self.login(self.admin)
        payload = {
            "name": "Produit reel",
            "sku": "REAL-001",
            "category_id": self.category.id,
            "brand_id": self.brand.id,
            "regular_price": "250.00",
            "promotional_price": "220.00",
            "low_stock_threshold": 4,
            "status": "ACTIVE",
            "featured": True,
            "variants_payload": [{"sku": "REAL-001-BLEU-M", "stock": 8, "color": "Bleu", "size": "M", "capacity": "128GB"}],
        }
        response = self.client.post("/api/v1/products/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(sku="REAL-001")
        self.assertEqual(product.variants.first().inventory.quantity, 8)
        response = self.client.post("/api/v1/products/", payload, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(f"/api/v1/products/{product.slug}/", {"regular_price": "260.00"}, format="json")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/v1/products/{product.slug}/archive/")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/v1/products/{product.slug}/restore/")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(f"/api/v1/products/{product.slug}/duplicate/")
        self.assertEqual(response.status_code, 201)

    def test_customer_cannot_create_product(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/products/", {"name": "Interdit", "sku": "NOPE"}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_product_image_upload_and_main_image(self):
        self.login(self.admin)
        image_buffer = BytesIO()
        Image.new("RGB", (250, 250), color="blue").save(image_buffer, format="PNG")
        image_buffer.seek(0)
        upload = SimpleUploadedFile("product.png", image_buffer.read(), content_type="image/png")
        response = self.client.post(f"/api/v1/products/{self.product.slug}/upload_images/", {"images": [upload]}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        image = ProductImage.objects.get(product=self.product)
        self.assertTrue(image.is_main)
        response = self.client.post(f"/api/v1/products/{self.product.slug}/set_main_image/", {"image_id": image.id}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_csv_import_preview_commit_and_invalid_rows(self):
        self.login(self.admin)
        valid = dict.fromkeys(TEMPLATE_COLUMNS, "")
        valid.update({"name": "Import valide", "sku": "IMP-001", "category": "Nouvelle categorie", "brand": "Nouvelle marque", "regular_price": "150.00", "stock": "6", "is_active": "true", "is_featured": "true"})
        invalid = dict.fromkeys(TEMPLATE_COLUMNS, "")
        invalid.update({"name": "", "sku": "IMP-BAD", "category": "", "regular_price": "abc"})
        rows = [",".join(TEMPLATE_COLUMNS), ",".join(str(valid[col]) for col in TEMPLATE_COLUMNS), ",".join(str(invalid[col]) for col in TEMPLATE_COLUMNS)]
        upload = SimpleUploadedFile("products.csv", ("\n".join(rows)).encode("utf-8"), content_type="text/csv")
        response = self.client.post("/api/v1/admin/product-imports/preview/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        job_id = response.data["id"]
        self.assertEqual(ProductImportJob.objects.get(pk=job_id).total_rows, 2)
        response = self.client.post(f"/api/v1/admin/product-imports/{job_id}/commit/", {"update_existing": False, "skip_duplicates": True, "create_missing_relations": True}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Product.objects.filter(sku="IMP-001").exists())
        self.assertFalse(Product.objects.filter(sku="IMP-BAD").exists())
        self.assertEqual(response.data["failed_count"], 1)

    def test_csv_import_duplicate_sku_skip_and_update(self):
        self.login(self.admin)
        row = dict.fromkeys(TEMPLATE_COLUMNS, "")
        row.update({"name": "Telephone maj", "sku": "TST-001", "category": self.category.name, "regular_price": "130.00", "stock": "5", "is_active": "true"})
        upload = SimpleUploadedFile("dupe.csv", ("\n".join([",".join(TEMPLATE_COLUMNS), ",".join(str(row[col]) for col in TEMPLATE_COLUMNS)])).encode("utf-8"), content_type="text/csv")
        preview = self.client.post("/api/v1/admin/product-imports/preview/", {"file": upload}, format="multipart")
        self.assertTrue(preview.data["rows"][0]["duplicate_sku"])
        skipped = self.client.post(f"/api/v1/admin/product-imports/{preview.data['id']}/commit/", {"update_existing": False, "skip_duplicates": True}, format="json")
        self.assertEqual(skipped.data["skipped_count"], 1)
        updated = self.client.post(f"/api/v1/admin/product-imports/{preview.data['id']}/commit/", {"update_existing": True, "skip_duplicates": False}, format="json")
        self.assertEqual(updated.data["updated_count"], 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.regular_price, Decimal("130.00"))

    def test_product_filtering(self):
        response = self.client.get(f"/api/v1/products/?category={self.category.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_guest_cart_coupon_checkout_and_stock_deduction(self):
        headers = {"HTTP_X_SESSION_KEY": "guest-session-test"}
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 2}, **headers)
        self.assertEqual(response.status_code, 201)
        response = self.client.post("/api/v1/cart/coupon/", {"code": "TEST10"}, **headers)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            "/api/v1/checkout/",
            {
                "guest_email": "guest@test.local",
                "shipping_full_name": "Client Test",
                "shipping_phone": "+212612345678",
                "shipping_address": "1 Rue Test",
                "shipping_city": "Casablanca",
                "delivery_zone_id": self.zone.id,
                "payment_method": "COD",
            },
            **headers,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get()
        self.assertIsNone(order.user)
        self.assertEqual(order.guest_email, "guest@test.local")
        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity, 1)

    def test_prevents_overselling(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 4})
        self.assertEqual(response.status_code, 400)

    def test_status_transition_rules(self):
        order = Order.objects.create(
            user=self.customer,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Client",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("25.00"),
            total=Decimal("125.00"),
        )
        self.login(self.admin)
        response = self.client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "DELIVERED"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "CONFIRMED"})
        self.assertEqual(response.status_code, 200)

    def test_admin_can_edit_order_details_without_changing_status_directly(self):
        order = Order.objects.create(
            user=self.customer,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Client",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("25.00"),
            total=Decimal("125.00"),
        )
        self.login(self.admin)
        response = self.client.patch(f"/api/v1/orders/{order.id}/", {"status": "CANCELLED"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(
            f"/api/v1/orders/{order.id}/update_details/",
            {"shipping_phone": "+212612345679", "internal_note": "Client demande rappel"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.shipping_phone, "+212612345679")
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_admin_can_cancel_order_with_required_reason(self):
        order = Order.objects.create(
            user=self.customer,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Client",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("25.00"),
            total=Decimal("125.00"),
        )
        self.login(self.admin)
        response = self.client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "CANCELLED"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            f"/api/v1/orders/{order.id}/transition/",
            {"status": "CANCELLED", "cancellation_reason": "NO_RESPONSE_2", "note": "Deux appels sans retour"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertIn("Pas reponse 2", order.internal_note)

    def test_admin_can_cancel_order_with_voicemail_reason(self):
        order = Order.objects.create(
            user=self.customer,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Client",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("25.00"),
            total=Decimal("125.00"),
        )
        self.login(self.admin)
        response = self.client.post(
            f"/api/v1/orders/{order.id}/transition/",
            {"status": "CANCELLED", "cancellation_reason": "VOICEMAIL"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertIn("Boite vocale", order.internal_note)

    def test_developer_endpoints_require_super_admin(self):
        self.login(self.customer)
        response = self.client.get("/api/v1/developer/system/")
        self.assertEqual(response.status_code, 403)

        manager = get_user_model().objects.create_user(email="manager@test.local", username="manager", password="Password123!", role=get_user_model().Role.MANAGER, is_staff=True)
        self.login(manager)
        response = self.client.get("/api/v1/developer/system/")
        self.assertEqual(response.status_code, 403)

        self.login(self.admin)
        response = self.client.get("/api/v1/developer/system/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("SECRET_KEY", str(response.data))
        self.assertNotIn("PASSWORD", str(response.data))

    def test_developer_can_view_audit_logs_and_users(self):
        self.login(self.admin)
        response = self.client.get("/api/v1/developer/audit-logs/")
        self.assertEqual(response.status_code, 200)
        response = self.client.get("/api/v1/admin/staff/")
        self.assertEqual(response.status_code, 200)

    def test_cannot_delete_last_active_developer(self):
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/admin/staff/{self.admin.id}/")
        self.assertEqual(response.status_code, 400)

    def test_create_developer_command_keeps_existing_password_without_flag(self):
        call_command("create_developer", email="admin@test.local", first_name="Admin", last_name="Developer")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, get_user_model().Role.SUPER_ADMIN)
        self.assertTrue(self.admin.check_password("Password123!"))
