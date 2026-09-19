from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from .importers.validators import TEMPLATE_COLUMNS
from .models import AuditLog, Brand, Cart, CartItem, Category, Coupon, DeliveryZone, Expense, Inventory, NewsletterSubscriber, Order, OrderItem, Product, ProductImportJob, ProductImage, ProductVariant, Refund, ReturnItem, ReturnRequest, StockMovement, Supplier, SupportTicket


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

    def test_customer_registration_and_admin_login_work(self):
        response = self.client.post("/api/v1/auth/register/", {"email": "new@test.local", "username": "new", "password": "Password123!", "first_name": "New", "last_name": "Client"})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("access", response.data)
        self.assertTrue(get_user_model().objects.filter(email="new@test.local", role=get_user_model().Role.CUSTOMER).exists())
        response = self.client.post("/api/v1/auth/login/", {"email": "admin@test.local", "password": "Password123!"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(response.data["user"]["page_permissions"], get_user_model().ADMIN_PAGE_PERMISSIONS)

    def test_customer_cannot_create_category(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/categories/", {"name": "Interdit"})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_category(self):
        self.login(self.admin)
        response = self.client.post("/api/v1/categories/", {"name": "Maison"})
        self.assertEqual(response.status_code, 201)

    def test_admin_customer_list_and_status_update(self):
        self.login(self.admin)
        response = self.client.get("/api/v1/admin/customers/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        response = self.client.patch(f"/api/v1/admin/customers/{self.customer.id}/status/", {"status": "BLOCKED"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.status, get_user_model().Status.BLOCKED)

    def test_admin_customer_orders_detail(self):
        order = Order.objects.create(
            user=self.customer,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Client",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("0.00"),
            total=Decimal("100.00"),
        )
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        self.login(self.admin)
        response = self.client.get(f"/api/v1/admin/customers/{self.customer.id}/orders/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["order_number"], order.order_number)
        self.assertEqual(response.data[0]["items"][0]["product_name"], self.product.name)

    def test_admin_customers_include_guest_checkout_clients(self):
        order = Order.objects.create(
            guest_email="guest-checkout@test.local",
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Guest Client",
            shipping_phone="0663336488",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("0.00"),
            total=Decimal("100.00"),
        )
        second_order = Order.objects.create(
            guest_email="guest-second@test.local",
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Guest Client",
            shipping_phone="+212663336488",
            shipping_address="Adresse 2",
            shipping_city="Casablanca",
            subtotal=Decimal("50.00"),
            shipping_total=Decimal("0.00"),
            total=Decimal("50.00"),
        )
        self.login(self.admin)
        response = self.client.get("/api/v1/admin/customers/?source=GUEST")
        self.assertEqual(response.status_code, 200, response.data)
        guest = next(row for row in response.data["results"] if row["first_name"] == "Guest Client")
        self.assertEqual(guest["source"], "GUEST")
        self.assertEqual(guest["order_count"], 2)
        response = self.client.get(f"/api/v1/admin/customers/{guest['id']}/orders/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row["order_number"] for row in response.data}, {order.order_number, second_order.order_number})

    def test_admin_product_crud_variants_and_duplicate_sku(self):
        self.login(self.admin)
        payload = {
            "name": "Produit reel",
            "sku": "REAL-001",
            "category_id": self.category.id,
            "brand_id": self.brand.id,
            "regular_price": "250.00",
            "promotional_price": "220.00",
            "status": "ACTIVE",
            "featured": True,
            "variants_payload": [{"sku": "REAL-001-BLEU-M", "color": "Bleu", "size": "M", "capacity": "128GB"}],
        }
        response = self.client.post("/api/v1/products/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(sku="REAL-001")
        self.assertEqual(product.variants.count(), 1)
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

    def test_admin_can_manage_promotions(self):
        self.login(self.admin)
        payload = {
            "name": "Promo test",
            "discount_type": "PERCENT",
            "value": "15.00",
            "minimum_amount": "50.00",
            "starts_at": (timezone.now() - timedelta(days=1)).isoformat(),
            "ends_at": (timezone.now() + timedelta(days=7)).isoformat(),
            "is_active": True,
            "products": [self.product.id],
            "categories": [self.category.id],
        }
        response = self.client.post("/api/v1/promotions/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        promotion_id = response.data["id"]
        self.assertEqual(response.data["products"], [self.product.id])
        response = self.client.patch(f"/api/v1/promotions/{promotion_id}/", {"is_active": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["is_active"])
        response = self.client.delete(f"/api/v1/promotions/{promotion_id}/")
        self.assertEqual(response.status_code, 204)

    def test_admin_product_can_have_zero_variants(self):
        self.login(self.admin)
        payload = {
            "name": "Produit sans variante",
            "sku": "NO-VARIANT-001",
            "category_id": self.category.id,
            "regular_price": "120.00",
            "status": "ACTIVE",
            "variants_payload": [],
        }
        response = self.client.post("/api/v1/products/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(sku="NO-VARIANT-001")
        self.assertEqual(product.variants.count(), 0)

    def test_admin_can_remove_variant_without_losing_order_snapshot(self):
        self.login(self.admin)
        second = ProductVariant.objects.create(product=self.product, sku="TST-001-ROUGE")
        Inventory.objects.create(variant=second, quantity=3)
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
        OrderItem.objects.create(order=order, product=self.product, variant=second, product_name=self.product.name, sku=second.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))

        response = self.client.patch(
            f"/api/v1/products/{self.product.slug}/",
            {"variants_payload": [{"id": self.variant.id, "sku": self.variant.sku}]},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(ProductVariant.objects.filter(pk=second.pk).exists())
        item = OrderItem.objects.get(order=order)
        self.assertIsNone(item.variant_id)
        self.assertEqual(item.product_name, self.product.name)
        self.assertEqual(item.sku, second.sku)

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
        response = self.client.delete(f"/api/v1/products/{self.product.slug}/delete_image/", {"image_id": image.id}, format="json")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ProductImage.objects.filter(pk=image.id).exists())

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

    def test_public_category_visibility_depends_on_active_state(self):
        response = self.client.get("/api/v1/categories/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.category.is_active = False
        self.category.save(update_fields=["is_active"])
        response = self.client.get("/api/v1/categories/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_inactive_category_hides_public_products_and_blocks_cart(self):
        self.category.is_active = False
        self.category.save(update_fields=["is_active"])
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        response = self.client.get(f"/api/v1/products/{self.product.slug}/")
        self.assertEqual(response.status_code, 404)
        response = self.client.get("/api/v1/products/?promotion=true")
        self.assertEqual(response.data["count"], 0)
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 1})
        self.assertEqual(response.status_code, 400)

    def test_checkout_blocks_cart_item_after_category_deactivation(self):
        headers = {"HTTP_X_SESSION_KEY": "inactive-category-checkout"}
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 1}, **headers)
        self.assertEqual(response.status_code, 201, response.data)
        self.category.is_active = False
        self.category.save(update_fields=["is_active"])
        response = self.client.post(
            "/api/v1/checkout/",
            {
                "guest_email": "inactive-category@test.local",
                "shipping_full_name": "Client Test",
                "shipping_phone": "+212612345678",
                "shipping_address": "1 Rue Test",
                "shipping_city": "Casablanca",
                "delivery_zone_id": self.zone.id,
                "payment_method": "COD",
            },
            **headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_category_deactivation_keeps_order_history_and_reactivation_restores_products(self):
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
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        self.login(self.admin)
        response = self.client.post(f"/api/v1/categories/{self.category.slug}/deactivate/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["is_active"])
        self.assertTrue(AuditLog.objects.filter(action="CATEGORY_DEACTIVATED", actor=self.admin, entity_id=str(self.category.id)).exists())
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/v1/products/").data["count"], 0)
        self.login(self.customer)
        response = self.client.get(f"/api/v1/orders/{order.id}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["product_name"], self.product.name)
        self.login(self.admin)
        response = self.client.post(f"/api/v1/categories/{self.category.slug}/activate/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["is_active"])
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.data["count"], 1)

    def test_category_activation_requires_catalog_manager(self):
        self.login(self.customer)
        response = self.client.post(f"/api/v1/categories/{self.category.slug}/deactivate/")
        self.assertEqual(response.status_code, 403)

    def test_public_product_list_empty_when_no_active_products(self):
        Product.objects.update(status=Product.Status.ARCHIVED)
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_inactive_variant_is_not_public_or_orderable(self):
        self.variant.is_active = False
        self.variant.save(update_fields=["is_active"])
        response = self.client.get(f"/api/v1/products/{self.product.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["variants"], [])
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 1})
        self.assertEqual(response.status_code, 400)

    def test_super_admin_bulk_delete_removes_unlinked_products_and_writes_audit_log(self):
        self.login(self.admin)
        unlinked = Product.objects.create(name="Bulk removable", sku="BULK-REMOVE", category=self.category, regular_price=Decimal("12.00"), status=Product.Status.ACTIVE)
        ProductVariant.objects.create(product=unlinked, sku="BULK-REMOVE-V1")
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [unlinked.id], "confirmation": "SUPPRIMER TOUS LES PRODUITS"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Product.objects.filter(pk=unlinked.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="PRODUCT_BULK_DELETE", actor=self.admin).exists())

    def test_super_admin_bulk_delete_removes_ordered_products_and_keeps_order_snapshot(self):
        self.login(self.admin)
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
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [self.product.id], "confirmation": "SUPPRIMER TOUS LES PRODUITS"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())
        item = OrderItem.objects.get(order=order)
        self.assertIsNone(item.product_id)
        self.assertIsNone(item.variant_id)
        self.assertEqual(item.product_name, self.product.name)
        self.assertEqual(item.sku, self.variant.sku)
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.data["count"], 0)
        response = self.client.get(f"/api/v1/orders/{order.id}/")
        self.assertEqual(response.status_code, 401)

    def test_super_admin_bulk_delete_removes_stock_history_products(self):
        self.login(self.admin)
        StockMovement.objects.create(variant=self.variant, movement_type=StockMovement.Type.IN, quantity=5, reason="Initial stock", actor=self.admin)
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [self.product.id], "confirmation": "SUPPRIMER TOUS LES PRODUITS"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["archived"], 0)
        self.assertEqual(response.data["deleted"], 1)
        self.assertFalse(Product.objects.filter(pk=self.product.pk).exists())

    def test_bulk_delete_requires_super_admin_and_exact_confirmation(self):
        User = get_user_model()
        manager = User.objects.create_user(email="bulk-manager@test.local", username="bulk-manager", password="Password123!", role=User.Role.MANAGER, is_staff=True)
        self.login(manager)
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [self.product.id], "confirmation": "SUPPRIMER TOUS LES PRODUITS"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.login(self.customer)
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [self.product.id], "confirmation": "SUPPRIMER TOUS LES PRODUITS"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.login(self.admin)
        response = self.client.post(
            "/api/v1/products/bulk_delete/",
            {"ids": [self.product.id], "confirmation": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_guest_cart_coupon_checkout(self):
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
        self.assertEqual(order.shipping_total, Decimal("0.00"))
        self.assertEqual(order.total, order.subtotal - order.discount_total)

    def test_guest_can_checkout_product_without_variant(self):
        product = Product.objects.create(name="Sans variante panier", sku="NO-VAR-CART", category=self.category, regular_price=Decimal("75.00"), status=Product.Status.ACTIVE)
        headers = {"HTTP_X_SESSION_KEY": "guest-no-variant"}
        response = self.client.post("/api/v1/cart/add/", {"product_id": product.id, "quantity": 3}, **headers)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["variant"])
        self.assertEqual(response.data["product_name"], product.name)
        response = self.client.post(
            "/api/v1/checkout/",
            {
                "guest_email": "novariant@test.local",
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
        item = OrderItem.objects.get(order_id=response.data["id"])
        self.assertEqual(item.product_id, product.id)
        self.assertIsNone(item.variant_id)
        self.assertEqual(item.product_name, product.name)
        self.assertEqual(item.sku, product.sku)
        self.assertEqual(item.quantity, 3)

    def test_cart_update_accepts_quantity_without_stock_limit(self):
        headers = {"HTTP_X_SESSION_KEY": "guest-cart-update"}
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 1}, **headers)
        self.assertEqual(response.status_code, 201, response.data)
        item_id = response.data["id"]
        response = self.client.patch("/api/v1/cart/update_item/", {"item_id": item_id, "quantity": 2}, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.patch("/api/v1/cart/update_item/", {"item_id": item_id, "quantity": 4}, format="json", **headers)
        self.assertEqual(response.status_code, 200, response.data)

    def test_cart_list_recovers_duplicate_guest_active_carts(self):
        headers = {"HTTP_X_SESSION_KEY": "duplicate-guest-cart"}
        Cart.objects.create(session_key="duplicate-guest-cart", is_active=True)
        Cart.objects.create(session_key="duplicate-guest-cart", is_active=True)
        response = self.client.get("/api/v1/cart/", **headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Cart.objects.filter(session_key="duplicate-guest-cart", is_active=True).count(), 1)

    def test_cart_list_tolerates_item_missing_product_and_variant(self):
        cart = Cart.objects.create(session_key="missing-product-cart", is_active=True)
        CartItem.objects.create(cart=cart, quantity=1)
        response = self.client.get("/api/v1/cart/", **{"HTTP_X_SESSION_KEY": "missing-product-cart"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["line_total"], 0)
        self.assertEqual(response.data["total"], Decimal("0.00"))

    def test_cart_list_recovers_duplicate_user_active_carts(self):
        Cart.objects.create(user=self.customer, is_active=True)
        Cart.objects.create(user=self.customer, is_active=True)
        self.login(self.customer)
        response = self.client.get("/api/v1/cart/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Cart.objects.filter(user=self.customer, is_active=True).count(), 1)

    def test_cart_add_accepts_quantity_without_stock_limit(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 4})
        self.assertEqual(response.status_code, 201, response.data)

    def test_cart_add_falls_back_to_product_when_variant_is_stale(self):
        response = self.client.post("/api/v1/cart/add/", {"variant_id": 999999, "product_id": self.product.id, "quantity": 1})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["variant"])
        self.assertEqual(response.data["product_name"], self.product.name)

    def test_admin_can_override_order_status(self):
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
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DELIVERED)

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

    def test_customer_can_only_read_own_orders(self):
        own = Order.objects.create(
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
        other_user = get_user_model().objects.create_user(email="other@test.local", username="other", password="Password123!", role=get_user_model().Role.CUSTOMER)
        other = Order.objects.create(
            user=other_user,
            payment_method=Order.PaymentMethod.COD,
            delivery_zone=self.zone,
            shipping_full_name="Autre",
            shipping_phone="+212612345678",
            shipping_address="Adresse",
            shipping_city="Casablanca",
            subtotal=Decimal("100.00"),
            shipping_total=Decimal("25.00"),
            total=Decimal("125.00"),
        )
        self.login(self.customer)
        listing = self.client.get("/api/v1/orders/")
        self.assertEqual(listing.status_code, 200, listing.data)
        self.assertEqual(listing.data["count"], 1)
        self.assertEqual(listing.data["results"][0]["id"], own.id)
        detail = self.client.get(f"/api/v1/orders/{other.id}/")
        self.assertEqual(detail.status_code, 404)
        blocked = self.client.patch(f"/api/v1/orders/{own.id}/", {"internal_note": "hack"}, format="json")
        self.assertEqual(blocked.status_code, 403)
        blocked_delete = self.client.delete(f"/api/v1/orders/{own.id}/")
        self.assertEqual(blocked_delete.status_code, 405)

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

    def test_role_matrix_for_staff_catalog_orders_support_and_suppliers(self):
        User = get_user_model()
        manager = User.objects.create_user(email="manager@test.local", username="manager", password="Password123!", role=User.Role.MANAGER, status=User.Status.ACTIVE, is_staff=True)
        operator = User.objects.create_user(email="operator@test.local", username="operator", password="Password123!", role=User.Role.ORDER_OPERATOR, status=User.Status.ACTIVE, is_staff=True)
        support = User.objects.create_user(email="support@test.local", username="support", password="Password123!", role=User.Role.CUSTOMER_SUPPORT, status=User.Status.ACTIVE, is_staff=True)
        customer = self.customer

        order = Order.objects.create(
            user=customer,
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
        staff_list = self.client.get("/api/v1/admin/staff/")
        self.assertEqual(staff_list.status_code, 200)
        self.assertNotIn("client@test.local", [row["email"] for row in staff_list.data["results"]])
        self.assertEqual(self.client.post("/api/v1/developer/suppliers/", {"name": "Supplier Matrix"}, format="json").status_code, 201)
        customer_staff_response = self.client.post(
            "/api/v1/admin/staff/",
            {
                "email": "not-staff@test.local",
                "username": "not-staff@test.local",
                "password": "Password123!",
                "role": User.Role.CUSTOMER,
                "status": User.Status.ACTIVE,
            },
            format="json",
        )
        self.assertEqual(customer_staff_response.status_code, 400)

        self.login(manager)
        self.assertEqual(self.client.post("/api/v1/products/", {"name": "Manager Product", "sku": "MGR-001", "category_id": self.category.id, "regular_price": "10.00"}, format="json").status_code, 201)
        self.assertEqual(self.client.get("/api/v1/admin/staff/").status_code, 200)
        staff_response = self.client.post(
            "/api/v1/admin/staff/",
            {
                "email": "staff-created@test.local",
                "username": "staff-created@test.local",
                "password": "Password123!",
                "role": User.Role.ORDER_OPERATOR,
                "status": User.Status.ACTIVE,
                "page_permissions": ["dashboard", "orders"],
            },
            format="json",
        )
        self.assertEqual(staff_response.status_code, 201)
        self.assertEqual(staff_response.data["page_permissions"], ["dashboard", "orders"])
        self.assertEqual(self.client.get("/api/v1/developer/suppliers/").status_code, 403)

        self.login(self.admin)
        super_staff_response = self.client.post(
            "/api/v1/admin/staff/",
            {
                "email": "super-created@test.local",
                "username": "super-created@test.local",
                "password": "Password123!",
                "role": User.Role.SUPER_ADMIN,
                "status": User.Status.ACTIVE,
                "page_permissions": [],
            },
            format="json",
        )
        self.assertEqual(super_staff_response.status_code, 201, super_staff_response.data)
        self.assertEqual(super_staff_response.data["page_permissions"], User.ADMIN_PAGE_PERMISSIONS)

        self.login(operator)
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "CONFIRMED"}, format="json").status_code, 200)
        self.assertEqual(self.client.post("/api/v1/products/", {"name": "Operator Product", "sku": "OPS-001", "category_id": self.category.id, "regular_price": "10.00"}, format="json").status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/staff/").status_code, 403)

        self.login(support)
        self.assertEqual(self.client.get("/api/v1/support/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/returns/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/orders/{order.id}/transition/", {"status": "PREPARING"}, format="json").status_code, 403)
        self.assertEqual(self.client.post("/api/v1/products/", {"name": "Support Product", "sku": "SUP-001", "category_id": self.category.id, "regular_price": "10.00"}, format="json").status_code, 403)

        self.login(customer)
        self.assertEqual(self.client.get("/api/v1/support/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/orders/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/admin/staff/").status_code, 403)

    def test_checkout_idempotency_returns_existing_order(self):
        headers = {"HTTP_X_SESSION_KEY": "guest-idempotent"}
        self.client.post("/api/v1/cart/add/", {"variant_id": self.variant.id, "quantity": 1}, **headers)
        payload = {
            "guest_email": "guest-idempotent@test.local",
            "shipping_full_name": "Client Test",
            "shipping_phone": "+212612345678",
            "shipping_address": "1 Rue Test",
            "shipping_city": "Casablanca",
            "delivery_zone_id": self.zone.id,
            "payment_method": "COD",
            "idempotency_key": "checkout-key-1",
        }
        first = self.client.post("/api/v1/checkout/", payload, **headers)
        second = self.client.post("/api/v1/checkout/", payload, **headers)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Order.objects.count(), 1)
        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity, 3)

    def test_inventory_adjustment_requires_admin_role_and_reason(self):
        self.login(self.customer)
        response = self.client.post("/api/v1/developer/inventory/", {"variant_id": self.variant.id, "quantity": 9, "reason": "Test"}, format="json")
        self.assertEqual(response.status_code, 403)
        self.login(self.admin)
        response = self.client.post("/api/v1/developer/inventory/", {"variant_id": self.variant.id, "quantity": 9}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/v1/developer/inventory/", {"variant_id": self.variant.id, "quantity": 9, "reason": "Comptage manuel"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.variant.inventory.refresh_from_db()
        self.assertEqual(self.variant.inventory.quantity, 9)

    def test_invoice_pdf_endpoint_is_protected_and_returns_pdf(self):
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
        OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        response = self.client.get(f"/api/v1/orders/{order.id}/invoice/")
        self.assertEqual(response.status_code, 401)
        self.login(self.admin)
        response = self.client.get(f"/api/v1/orders/{order.id}/invoice/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_return_refund_workflow_and_refund_limit(self):
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
            status=Order.Status.DELIVERED,
        )
        item = OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        self.login(self.customer)
        created = self.client.post("/api/v1/returns/", {"order": order.id, "reason": "Produit defectueux", "items_payload": [{"order_item": item.id, "quantity": 1}]}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["items"][0]["quantity"], 1)
        return_request = ReturnRequest.objects.get(pk=created.data["id"])
        self.login(self.admin)
        approved = self.client.post(f"/api/v1/returns/{return_request.id}/approve/", {"decision": "OK"}, format="json")
        self.assertEqual(approved.status_code, 200, approved.data)
        refunded = self.client.post(f"/api/v1/returns/{return_request.id}/refund/", {"amount": "50.00", "method": "MANUAL", "reference": "RF-1"}, format="json")
        self.assertEqual(refunded.status_code, 200, refunded.data)
        self.assertEqual(Refund.objects.count(), 1)
        too_much = self.client.post("/api/v1/refunds/", {"order": order.id, "amount": "126.00", "method": "MANUAL"}, format="json")
        self.assertEqual(too_much.status_code, 400)

    def test_return_rejects_quantities_above_ordered_amount(self):
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
            status=Order.Status.DELIVERED,
        )
        item = OrderItem.objects.create(order=order, product=self.product, variant=self.variant, product_name=self.product.name, sku=self.variant.sku, unit_price=Decimal("100.00"), quantity=1, total=Decimal("100.00"))
        self.login(self.customer)
        response = self.client.post("/api/v1/returns/", {"order": order.id, "reason": "Trop", "items_payload": [{"order_item": item.id, "quantity": 2}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_wishlist_and_support_are_isolated_per_customer(self):
        other = get_user_model().objects.create_user(email="support-other@test.local", username="support-other", password="Password123!", role=get_user_model().Role.CUSTOMER)
        self.login(self.customer)
        wishlist = self.client.post("/api/v1/wishlist/", {"product_id": self.product.id}, format="json")
        self.assertEqual(wishlist.status_code, 201, wishlist.data)
        ticket = self.client.post("/api/v1/support/", {"subject": "Besoin d'aide", "priority": "NORMAL"}, format="json")
        self.assertEqual(ticket.status_code, 201, ticket.data)

        self.login(other)
        self.assertEqual(self.client.get("/api/v1/wishlist/").data["count"], 0)
        self.assertEqual(self.client.get("/api/v1/support/").data["count"], 0)

        support = get_user_model().objects.create_user(email="support-agent@test.local", username="support-agent", password="Password123!", role=get_user_model().Role.CUSTOMER_SUPPORT, is_staff=True)
        self.login(support)
        self.assertEqual(self.client.get("/api/v1/support/").data["count"], 1)
        self.assertEqual(SupportTicket.objects.count(), 1)

    def test_expenses_suppliers_and_excel_exports_are_protected(self):
        response = self.client.get("/api/v1/developer/expenses/")
        self.assertEqual(response.status_code, 401)
        self.login(self.admin)
        supplier_response = self.client.post("/api/v1/developer/suppliers/", {"name": "Fournisseur Test", "percentage_margin": "12.00"}, format="json")
        self.assertEqual(supplier_response.status_code, 201, supplier_response.data)
        supplier = Supplier.objects.get(pk=supplier_response.data["id"])
        expense_response = self.client.post("/api/v1/developer/expenses/", {"category": "Transport", "amount": "80.00", "date": timezone.localdate(), "supplier": supplier.id, "reference": "EXP-1"}, format="json")
        self.assertEqual(expense_response.status_code, 201, expense_response.data)
        self.assertEqual(Expense.objects.count(), 1)
        export_response = self.client.get(reverse("report-export", kwargs={"kind": "expenses"}), {"file_format": "xlsx"})
        self.assertEqual(export_response.status_code, 200, getattr(export_response, "data", export_response.content[:200]))
        self.assertIn("spreadsheetml", export_response["Content-Type"])
        stock_pdf = self.client.get(reverse("report-export", kwargs={"kind": "stock"}), {"file_format": "pdf", "state": "low"})
        self.assertEqual(stock_pdf.status_code, 200)
        self.assertEqual(stock_pdf["Content-Type"], "application/pdf")

    def test_customer_and_staff_exports_are_separated(self):
        manager = get_user_model().objects.create_user(email="export-manager@test.local", username="export-manager", password="Password123!", role=get_user_model().Role.MANAGER, is_staff=True)
        self.login(self.admin)
        customers = self.client.get(reverse("report-export", kwargs={"kind": "customers"}))
        self.assertEqual(customers.status_code, 200)
        customers_csv = customers.content.decode("utf-8")
        self.assertIn(self.customer.email, customers_csv)
        self.assertNotIn(self.admin.email, customers_csv)
        self.assertNotIn(manager.email, customers_csv)
        staff = self.client.get(reverse("report-export", kwargs={"kind": "staff"}))
        self.assertEqual(staff.status_code, 200)
        staff_csv = staff.content.decode("utf-8")
        self.assertIn(self.admin.email, staff_csv)
        self.assertIn(manager.email, staff_csv)
        self.assertNotIn(self.customer.email, staff_csv)

        self.login(manager)
        staff_for_manager = self.client.get(reverse("report-export", kwargs={"kind": "staff"}))
        self.assertEqual(staff_for_manager.status_code, 403)

    def test_newsletter_subscription_upserts_email(self):
        response = self.client.post("/api/v1/newsletter/subscribe/", {"email": "NEWS@Test.Local"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post("/api/v1/newsletter/subscribe/", {"email": "news@test.local"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(NewsletterSubscriber.objects.filter(email="news@test.local").count(), 1)

    def test_cannot_delete_last_active_developer(self):
        self.login(self.admin)
        response = self.client.delete(f"/api/v1/admin/staff/{self.admin.id}/")
        self.assertEqual(response.status_code, 400)

    def test_create_developer_command_keeps_existing_password_without_flag(self):
        call_command("create_developer", email="admin@test.local", first_name="Admin", last_name="Developer")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, get_user_model().Role.SUPER_ADMIN)
        self.assertTrue(self.admin.check_password("Password123!"))
