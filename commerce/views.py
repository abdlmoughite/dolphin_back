import csv
import platform
import sys
from datetime import timedelta

import django
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.db.models import Avg, Count, Q, Sum
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django_filters.rest_framework import FilterSet, NumberFilter
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import (
    Brand,
    AuditLog,
    CartItem,
    Category,
    Coupon,
    CustomerAddress,
    CustomerNotification,
    DeliveryZone,
    HomepageBanner,
    Inventory,
    Order,
    Product,
    ProductImage,
    ProductVariant,
    ProductImportJob,
    ProductReview,
    Promotion,
    ReturnRequest,
    SupportTicket,
    User,
    Wishlist,
    WishlistItem,
)
from .importers.excel_importer import build_template_workbook
from .importers.services import commit_import, preview_import
from .permissions import CanManageUsers, IsAdminRole, IsCatalogManagerOrReadOnly, IsDeveloper, IsOrderManager
from .serializers import (
    AdminProductWriteSerializer,
    AuditLogSerializer,
    BrandSerializer,
    CartItemSerializer,
    CartSerializer,
    CategorySerializer,
    ChangePasswordSerializer,
    CheckoutSerializer,
    CouponSerializer,
    CustomerAddressSerializer,
    CustomerNotificationSerializer,
    DeliveryZoneSerializer,
    DeveloperUserSerializer,
    DolphinTokenObtainPairSerializer,
    HomepageBannerSerializer,
    OrderSerializer,
    ProductReviewSerializer,
    ProductSerializer,
    ProductImportJobSerializer,
    PromotionSerializer,
    RegisterSerializer,
    ReturnRequestSerializer,
    SupportTicketSerializer,
    UserSerializer,
    WishlistItemSerializer,
)
from .services import add_cart_item, apply_coupon, cart_totals, checkout, dashboard_metrics, get_or_create_cart, transition_order


def request_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "oui"}


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class DolphinTokenObtainPairView(TokenObtainPairView):
    serializer_class = DolphinTokenObtainPairSerializer
    throttle_scope = "auth"

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        email = str(request.data.get("email", "")).strip()
        ip = client_ip(request)
        if response.status_code == 200:
            user = get_user_model().objects.filter(email=email).first()
            AuditLog.objects.create(actor=user, action="LOGIN_SUCCESS", entity="User", entity_id=str(user.pk if user else ""), after={"email": email}, ip_address=ip)
        else:
            AuditLog.objects.create(action="LOGIN_FAILED", entity="User", after={"email": email}, ip_address=ip)
        return response

    def handle_exception(self, exc):
        email = str(self.request.data.get("email", "")).strip() if hasattr(self, "request") else ""
        AuditLog.objects.create(action="LOGIN_FAILED", entity="User", after={"email": email}, ip_address=client_ip(self.request))
        return super().handle_exception(exc)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        return Response({"detail": "Les comptes client sont desactives. Commandez sans compte; seuls les comptes admin et equipe sont autorises."}, status=status.HTTP_403_FORBIDDEN)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get("refresh")
        if token:
            RefreshToken(token).blacklist()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not request.user.check_password(serializer.validated_data["current_password"]):
            return Response({"current_password": "Mot de passe actuel incorrect."}, status=400)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.token_version += 1
        request.user.save()
        return Response({"detail": "Mot de passe modifie."})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        return Response({"detail": "Si le compte existe, un email de reinitialisation sera envoye."})


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.annotate(product_count=Count("products")).order_by("display_order", "name")
    serializer_class = CategorySerializer
    permission_classes = [IsCatalogManagerOrReadOnly]
    lookup_field = "slug"
    filterset_fields = ["parent", "is_active", "is_archived"]
    search_fields = ["name", "description"]
    ordering_fields = ["display_order", "name", "created_at"]

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def archive(self, request, slug=None):
        category = self.get_object()
        category.is_archived = True
        category.is_active = False
        category.save(update_fields=["is_archived", "is_active"])
        return Response(self.get_serializer(category).data)

    @action(detail=False, methods=["post"], permission_classes=[IsAdminRole])
    def reorder(self, request):
        for item in request.data.get("items", []):
            Category.objects.filter(pk=item.get("id")).update(display_order=item.get("display_order", 0))
        return Response({"detail": "Ordre des categories mis a jour."})

    def destroy(self, request, *args, **kwargs):
        category = self.get_object()
        move_to = request.query_params.get("move_to")
        if category.products.exists():
            if not move_to:
                return Response({"detail": "Categorie liee a des produits. Indiquez move_to ou archivez-la."}, status=400)
            target = Category.objects.get(pk=move_to)
            category.products.update(category=target)
        category.delete()
        return Response(status=204)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def restore(self, request, slug=None):
        category = self.get_object()
        category.is_archived = False
        category.is_active = True
        category.save(update_fields=["is_archived", "is_active"])
        return Response(self.get_serializer(category).data)


class BrandViewSet(viewsets.ModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]
    lookup_field = "slug"
    search_fields = ["name"]


class ProductFilter(FilterSet):
    min_price = NumberFilter(field_name="regular_price", lookup_expr="gte")
    max_price = NumberFilter(field_name="regular_price", lookup_expr="lte")

    class Meta:
        model = Product
        fields = ["category", "brand", "status", "featured", "new_arrival", "bestseller"]


class ProductViewSet(viewsets.ModelViewSet):
    queryset = (
        Product.objects.select_related("category", "brand")
        .prefetch_related("images", "variants__values", "variants__inventory", "reviews")
        .annotate(average_rating=Avg("reviews__rating"))
    )
    serializer_class = ProductSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]
    lookup_field = "slug"
    filterset_class = ProductFilter
    search_fields = ["name", "sku", "short_description", "description", "brand__name", "category__name"]
    ordering_fields = ["created_at", "regular_price", "view_count", "sales_count", "average_rating"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_serializer_class(self):
        if self.request.method in {"POST", "PUT", "PATCH"}:
            return AdminProductWriteSerializer
        return ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if not (self.request.user.is_authenticated and self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            qs = qs.filter(status=Product.Status.ACTIVE)
        if self.request.query_params.get("promotion") == "true":
            qs = qs.filter(promotional_price__isnull=False)
        return qs.order_by("-created_at")

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if not (request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            Product.objects.filter(pk=instance.pk).update(view_count=instance.view_count + 1)
        return Response(ProductSerializer(instance).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return Response(ProductSerializer(product).data, status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        product = self.get_object()
        serializer = self.get_serializer(product, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return Response(ProductSerializer(product).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def archive(self, request, slug=None):
        product = self.get_object()
        product.status = Product.Status.ARCHIVED
        product.save(update_fields=["status"])
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def restore(self, request, slug=None):
        product = self.get_object()
        product.status = Product.Status.ACTIVE
        product.save(update_fields=["status"])
        return Response(self.get_serializer(product).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def duplicate(self, request, slug=None):
        product = self.get_object()
        old_pk = product.pk
        product.pk = None
        product.slug = ""
        product.sku = f"{product.sku}-COPY"
        product.name = f"{product.name} copie"
        product.status = Product.Status.DRAFT
        product.source_type = Product.SourceType.MANUAL
        product.save()
        for old_variant in Product.objects.get(pk=old_pk).variants.prefetch_related("values").all():
            variant = old_variant
            variant.pk = None
            variant.product = product
            variant.sku = f"{old_variant.sku}-COPY"
            variant.save()
            variant.values.set(old_variant.values.all())
            Inventory.objects.create(variant=variant, quantity=getattr(old_variant, "inventory", None).quantity if hasattr(old_variant, "inventory") else 0)
        return Response(ProductSerializer(product).data, status=201)

    @action(detail=False, methods=["post"], permission_classes=[IsAdminRole])
    def bulk(self, request):
        ids = request.data.get("ids", [])
        action_name = request.data.get("action")
        qs = Product.objects.filter(id__in=ids)
        if action_name == "activate":
            qs.update(status=Product.Status.ACTIVE)
        elif action_name == "deactivate":
            qs.update(status=Product.Status.DRAFT)
        elif action_name == "archive":
            qs.update(status=Product.Status.ARCHIVED)
        elif action_name == "feature":
            qs.update(featured=True)
        elif action_name == "category":
            qs.update(category_id=request.data.get("category_id"))
        else:
            return Response({"detail": "Action bulk inconnue."}, status=400)
        return Response({"detail": "Action appliquee.", "count": qs.count()})

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def upload_images(self, request, slug=None):
        product = self.get_object()
        files = request.FILES.getlist("images")
        if not files:
            return Response({"images": "Ajoutez au moins une image."}, status=400)
        created = []
        for index, file in enumerate(files):
            if file.size > 5 * 1024 * 1024:
                return Response({"images": "Chaque image doit faire moins de 5 Mo."}, status=400)
            if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                return Response({"images": "Formats acceptes: JPG, PNG, WEBP."}, status=400)
            try:
                image = Image.open(file)
                image.verify()
                width, height = image.size
                if width < 200 or height < 200 or width > 6000 or height > 6000:
                    return Response({"images": "Dimensions acceptees: 200x200 a 6000x6000 px."}, status=400)
                file.seek(0)
            except Exception:
                return Response({"images": "Image invalide."}, status=400)
            created.append(ProductImage.objects.create(product=product, image=file, is_main=not product.images.filter(is_main=True).exists() and index == 0, display_order=product.images.count() + index))
        return Response(ProductSerializer(product).data, status=201)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def set_main_image(self, request, slug=None):
        product = self.get_object()
        image = product.images.get(pk=request.data.get("image_id"))
        product.images.update(is_main=False)
        image.is_main = True
        image.save(update_fields=["is_main"])
        return Response(ProductSerializer(product).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def reorder_images(self, request, slug=None):
        product = self.get_object()
        for item in request.data.get("items", []):
            product.images.filter(pk=item.get("id")).update(display_order=item.get("display_order", 0))
        return Response(ProductSerializer(product).data)

    @action(detail=True, methods=["delete"], permission_classes=[IsAdminRole])
    def delete_image(self, request, slug=None):
        self.get_object().images.filter(pk=request.data.get("image_id")).delete()
        return Response(status=204)

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        if product.orderitem_set.exists():
            product.status = Product.Status.ARCHIVED
            product.save(update_fields=["status"])
            return Response({"detail": "Produit archive car il est lie a des commandes."})
        product.delete()
        return Response(status=204)


class CartViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

    def list(self, request):
        cart = get_or_create_cart(request)
        data = CartSerializer(cart).data
        data.update(cart_totals(cart))
        return Response(data)

    @action(detail=False, methods=["post"])
    def add(self, request):
        cart = get_or_create_cart(request)
        serializer = CartItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = add_cart_item(cart, serializer.validated_data["variant"], serializer.validated_data["quantity"])
        return Response(CartItemSerializer(item).data, status=201)

    @action(detail=False, methods=["patch"])
    def update_item(self, request):
        cart = get_or_create_cart(request)
        item = cart.items.get(pk=request.data["item_id"])
        item.quantity = max(int(request.data.get("quantity", 1)), 1)
        item.save(update_fields=["quantity"])
        return Response(CartItemSerializer(item).data)

    @action(detail=False, methods=["delete"])
    def remove(self, request):
        cart = get_or_create_cart(request)
        cart.items.filter(pk=request.data.get("item_id")).delete()
        return Response(status=204)

    @action(detail=False, methods=["post"])
    def coupon(self, request):
        cart = apply_coupon(get_or_create_cart(request), request.data.get("code", ""))
        data = CartSerializer(cart).data
        data.update(cart_totals(cart))
        return Response(data)


class CheckoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = checkout(request.user, get_or_create_cart(request), serializer.validated_data)
        return Response(OrderSerializer(order).data, status=201)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsOrderManager]
    search_fields = ["order_number", "user__email", "guest_email", "shipping_phone", "tracking_number"]
    filterset_fields = ["status", "payment_method", "shipping_city"]
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        qs = Order.objects.prefetch_related("items", "status_history")
        return qs

    def update(self, request, *args, **kwargs):
        if "status" in request.data:
            return Response({"detail": "Utilisez l'action transition pour changer le statut d'une commande."}, status=400)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if "status" in request.data:
            return Response({"detail": "Utilisez l'action transition pour changer le statut d'une commande."}, status=400)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], permission_classes=[IsOrderManager])
    def transition(self, request, pk=None):
        new_status = request.data.get("status")
        note = request.data.get("note", "")
        reason = request.data.get("cancellation_reason", "")
        if new_status == Order.Status.CANCELLED:
            allowed_reasons = {
                "NO_RESPONSE_1": "Pas reponse 1",
                "NO_RESPONSE_2": "Pas reponse 2",
                "NO_RESPONSE_3": "Pas reponse 3",
                "VOICEMAIL": "Boite vocale",
                "REFUSED": "Refuse",
                "OTHER": "Autre raison",
            }
            if reason not in allowed_reasons:
                return Response({"cancellation_reason": "Choisissez une raison d'annulation valide."}, status=400)
            note = f"Annulation: {allowed_reasons[reason]}. {note}".strip()
        order = transition_order(self.get_object(), new_status, request.user, note)
        if new_status == Order.Status.CANCELLED and note:
            order.internal_note = f"{order.internal_note}\n{note}".strip()
            order.save(update_fields=["internal_note", "updated_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["patch"], permission_classes=[IsOrderManager])
    def update_details(self, request, pk=None):
        order = self.get_object()
        allowed = {
            "shipping_full_name",
            "shipping_phone",
            "shipping_address",
            "shipping_city",
            "customer_note",
            "internal_note",
            "tracking_number",
            "guest_email",
        }
        before = {field: getattr(order, field) for field in allowed}
        changed = {}
        for field in allowed:
            if field in request.data:
                setattr(order, field, request.data[field])
                changed[field] = request.data[field]
        if not changed:
            return Response({"detail": "Aucun champ modifiable envoye."}, status=400)
        order.save(update_fields=[*changed.keys(), "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="ORDER_UPDATED",
            entity="Order",
            entity_id=str(order.pk),
            before={field: before[field] for field in changed},
            after=changed,
            ip_address=client_ip(request),
        )
        return Response(OrderSerializer(order).data)


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerAddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CustomerAddress.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        if serializer.validated_data.get("is_default"):
            self.request.user.addresses.update(is_default=False)
        serializer.save(user=self.request.user)


class WishlistViewSet(viewsets.ModelViewSet):
    serializer_class = WishlistItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        return wishlist.items.select_related("product")

    def perform_create(self, serializer):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        serializer.save(wishlist=wishlist)


class ReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ProductReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ProductReview.objects.select_related("user", "product")
        if self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}:
            return qs
        return qs.filter(Q(user=self.request.user) | Q(status=ProductReview.Status.APPROVED))

    def perform_create(self, serializer):
        product = serializer.validated_data["product"]
        eligible = Order.objects.filter(user=self.request.user, status=Order.Status.DELIVERED, items__product=product).exists()
        serializer.save(user=self.request.user, verified_purchase=eligible)


class PromotionViewSet(viewsets.ModelViewSet):
    queryset = Promotion.objects.all()
    serializer_class = PromotionSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]


class CouponViewSet(viewsets.ModelViewSet):
    queryset = Coupon.objects.all()
    serializer_class = CouponSerializer
    permission_classes = [IsAdminRole]


class DeliveryZoneViewSet(viewsets.ModelViewSet):
    queryset = DeliveryZone.objects.order_by("city")
    serializer_class = DeliveryZoneSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = CustomerNotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CustomerNotification.objects.filter(user=self.request.user)

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return Response(self.get_serializer(notification).data)


class SupportTicketViewSet(viewsets.ModelViewSet):
    serializer_class = SupportTicketSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = SupportTicket.objects.prefetch_related("messages")
        if self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}:
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReturnRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ReturnRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ReturnRequest.objects.all()
        if self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}:
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class HomepageBannerViewSet(viewsets.ModelViewSet):
    queryset = HomepageBanner.objects.filter(is_active=True).order_by("-created_at")
    serializer_class = HomepageBannerSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]


class AdminDashboardView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        return Response(dashboard_metrics())


class StaffViewSet(viewsets.ModelViewSet):
    serializer_class = DeveloperUserSerializer
    permission_classes = [CanManageUsers]
    search_fields = ["email", "username", "first_name", "last_name", "phone"]
    filterset_fields = ["role", "status", "is_staff"]
    ordering_fields = ["date_joined", "last_login", "email"]

    def get_queryset(self):
        return get_user_model().objects.annotate(
            order_count=Count("orders", distinct=True),
            total_spent=Sum("orders__total"),
        ).order_by("-date_joined")

    def perform_create(self, serializer):
        user = serializer.save()
        AuditLog.objects.create(actor=self.request.user, action="USER_CREATED", entity="User", entity_id=str(user.pk), after={"email": user.email, "role": user.role}, ip_address=client_ip(self.request))

    def perform_update(self, serializer):
        instance = self.get_object()
        before = {"email": instance.email, "role": instance.role, "status": instance.status, "is_staff": instance.is_staff}
        if instance.role == User.Role.SUPER_ADMIN and self.request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied("Seul un Developer peut modifier un Developer.")
        user = serializer.save()
        after = {"email": user.email, "role": user.role, "status": user.status, "is_staff": user.is_staff}
        AuditLog.objects.create(actor=self.request.user, action="USER_UPDATED", entity="User", entity_id=str(user.pk), before=before, after=after, ip_address=client_ip(self.request))

    def destroy(self, request, *args, **kwargs):
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response({"detail": "Vous ne pouvez pas supprimer votre propre compte."}, status=400)
        if user.role == User.Role.SUPER_ADMIN and get_user_model().objects.filter(role=User.Role.SUPER_ADMIN, status=User.Status.ACTIVE).count() <= 1:
            return Response({"detail": "Impossible de supprimer le dernier Developer actif."}, status=400)
        before = {"email": user.email, "role": user.role}
        user.delete()
        AuditLog.objects.create(actor=request.user, action="USER_DELETED", entity="User", entity_id=str(user.pk), before=before, ip_address=client_ip(request))
        return Response(status=204)


class DeveloperDashboardView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        delivered = Order.objects.filter(status=Order.Status.DELIVERED)
        orders_by_status = dict(Order.objects.values_list("status").annotate(total=Count("id")))
        sales_by_day = [
            {
                "day": (today - timedelta(days=offset)).isoformat(),
                "sales": delivered.filter(updated_at__date=today - timedelta(days=offset)).aggregate(total=Sum("total"))["total"] or 0,
            }
            for offset in range(6, -1, -1)
        ]
        top_products = Product.objects.order_by("-sales_count").values("id", "name", "sku", "sales_count")[:8]
        latest_orders = Order.objects.order_by("-created_at").values("id", "order_number", "status", "shipping_full_name", "shipping_city", "total", "created_at")[:8]
        data = {
            **dashboard_metrics(),
            "revenue_total": delivered.aggregate(total=Sum("total"))["total"] or 0,
            "revenue_today": delivered.filter(updated_at__date=today).aggregate(total=Sum("total"))["total"] or 0,
            "revenue_month": delivered.filter(updated_at__date__gte=month_start).aggregate(total=Sum("total"))["total"] or 0,
            "new_orders": Order.objects.filter(status=Order.Status.PENDING).count(),
            "cancelled_orders": Order.objects.filter(status=Order.Status.CANCELLED).count(),
            "products_total": Product.objects.count(),
            "products_active": Product.objects.filter(status=Product.Status.ACTIVE).count(),
            "customers_total": get_user_model().objects.filter(role=User.Role.CUSTOMER).count(),
            "customers_new": get_user_model().objects.filter(role=User.Role.CUSTOMER, date_joined__date__gte=month_start).count(),
            "orders_by_status": orders_by_status,
            "sales_by_day": sales_by_day,
            "top_products": list(top_products),
            "latest_orders": list(latest_orders),
            "unread_notifications": CustomerNotification.objects.filter(is_read=False).count(),
        }
        return Response(data)


class DeveloperSystemView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request):
        db_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_ok = False
        media_root = settings.MEDIA_ROOT
        return Response(
            {
                "backend_status": "ok",
                "api_status": "ok",
                "database_status": "ok" if db_ok else "error",
                "environment": "development" if settings.DEBUG or settings.SERVE_MEDIA_FILES else "production",
                "server_time": timezone.now(),
                "python_version": platform.python_version(),
                "django_version": django.get_version(),
                "platform": sys.platform,
                "media_root_exists": media_root.exists(),
                "media_root": str(media_root),
                "counts": {
                    "users": get_user_model().objects.count(),
                    "products": Product.objects.count(),
                    "orders": Order.objects.count(),
                    "audit_logs": AuditLog.objects.count(),
                },
                "last_activity": AuditLog.objects.order_by("-created_at").values("action", "entity", "created_at").first(),
            }
        )


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsDeveloper]
    search_fields = ["action", "entity", "entity_id", "actor__email"]
    filterset_fields = ["action", "entity"]
    ordering_fields = ["created_at", "action", "entity"]

    def get_queryset(self):
        return AuditLog.objects.select_related("actor").order_by("-created_at")


class DeveloperInventoryView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request):
        rows = []
        for variant in ProductVariant.objects.select_related("product", "inventory").order_by("product__name")[:200]:
            inventory = getattr(variant, "inventory", None)
            quantity = inventory.quantity if inventory else 0
            rows.append(
                {
                    "variant_id": variant.id,
                    "product": variant.product.name,
                    "sku": variant.sku,
                    "status": variant.product.status,
                    "quantity": quantity,
                    "reserved_quantity": inventory.reserved_quantity if inventory else 0,
                    "low_stock_threshold": variant.product.low_stock_threshold,
                    "is_low_stock": quantity <= variant.product.low_stock_threshold,
                }
            )
        return Response({"count": len(rows), "results": rows})


class DeveloperExportView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request, kind):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="dolphin-{kind}.csv"'
        writer = csv.writer(response)
        if kind == "products":
            writer.writerow(["sku", "name", "status", "regular_price", "current_price", "category", "brand"])
            for product in Product.objects.select_related("category", "brand"):
                writer.writerow([product.sku, product.name, product.status, product.regular_price, product.current_price, product.category.name, product.brand.name if product.brand else ""])
        elif kind == "orders":
            writer.writerow(["order_number", "status", "customer", "city", "total", "created_at"])
            for order in Order.objects.all():
                writer.writerow([order.order_number, order.status, order.shipping_full_name, order.shipping_city, order.total, order.created_at])
        elif kind == "customers":
            writer.writerow(["email", "first_name", "last_name", "role", "status", "date_joined"])
            for user in get_user_model().objects.all():
                writer.writerow([user.email, user.first_name, user.last_name, user.role, user.status, user.date_joined])
        else:
            return Response({"detail": "Export inconnu."}, status=404)
        AuditLog.objects.create(actor=request.user, action="CSV_EXPORTED", entity=kind, ip_address=client_ip(request))
        return response


class ProductImportViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ProductImportJob.objects.prefetch_related("rows").order_by("-created_at")
    serializer_class = ProductImportJobSerializer
    permission_classes = [IsAdminRole]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @action(detail=False, methods=["get"])
    def template(self, request):
        stream = build_template_workbook()
        return FileResponse(stream, as_attachment=True, filename="dolphin_product_import_template.xlsx")

    @action(detail=False, methods=["post"])
    def preview(self, request):
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"file": "Fichier requis."}, status=400)
        job = preview_import(uploaded, request.user)
        return Response(self.get_serializer(job).data, status=201)

    @action(detail=True, methods=["post"])
    def commit(self, request, pk=None):
        job = commit_import(
            self.get_object(),
            update_existing=request_bool(request.data.get("update_existing")),
            skip_duplicates=request_bool(request.data.get("skip_duplicates"), True),
            create_missing_relations=request_bool(request.data.get("create_missing_relations"), True),
            actor=request.user,
        )
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["get"])
    def errors(self, request, pk=None):
        job = self.get_object()
        response = HttpResponse(job.error_report or "row_number,sku,errors\n", content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="import-errors-{job.id}.csv"'
        return response
