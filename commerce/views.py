import csv
import json
import platform
import sys
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, time, timedelta
from io import BytesIO

import django
import requests
from openpyxl import Workbook
from PIL import Image
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.db.models import F
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, HttpResponse
from django.utils.dateparse import parse_date
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
    Expense,
    HomepageBanner,
    HomeSection,
    Inventory,
    NewsletterSubscriber,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    ProductImage,
    ProductVariant,
    ProductImportJob,
    Promotion,
    Refund,
    ReturnHistory,
    ReturnRequest,
    SiteSettings,
    StockMovement,
    Supplier,
    SupportTicket,
    User,
    Wishlist,
    WishlistItem,
)
from .importers.excel_importer import build_template_workbook
from .importers.services import commit_import, preview_import
from .permissions import CanManageUsers, IsAdminRole, IsCatalogManagerOrReadOnly, IsDeveloper, IsOrderManager, IsOrderManagerOrCustomer
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
    ExpenseSerializer,
    RefundSerializer,
    HomepageBannerSerializer,
    HomeSectionSerializer,
    NewsletterSubscriberSerializer,
    OrderSerializer,
    ProductSerializer,
    ProductImportJobSerializer,
    PromotionSerializer,
    RegisterSerializer,
    ReturnRequestSerializer,
    SupplierSerializer,
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


HOME_DESIGN_DEFAULTS = {
    "store_name": "DOLPHIN",
    "announcement_text": "Livraison gratuite partout au Maroc",
    "announcement_bg_color": "#FF6B4A",
    "announcement_text_color": "#FFFFFF",
    "hero_eyebrow": "Marketplace multi-categories au Maroc",
    "hero_title": "DOLPHIN",
    "hero_subtitle": "Tout ce qu'il vous faut, au meme endroit. Produits selectionnes, promotions claires, livraison gratuite.",
    "primary_cta_label": "Decouvrir les produits",
    "primary_cta_url": "/catalogue",
    "secondary_cta_label": "Voir les offres",
    "secondary_cta_url": "/catalogue?promotion=true",
    "trust_1": "Variantes disponibles",
    "trust_2": "Paiement livraison",
    "trust_3": "Retours suivis",
    "service_1_title": "Livraison gratuite",
    "service_1_text": "Livraison gratuite partout au Maroc avec suivi de commande.",
    "service_2_title": "Paiement securise",
    "service_2_text": "Paiement a la livraison sur les zones actives.",
    "service_3_title": "Support verifie",
    "service_3_text": "Service client disponible pour commandes, livraison et retours.",
    "newsletter_title": "Newsletter",
    "newsletter_subtitle": "Recevez les nouveautes et promotions publiees par Dolphin.",
    "primary_color": "#0077B6",
    "accent_color": "#FF6B4A",
}


def home_design_settings():
    settings_row, _ = SiteSettings.objects.get_or_create(key="home_design", defaults={"value": HOME_DESIGN_DEFAULTS})
    return settings_row, {**HOME_DESIGN_DEFAULTS, **(settings_row.value or {})}


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def simple_pdf_response(filename, lines):
    escaped_lines = [str(line).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    text_commands = ["BT", "/F1 12 Tf", "50 790 Td"]
    for index, line in enumerate(escaped_lines):
        if index:
            text_commands.append("0 -18 Td")
        text_commands.append(f"({line}) Tj")
    text_commands.append("ET")
    stream = "\n".join(text_commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    body = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode()
    body += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    response = HttpResponse(body, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def pdf_escape(value):
    return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def money_dh(value):
    amount = Decimal(value or "0.00")
    if amount == amount.to_integral():
        return f"{amount:.0f} Dh"
    return f"{amount:.2f} Dh"


def ozon_amount(value):
    amount = Decimal(value or "0.00")
    return str(int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def invoice_number(order):
    digits = "".join(char for char in str(order.pk) if char.isdigit())[-3:] or "001"
    return f"026.DLP.{digits.zfill(3)}.FAC.001"


def truncated_text(value, limit=42):
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[: max(limit - 3, 0)]}..."


def dolphin_invoice_pdf_response(order):
    width = 595
    commands = []

    def line_width(size):
        commands.append(f"{size} w")

    def stroke_color(gray):
        commands.append(f"{gray} G")

    def fill_color(gray):
        commands.append(f"{gray} g")

    def fill_rgb(r, g, b):
        commands.append(f"{r} {g} {b} rg")

    def rect(x, y, w, h, fill=False):
        commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {'f' if fill else 'S'}")

    def line(x1, y1, x2, y2):
        commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def text(x, y, value, size=8, font="F1", align="left"):
        safe = pdf_escape(value)
        approx_width = len(str(value)) * size * 0.48
        if align == "center":
            x -= approx_width / 2
        elif align == "right":
            x -= approx_width
        commands.append(f"BT /{font} {size} Tf {x:.2f} {y:.2f} Td ({safe}) Tj ET")

    line_width(0.7)
    stroke_color(0)

    logo_path = settings.BASE_DIR.parent / "frontend" / "src" / "assets" / "dolphin-logo.jpeg"
    logo_data = None
    logo_size = None
    if logo_path.exists():
        logo_data = logo_path.read_bytes()
        with Image.open(logo_path) as logo:
            logo_size = logo.size
        commands.append("q 170 0 0 50 24 752 cm /Logo Do Q")
    else:
        text(30, 770, "DOLPHIN", 26, "F2")
        commands.append("84 781 m 93 790 l 101 780 l 93 772 l h f")
    text(410, 770, "FACTURE", 30, "F2", "center")

    info_x, info_y, info_w, row_h = 221, 650, 339, 18
    fill_rgb(0.73, 0.80, 0.91)
    rect(info_x, info_y + row_h * 4, info_w, row_h, True)
    fill_color(0)
    rect(info_x, info_y, info_w, row_h * 5)
    for index in range(1, 5):
        line(info_x, info_y + row_h * index, info_x + info_w, info_y + row_h * index)
    line(info_x + 118, info_y, info_x + 118, info_y + row_h * 4)
    text(info_x + info_w / 2, info_y + row_h * 4 + 6, f"Facture N : {invoice_number(order)}", 7, "F2", "center")
    info_rows = [
        ("Nom de client", order.shipping_full_name),
        ("Telephone", order.shipping_phone),
        ("Adresse postale", f"{order.shipping_address}, {order.shipping_city}".strip(", ")),
        ("Date de commande", timezone.localtime(order.created_at).strftime("%d / %m / %Y")),
    ]
    for index, (label, value) in enumerate(info_rows):
        y = info_y + row_h * (3 - index) + 6
        text(info_x + 7, y, label, 7, "F2")
        text(info_x + 126, y, truncated_text(value, 50), 6.5)

    meta_x, meta_y, meta_w, meta_h = 9, 613, 551, 32
    rect(meta_x, meta_y, meta_w, meta_h)
    col_widths = [112, 110, 112, 132, 85]
    fill_rgb(0.73, 0.80, 0.91)
    rect(meta_x, meta_y + 16, meta_w, 16, True)
    fill_color(0)
    rect(meta_x, meta_y, meta_w, meta_h)
    cursor = meta_x
    for col_w in col_widths[:-1]:
        cursor += col_w
        line(cursor, meta_y, cursor, meta_y + meta_h)
    line(meta_x, meta_y + 16, meta_x + meta_w, meta_y + 16)
    meta_headers = ["N devis", "Mode Reglement", "Ref Reglement", "Mode Livraison", "Page N"]
    meta_values = [order.order_number, "Cash", invoice_number(order), "Livraison Standard", "1/1"]
    centers = []
    cursor = meta_x
    for col_w in col_widths:
        centers.append(cursor + col_w / 2)
        cursor += col_w
    for index, header in enumerate(meta_headers):
        text(centers[index], meta_y + 21.5, header, 7, "F2", "center")
        text(centers[index], meta_y + 5.5, meta_values[index], 6.3, align="center")

    table_x, table_y, table_w, table_h = 9, 206, 551, 390
    header_h = 17
    fill_rgb(0.73, 0.80, 0.91)
    rect(table_x, table_y + table_h - header_h, table_w, header_h, True)
    fill_color(0)
    rect(table_x, table_y, table_w, table_h)
    line(table_x, table_y + table_h - header_h, table_x + table_w, table_y + table_h - header_h)
    table_cols = [112, 203, 86, 72, 78]
    cursor = table_x
    for col_w in table_cols[:-1]:
        cursor += col_w
        line(cursor, table_y, cursor, table_y + table_h)
    headers = ["Article", "Designation", "Quantite", "Prix", "Total HT"]
    cursor = table_x
    for index, col_w in enumerate(table_cols):
        text(cursor + col_w / 2, table_y + table_h - 11, headers[index], 7, "F2", "center")
        cursor += col_w
    row_y = table_y + table_h - header_h - 29
    for item in order.items.all():
        text(table_x + 8, row_y, truncated_text(item.product_name, 17), 8)
        designation = item.variant_label or item.sku or item.product_name
        text(table_x + table_cols[0] + 8, row_y, truncated_text(designation, 39), 8)
        text(table_x + sum(table_cols[:2]) + table_cols[2] / 2, row_y, str(item.quantity).zfill(2), 8, align="center")
        text(table_x + sum(table_cols[:3]) + table_cols[3] / 2, row_y, money_dh(item.unit_price).replace(" Dh", ""), 8, align="center")
        text(table_x + sum(table_cols[:4]) + table_cols[4] / 2, row_y, money_dh(item.total).replace(" Dh", ""), 8, align="center")
        row_y -= 17
        if row_y < table_y + 12:
            break

    totals_x, totals_y, totals_w, totals_h = 309, 124, 251, 54
    rect(totals_x, totals_y, totals_w, totals_h)
    line(totals_x, totals_y + 18, totals_x + totals_w, totals_y + 18)
    line(totals_x, totals_y + 36, totals_x + totals_w, totals_y + 36)
    line(totals_x + 96, totals_y, totals_x + 96, totals_y + totals_h)
    totals = [("TVA", "0%"), ("TTC", money_dh(order.total)), ("TOTAL", money_dh(order.total))]
    for index, (label, value) in enumerate(totals):
        y = totals_y + totals_h - 13 - index * 18
        text(totals_x + 48, y, label, 8, "F2", "center")
        text(totals_x + 171, y, value, 8, "F2", "center")

    text(width / 2, 58, "DOLPHIN.ma", 9, "F2", "center")
    text(width / 2, 43, "Tel : 06-63-33-61-88 / R.S : DOLPHIN.OFFICIEL / CASABLANCA - MAROC", 7.5, "F2", "center")

    stream = "\n".join(commands).encode("latin-1", errors="replace")
    content_object_number = 7 if logo_data and logo_size else 6
    xobject_resource = " /XObject << /Logo 6 0 R >>" if logo_data and logo_size else ""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >>{xobject_resource} >> /Contents {content_object_number} 0 R >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    if logo_data and logo_size:
        logo_width, logo_height = logo_size
        objects.append(
            f"<< /Type /XObject /Subtype /Image /Width {logo_width} /Height {logo_height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(logo_data)} >>\nstream\n".encode()
            + logo_data
            + b"\nendstream"
        )
    objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    body = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_start = len(body)
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode()
    body += f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    response = HttpResponse(body, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="facture-{order.order_number}.pdf"'
    return response


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
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        AuditLog.objects.create(actor=user, action="CUSTOMER_REGISTERED", entity="User", entity_id=str(user.pk), after={"email": user.email}, ip_address=client_ip(request))
        refresh = RefreshToken.for_user(user)
        return Response({"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data}, status=status.HTTP_201_CREATED)


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

    def get_queryset(self):
        qs = super().get_queryset()
        if not (self.request.user.is_authenticated and self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            qs = qs.filter(is_active=True, is_archived=False)
        return qs

    def _ensure_catalog_manager(self, request):
        if not (request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            raise PermissionDenied("Role non autorise.")

    def _set_active(self, request, category, active):
        before = {"is_active": category.is_active}
        category.is_active = active
        category.save(update_fields=["is_active", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="CATEGORY_ACTIVATED" if active else "CATEGORY_DEACTIVATED",
            entity="Category",
            entity_id=str(category.pk),
            before=before,
            after={"is_active": category.is_active},
            ip_address=client_ip(request),
        )
        return Response(self.get_serializer(category).data)

    @action(detail=True, methods=["post"])
    def deactivate(self, request, slug=None):
        self._ensure_catalog_manager(request)
        return self._set_active(request, self.get_object(), False)

    @action(detail=True, methods=["post"])
    def activate(self, request, slug=None):
        self._ensure_catalog_manager(request)
        category = self.get_object()
        if category.is_archived:
            category.is_archived = False
            category.save(update_fields=["is_archived", "updated_at"])
        return self._set_active(request, category, True)

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
    queryset = Brand.objects.all().order_by("name", "id")
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
        .prefetch_related("images", "variants__values", "variants__inventory")
    )
    serializer_class = ProductSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]
    lookup_field = "slug"
    filterset_class = ProductFilter
    search_fields = ["name", "sku", "short_description", "description", "brand__name", "category__name"]
    ordering_fields = ["created_at", "regular_price", "view_count", "sales_count"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_serializer_class(self):
        if self.request.method in {"POST", "PUT", "PATCH"}:
            return AdminProductWriteSerializer
        return ProductSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if not (self.request.user.is_authenticated and self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            qs = qs.filter(status=Product.Status.ACTIVE, category__is_active=True, category__is_archived=False)
        if self.request.query_params.get("promotion") == "true":
            qs = qs.filter(promotional_price__isnull=False)
        return qs.order_by("-created_at")

    def _filtered_products_from_payload(self, request):
        ids = request.data.get("ids")
        qs = Product.objects.all()
        if request.data.get("all_results") is True:
            search = request.data.get("search", "")
            status_filter = request.data.get("status", "")
            category_id = request.data.get("category_id")
            brand_id = request.data.get("brand_id")
            if search:
                qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search) | Q(short_description__icontains=search) | Q(description__icontains=search) | Q(brand__name__icontains=search) | Q(category__name__icontains=search))
            if status_filter:
                qs = qs.filter(status=status_filter)
            if category_id:
                qs = qs.filter(category_id=category_id)
            if brand_id:
                qs = qs.filter(brand_id=brand_id)
            return qs
        return qs.filter(id__in=ids or [])

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        if not (request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            Product.objects.filter(pk=instance.pk).update(view_count=instance.view_count + 1)
        return Response(ProductSerializer(instance, context=self.get_serializer_context()).data)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data, status=201)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        product = self.get_object()
        serializer = self.get_serializer(product, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def archive(self, request, slug=None):
        product = self.get_object()
        product.status = Product.Status.ARCHIVED
        product.save(update_fields=["status"])
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def restore(self, request, slug=None):
        product = self.get_object()
        product.status = Product.Status.ACTIVE
        product.save(update_fields=["status"])
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

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
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data, status=201)

    @action(detail=False, methods=["post"], permission_classes=[IsAdminRole])
    def bulk(self, request):
        action_name = request.data.get("action")
        qs = self._filtered_products_from_payload(request)
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

    def _remove_or_archive_products(self, qs):
        archived = 0
        deleted = 0
        for product in qs:
            try:
                CartItem.objects.filter(variant__product=product).delete()
                StockMovement.objects.filter(variant__product=product).delete()
                product.delete()
                deleted += 1
            except ProtectedError:
                product.status = Product.Status.ARCHIVED
                product.variants.update(is_active=False)
                product.save(update_fields=["status"])
                archived += 1
        return archived, deleted

    @action(detail=False, methods=["post"], permission_classes=[IsDeveloper])
    def bulk_delete(self, request):
        confirmation = request.data.get("confirmation")
        if confirmation != "SUPPRIMER TOUS LES PRODUITS":
            return Response({"confirmation": "Confirmation incorrecte."}, status=400)

        ids = request.data.get("ids")
        all_results = request.data.get("all_results") is True
        qs = self._filtered_products_from_payload(request)
        products = list(qs.select_related("category", "brand").prefetch_related("variants"))
        if not products:
            return Response({"detail": "Aucun produit concerne.", "count": 0, "archived": 0, "deleted": 0})

        with transaction.atomic():
            archived, deleted = self._remove_or_archive_products(products)
            AuditLog.objects.create(
                actor=request.user,
                action="PRODUCT_BULK_DELETE",
                entity="Product",
                after={"count": len(products), "archived": archived, "deleted": deleted, "all_results": all_results, "ids": ids or []},
                ip_address=client_ip(request),
            )
        return Response({"detail": "Produits traites.", "count": len(products), "archived": archived, "deleted": deleted})

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
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def set_main_image(self, request, slug=None):
        product = self.get_object()
        image = product.images.get(pk=request.data.get("image_id"))
        product.images.update(is_main=False)
        image.is_main = True
        image.save(update_fields=["is_main"])
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminRole])
    def reorder_images(self, request, slug=None):
        product = self.get_object()
        for item in request.data.get("items", []):
            product.images.filter(pk=item.get("id")).update(display_order=item.get("display_order", 0))
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["delete"], permission_classes=[IsAdminRole])
    def delete_image(self, request, slug=None):
        self.get_object().images.filter(pk=request.data.get("image_id")).delete()
        return Response(status=204)

    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        self._remove_or_archive_products([product])
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
        if not serializer.is_valid() and request.data.get("variant_id") and request.data.get("product_id"):
            fallback_data = request.data.copy()
            fallback_data.pop("variant_id", None)
            serializer = CartItemSerializer(data=fallback_data)
        serializer.is_valid(raise_exception=True)
        item = add_cart_item(cart, variant=serializer.validated_data.get("variant"), product=serializer.validated_data.get("product"), quantity=serializer.validated_data["quantity"])
        return Response(CartItemSerializer(item).data, status=201)

    @action(detail=False, methods=["patch"])
    def update_item(self, request):
        cart = get_or_create_cart(request)
        item = cart.items.select_related("variant__product").get(pk=request.data["item_id"])
        quantity = max(int(request.data.get("quantity", 1)), 1)
        item.quantity = quantity
        item.save(update_fields=["quantity"])
        data = CartSerializer(cart).data
        data.update(cart_totals(cart))
        return Response(data)

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


def ozon_settings():
    settings_row, _ = SiteSettings.objects.get_or_create(key="ozon", defaults={"value": {}})
    return settings_row


def ozon_settings_payload(value):
    return {
        "customer_id": value.get("customer_id", ""),
        "api_key": value.get("api_key", ""),
        "has_api_key": bool(value.get("api_key")),
    }


def ozon_order_products(order):
    return [
        {"ref": item.sku or f"ORDER-{order.id}-{item.id}", "qnty": item.quantity}
        for item in order.items.all()
    ]


def normalize_ozon_cities(payload):
    if isinstance(payload, dict):
        rows = payload.get("CITIES") or payload.get("cities") or payload.get("data") or payload.get("results") or payload.get("CITY") or payload.get("VILLES")
        if rows is None:
            rows = [{"id": key, "name": value} for key, value in payload.items()]
    else:
        rows = payload
    if isinstance(rows, dict):
        rows = [
            value if isinstance(value, dict) else {"id": key, "name": value}
            for key, value in rows.items()
        ]
    cities = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, str):
            cities.append({"id": row, "name": row})
            continue
        if not isinstance(row, dict):
            continue
        city_id = row.get("id") or row.get("ID") or row.get("city_id") or row.get("CITY_ID") or row.get("ref")
        name = row.get("name") or row.get("NAME") or row.get("city") or row.get("CITY") or row.get("ville") or row.get("VILLE")
        if city_id and name:
            cities.append({"id": str(city_id), "name": str(name)})
    return sorted(cities, key=lambda city: city["name"].lower())


def find_ozon_tracking_number(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key).replace("-", "").replace("_", "").upper()
            if normalized_key in {"TRACKINGNUMBER", "TRACKING"} and value:
                return str(value)
            found = find_ozon_tracking_number(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = find_ozon_tracking_number(item)
            if found:
                return found
    return ""


def normalize_ozon_key(value):
    return str(value).replace("-", "").replace("_", "").replace(" ", "").upper()


def ozon_status_from_payload(payload):
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = normalize_ozon_key(key)
            if normalized_key in {"STATUS", "STATUT", "PARCELSTATUS", "DELIVERYSTATUS", "LASTSTATUS", "MESSAGE"} and value:
                text = str(value)
                if text.upper() not in {"SUCCESS", "VALID CUSTOMER"}:
                    return text
        for value in payload.values():
            found = ozon_status_from_payload(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = ozon_status_from_payload(item)
            if found:
                return found
    return ""


def collect_ozon_tracking_rows(payload):
    rows = {}
    if isinstance(payload, dict):
        tracking = find_ozon_tracking_number(payload)
        if tracking:
            rows[tracking] = payload
        for key, value in payload.items():
            if isinstance(value, dict) and key:
                nested_tracking = find_ozon_tracking_number(value) or str(key)
                if nested_tracking and isinstance(value, dict):
                    rows[nested_tracking] = value
            rows.update(collect_ozon_tracking_rows(value))
    elif isinstance(payload, list):
        for item in payload:
            rows.update(collect_ozon_tracking_rows(item))
    return rows


def order_status_from_ozon(ozon_status):
    status = normalize_ozon_key(ozon_status)
    if not status:
        return ""
    if any(word in status for word in ["DELIVERED", "LIVRE", "LIVREE"]):
        return Order.Status.DELIVERED
    if any(word in status for word in ["RETURN", "RETOUR"]):
        return Order.Status.RETURNED
    if any(word in status for word in ["REFUSE", "REFUSED", "CANCEL", "ANNULE"]):
        return Order.Status.CANCELLED
    if any(word in status for word in ["DISTRIBUTION", "DELIVERY", "LIVRAISON", "COURSIER"]):
        return Order.Status.OUT_FOR_DELIVERY
    if any(word in status for word in ["PICKED", "PICKUP", "RAMASSE", "RECEIVED", "RECU", "EXPEDIE", "SHIPPED", "TRANSIT"]):
        return Order.Status.SHIPPED
    return ""


class OzonSettingsView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        settings_row = ozon_settings()
        return Response(ozon_settings_payload(settings_row.value or {}))

    def patch(self, request):
        customer_id = str(request.data.get("customer_id", "")).strip()
        api_key = str(request.data.get("api_key", "")).strip()
        if not customer_id or not api_key:
            return Response({"detail": "Ozon customer ID et API key sont obligatoires."}, status=400)
        settings_row = ozon_settings()
        before = settings_row.value or {}
        settings_row.value = {"customer_id": customer_id, "api_key": api_key}
        settings_row.save(update_fields=["value", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="OZON_SETTINGS_UPDATED",
            entity="SiteSettings",
            entity_id=str(settings_row.pk),
            before={"customer_id": before.get("customer_id", ""), "has_api_key": bool(before.get("api_key"))},
            after={"customer_id": customer_id, "has_api_key": True},
            ip_address=client_ip(request),
        )
        return Response(ozon_settings_payload(settings_row.value))


class OzonCitiesView(APIView):
    permission_classes = [IsOrderManager]

    def get(self, request):
        try:
            response = requests.get("https://api.ozonexpress.ma/cities", timeout=20)
            response.raise_for_status()
            cities = normalize_ozon_cities(response.json())
        except requests.RequestException as exc:
            return Response({"detail": f"Ozon cities indisponible: {exc}"}, status=502)
        except ValueError:
            return Response({"detail": "Reponse villes Ozon invalide."}, status=502)
        return Response(cities)


class OzonEligibleOrdersView(APIView):
    permission_classes = [IsOrderManager]

    def get(self, request):
        qs = (
            Order.objects.select_related("user", "delivery_zone")
            .prefetch_related("items")
            .filter(tracking_number="")
            .filter(status=Order.Status.CONFIRMED)
            .order_by("-created_at")[:100]
        )
        return Response(OrderSerializer(qs, many=True).data)


class OzonParcelView(APIView):
    permission_classes = [IsOrderManager]

    def post(self, request):
        order_id = request.data.get("order_id")
        city_id = str(request.data.get("city_id", "")).strip()
        city_name = str(request.data.get("city_name", "")).strip()
        if not city_id:
            return Response({"city_id": "ID ville Ozon obligatoire."}, status=400)
        order = Order.objects.prefetch_related("items").get(pk=order_id)
        if order.tracking_number:
            return Response({"detail": "Cette commande a deja un tracking number."}, status=400)
        settings_value = ozon_settings().value or {}
        customer_id = settings_value.get("customer_id")
        api_key = settings_value.get("api_key")
        if not customer_id or not api_key:
            return Response({"detail": "Configurez Ozon customer ID et API key avant l'envoi."}, status=400)

        parcel_nature = ", ".join(item.product_name for item in order.items.all())[:180] or f"Commande {order.order_number}"
        payload = {
            "parcel-receiver": order.shipping_full_name,
            "parcel-phone": order.shipping_phone,
            "parcel-city": city_id,
            "parcel-address": order.shipping_address,
            "parcel-note": order.customer_note or "",
            "parcel-price": ozon_amount(order.total),
            "parcel-nature": parcel_nature,
            "parcel-stock": str(request.data.get("parcel_stock", "1")),
            "parcel-open": str(request.data.get("parcel_open", "1")),
            "parcel-fragile": str(request.data.get("parcel_fragile", "0")),
            "parcel-replace": str(request.data.get("parcel_replace", "0")),
            "products": json.dumps(ozon_order_products(order)),
        }
        tracking_number_override = str(request.data.get("tracking_number", "")).strip()
        if tracking_number_override:
            payload["tracking-number"] = tracking_number_override
        url = f"https://api.ozonexpress.ma/customers/{customer_id}/{api_key}/add-parcel"
        try:
            multipart_payload = {key: (None, value) for key, value in payload.items()}
            response = requests.post(url, files=multipart_payload, timeout=20)
            if response.status_code >= 400:
                return Response({"detail": "Ozon a refuse le colis.", "status_code": response.status_code, "ozon_response": response.text[:1000]}, status=502)
            data = response.json()
        except requests.RequestException as exc:
            return Response({"detail": f"Ozon API indisponible: {exc}"}, status=502)
        except ValueError:
            return Response({"detail": "Reponse Ozon invalide."}, status=502)

        tracking_number = find_ozon_tracking_number(data)
        if not tracking_number:
            return Response({"detail": f"Ozon n'a pas retourne de tracking number. Reponse: {json.dumps(data, ensure_ascii=False)[:800]}", "ozon_response": data}, status=502)
        previous_status = order.status
        order.tracking_number = tracking_number
        if city_name:
            order.shipping_city = city_name
        if order.status in {Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.PREPARING}:
            order.status = Order.Status.SHIPPED
        order.save(update_fields=["tracking_number", "shipping_city", "status", "updated_at"])
        if previous_status != order.status:
            OrderStatusHistory.objects.create(order=order, from_status=previous_status, to_status=order.status, actor=request.user, note=f"Colis ajoute a Ozon: {tracking_number}")
        AuditLog.objects.create(
            actor=request.user,
            action="OZON_PARCEL_CREATED",
            entity="Order",
            entity_id=str(order.pk),
            after={"tracking_number": tracking_number, "city_id": city_id, "city_name": city_name},
            ip_address=client_ip(request),
        )
        return Response({"order": OrderSerializer(order).data, "ozon": data})


class OzonTrackingView(APIView):
    permission_classes = [IsOrderManager]

    def get(self, request):
        qs = (
            Order.objects.select_related("user", "delivery_zone")
            .prefetch_related("items", "status_history")
            .exclude(tracking_number="")
            .order_by("-updated_at", "-created_at")[:200]
        )
        rows = OrderSerializer(qs, many=True).data
        all_tracking = Order.objects.exclude(tracking_number="")
        dashboard = {
            "total": all_tracking.count(),
            "shipped": all_tracking.filter(status=Order.Status.SHIPPED).count(),
            "out_for_delivery": all_tracking.filter(status=Order.Status.OUT_FOR_DELIVERY).count(),
            "delivered": all_tracking.filter(status=Order.Status.DELIVERED).count(),
            "returned": all_tracking.filter(status=Order.Status.RETURNED).count(),
            "cancelled": all_tracking.filter(status=Order.Status.CANCELLED).count(),
            "open": all_tracking.exclude(status__in=[Order.Status.DELIVERED, Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.REFUNDED]).count(),
        }
        return Response({"dashboard": dashboard, "results": rows})


class OzonTrackingSyncView(APIView):
    permission_classes = [IsOrderManager]

    def post(self, request):
        order_ids = request.data.get("order_ids") or []
        qs = Order.objects.exclude(tracking_number="")
        if order_ids:
            qs = qs.filter(id__in=order_ids)
        else:
            qs = qs.exclude(status__in=[Order.Status.DELIVERED, Order.Status.CANCELLED, Order.Status.RETURNED, Order.Status.REFUNDED])
        orders = list(qs.order_by("-updated_at")[:200])
        tracking_numbers = [order.tracking_number for order in orders if order.tracking_number]
        if not tracking_numbers:
            return Response({"detail": "Aucune commande avec tracking a synchroniser.", "updated": 0, "results": []})

        settings_value = ozon_settings().value or {}
        customer_id = settings_value.get("customer_id")
        api_key = settings_value.get("api_key")
        if not customer_id or not api_key:
            return Response({"detail": "Configurez Ozon customer ID et API key avant le tracking."}, status=400)

        url = f"https://api.ozonexpress.ma/customers/{customer_id}/{api_key}/tracking"
        try:
            response = requests.post(url, json={"tracking-number": tracking_numbers}, timeout=30)
            if response.status_code >= 400:
                return Response({"detail": "Ozon a refuse le tracking.", "status_code": response.status_code, "ozon_response": response.text[:1000]}, status=502)
            data = response.json()
        except requests.RequestException as exc:
            return Response({"detail": f"Ozon tracking indisponible: {exc}"}, status=502)
        except ValueError:
            return Response({"detail": "Reponse tracking Ozon invalide."}, status=502)

        rows_by_tracking = collect_ozon_tracking_rows(data)
        updated = 0
        results = []
        for order in orders:
            row = rows_by_tracking.get(order.tracking_number) or rows_by_tracking.get(order.tracking_number.upper()) or {}
            ozon_status = ozon_status_from_payload(row)
            next_status = order_status_from_ozon(ozon_status)
            before = order.status
            if next_status and next_status != order.status:
                order.status = next_status
                order.save(update_fields=["status", "updated_at"])
                OrderStatusHistory.objects.create(order=order, from_status=before, to_status=next_status, actor=request.user, note=f"Ozon tracking: {ozon_status}")
                updated += 1
            results.append({"order_id": order.id, "order_number": order.order_number, "tracking_number": order.tracking_number, "ozon_status": ozon_status, "old_status": before, "new_status": order.status})
        AuditLog.objects.create(actor=request.user, action="OZON_TRACKING_SYNCED", entity="Order", after={"count": len(orders), "updated": updated}, ip_address=client_ip(request))
        return Response({"updated": updated, "results": results, "ozon": data})


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [IsOrderManagerOrCustomer]
    search_fields = ["order_number", "user__email", "guest_email", "shipping_phone", "tracking_number"]
    filterset_fields = ["status", "payment_method", "shipping_city"]
    ordering_fields = ["created_at", "total"]

    def get_queryset(self):
        qs = Order.objects.select_related("user", "delivery_zone").prefetch_related("items", "status_history", "refunds").order_by("-created_at")
        if getattr(self.request.user, "role", None) == User.Role.CUSTOMER:
            qs = qs.filter(user=self.request.user)
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        return qs

    def update(self, request, *args, **kwargs):
        if request.user.role == User.Role.CUSTOMER:
            return Response({"detail": "Les clients ne peuvent pas modifier une commande."}, status=403)
        if "status" in request.data:
            return Response({"detail": "Utilisez l'action transition pour changer le statut d'une commande."}, status=400)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        if request.user.role == User.Role.CUSTOMER:
            return Response({"detail": "Les clients ne peuvent pas modifier une commande."}, status=403)
        if "status" in request.data:
            return Response({"detail": "Utilisez l'action transition pour changer le statut d'une commande."}, status=400)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return Response({"detail": "La suppression de commande est interdite; utilisez une transition de statut."}, status=405)

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
        order = transition_order(self.get_object(), new_status, request.user, note, force=True)
        if new_status == Order.Status.CANCELLED and note:
            order.internal_note = f"{order.internal_note}\n{note}".strip()
            order.save(update_fields=["internal_note", "updated_at"])
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def invoice(self, request, pk=None):
        order = self.get_object()
        is_order_manager = request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.ORDER_OPERATOR}
        is_owner = request.user.is_authenticated and order.user_id == request.user.id
        invoice_key = request.query_params.get("key", "")
        if not (is_order_manager or is_owner or (order.idempotency_key and invoice_key == order.idempotency_key)):
            return Response({"detail": "Cle facture invalide."}, status=403)
        AuditLog.objects.create(actor=request.user if request.user.is_authenticated else None, action="INVOICE_DOWNLOADED", entity="Order", entity_id=str(order.pk), ip_address=client_ip(request))
        return dolphin_invoice_pdf_response(order)

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
        return wishlist.items.select_related("product").order_by("-created_at")

    def perform_create(self, serializer):
        wishlist, _ = Wishlist.objects.get_or_create(user=self.request.user)
        serializer.save(wishlist=wishlist)


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
        return CustomerNotification.objects.filter(user=self.request.user).order_by("-created_at", "-id")

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
        qs = SupportTicket.objects.prefetch_related("messages").order_by("-created_at")
        if self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}:
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ReturnRequestViewSet(viewsets.ModelViewSet):
    serializer_class = ReturnRequestSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ReturnRequest.objects.order_by("-created_at")
        if self.request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}:
            return qs
        return qs.filter(user=self.request.user)

    def perform_create(self, serializer):
        request_return = serializer.save(user=self.request.user)
        ReturnHistory.objects.create(return_request=request_return, to_status=request_return.status, actor=self.request.user, note="Demande de retour creee")
        AuditLog.objects.create(actor=self.request.user, action="RETURN_CREATED", entity="ReturnRequest", entity_id=str(request_return.pk), after={"order": request_return.order_id}, ip_address=client_ip(self.request))

    def _admin_decide(self, request, status_value):
        return_request = self.get_object()
        previous = return_request.status
        return_request.status = status_value
        return_request.admin_decision = request.data.get("decision", "")
        return_request.decided_by = request.user
        return_request.decided_at = timezone.now()
        return_request.save(update_fields=["status", "admin_decision", "decided_by", "decided_at", "updated_at"])
        ReturnHistory.objects.create(return_request=return_request, from_status=previous, to_status=status_value, actor=request.user, note=return_request.admin_decision)
        AuditLog.objects.create(actor=request.user, action="RETURN_STATUS_CHANGED", entity="ReturnRequest", entity_id=str(return_request.pk), before={"status": previous}, after={"status": status_value}, ip_address=client_ip(request))
        return Response(self.get_serializer(return_request).data)

    @action(detail=True, methods=["post"], permission_classes=[IsOrderManager])
    def approve(self, request, pk=None):
        return self._admin_decide(request, ReturnRequest.Status.APPROVED)

    @action(detail=True, methods=["post"], permission_classes=[IsOrderManager])
    def reject(self, request, pk=None):
        return self._admin_decide(request, ReturnRequest.Status.REJECTED)

    @action(detail=True, methods=["post"], permission_classes=[IsOrderManager])
    def replace(self, request, pk=None):
        return_request = self.get_object()
        with transaction.atomic():
            previous = return_request.status
            return_request.status = ReturnRequest.Status.REPLACED
            return_request.decided_by = request.user
            return_request.decided_at = timezone.now()
            return_request.admin_decision = request.data.get("decision", "Remplacement accepte")
            return_request.save(update_fields=["status", "decided_by", "decided_at", "admin_decision", "updated_at"])
            ReturnHistory.objects.create(return_request=return_request, from_status=previous, to_status=return_request.status, actor=request.user, note=return_request.admin_decision)
            AuditLog.objects.create(actor=request.user, action="RETURN_REPLACED", entity="ReturnRequest", entity_id=str(return_request.pk), ip_address=client_ip(request))
        return Response(self.get_serializer(return_request).data)

    @action(detail=True, methods=["post"], permission_classes=[IsOrderManager])
    def refund(self, request, pk=None):
        return_request = self.get_object()
        amount = Decimal(str(request.data.get("amount", return_request.order.total)))
        serializer = RefundSerializer(data={"order": return_request.order_id, "amount": amount, "method": request.data.get("method", "MANUAL"), "reference": request.data.get("reference", ""), "status": Refund.Status.APPROVED, "reason": request.data.get("reason", return_request.reason)})
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            refund = serializer.save(processed_by=request.user, processed_at=timezone.now())
            previous = return_request.status
            return_request.status = ReturnRequest.Status.REFUNDED
            return_request.decided_by = request.user
            return_request.decided_at = timezone.now()
            return_request.admin_decision = request.data.get("decision", "Remboursement accepte")
            return_request.save(update_fields=["status", "decided_by", "decided_at", "admin_decision", "updated_at"])
            ReturnHistory.objects.create(return_request=return_request, from_status=previous, to_status=return_request.status, actor=request.user, note=f"Remboursement {refund.amount} MAD")
            AuditLog.objects.create(actor=request.user, action="REFUND_CREATED", entity="Refund", entity_id=str(refund.pk), after={"order": return_request.order_id, "amount": str(refund.amount)}, ip_address=client_ip(request))
        return Response({"return": self.get_serializer(return_request).data, "refund": RefundSerializer(refund).data})


class HomepageBannerViewSet(viewsets.ModelViewSet):
    serializer_class = HomepageBannerSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = HomepageBanner.objects.order_by("-created_at")
        user = self.request.user
        if not (user.is_authenticated and user.role == User.Role.SUPER_ADMIN):
            qs = qs.filter(is_active=True)
        return qs

    def get_permissions(self):
        if self.request.method in {"GET", "HEAD", "OPTIONS"}:
            return [AllowAny()]
        return [IsDeveloper()]


class HomeSectionViewSet(viewsets.ModelViewSet):
    serializer_class = HomeSectionSerializer
    permission_classes = [IsCatalogManagerOrReadOnly]
    lookup_field = "key"
    ordering_fields = ["display_order", "title"]

    def get_queryset(self):
        qs = HomeSection.objects.prefetch_related("products__category", "products__brand", "products__images", "products__variants__values").order_by("display_order", "title")
        user = self.request.user
        if self.request.query_params.get("public") == "true" or not (user.is_authenticated and user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER}):
            qs = qs.filter(is_visible=True)
        return qs

    def destroy(self, request, *args, **kwargs):
        section = self.get_object()
        if section.products.exists():
            return Response({"detail": "Retirez tous les produits de cette section avant de la supprimer."}, status=400)
        return super().destroy(request, *args, **kwargs)


class HomeDesignSettingsView(APIView):
    permission_classes = [AllowAny]

    def get_permissions(self):
        if self.request.method in {"PATCH", "PUT"}:
            return [IsDeveloper()]
        return [AllowAny()]

    def get(self, request):
        _settings_row, value = home_design_settings()
        return Response(value)

    def patch(self, request):
        settings_row, current = home_design_settings()
        allowed = set(HOME_DESIGN_DEFAULTS)
        cleaned = {}
        for key, value in request.data.items():
            if key in allowed:
                cleaned[key] = str(value).strip()
        settings_row.value = {**current, **cleaned}
        settings_row.save(update_fields=["value", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            action="HOME_DESIGN_UPDATED",
            entity="SiteSettings",
            entity_id=str(settings_row.pk),
            after=cleaned,
            ip_address=client_ip(request),
        )
        return Response(settings_row.value)


class NewsletterSubscribeView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "auth"

    def post(self, request):
        email = str(request.data.get("email", "")).strip().lower()
        existing = NewsletterSubscriber.objects.filter(email=email).first()
        if existing:
            existing.is_active = True
            existing.save(update_fields=["is_active", "updated_at"])
            return Response(NewsletterSubscriberSerializer(existing).data, status=status.HTTP_201_CREATED)
        serializer = NewsletterSubscriberSerializer(data={"email": email})
        serializer.is_valid(raise_exception=True)
        subscriber, _ = NewsletterSubscriber.objects.update_or_create(
            email=serializer.validated_data["email"],
            defaults={"is_active": True},
        )
        return Response(NewsletterSubscriberSerializer(subscriber).data, status=status.HTTP_201_CREATED)


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
        return get_user_model().objects.exclude(role=User.Role.CUSTOMER).annotate(
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
        if user.role == User.Role.SUPER_ADMIN and request.user.role != User.Role.SUPER_ADMIN:
            return Response({"detail": "Seul un Developer peut supprimer un Developer."}, status=403)
        if user.role == User.Role.SUPER_ADMIN and get_user_model().objects.filter(role=User.Role.SUPER_ADMIN, status=User.Status.ACTIVE).count() <= 1:
            return Response({"detail": "Impossible de supprimer le dernier Developer actif."}, status=400)
        before = {"email": user.email, "role": user.role}
        user.delete()
        AuditLog.objects.create(actor=request.user, action="USER_DELETED", entity="User", entity_id=str(user.pk), before=before, ip_address=client_ip(request))
        return Response(status=204)


class CustomerAdminViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DeveloperUserSerializer
    permission_classes = [IsAdminRole]
    search_fields = ["email", "username", "first_name", "last_name", "phone"]
    filterset_fields = ["status"]
    ordering_fields = ["date_joined", "last_login", "email"]

    def get_queryset(self):
        return get_user_model().objects.filter(role=User.Role.CUSTOMER).annotate(
            order_count=Count("orders", distinct=True),
            total_spent=Sum("orders__total"),
        ).order_by("-date_joined")

    def _normalize_phone(self, phone):
        digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
        if digits.startswith("0"):
            digits = f"212{digits[1:]}"
        return digits

    def _normalize_name(self, name):
        return " ".join(str(name or "").strip().lower().split())

    def _client_key_for_order(self, order):
        phone = self._normalize_phone(order.shipping_phone)
        if phone:
            return f"phone:{phone}"
        name = self._normalize_name(order.shipping_full_name)
        if name:
            return f"name:{name}"
        return f"email:{str(order.guest_email or '').strip().lower()}" or f"order:{order.pk}"

    def _matching_orders_for_seed(self, seed):
        seed_phone = self._normalize_phone(seed.shipping_phone)
        seed_name = self._normalize_name(seed.shipping_full_name)
        matches = []
        for order in Order.objects.select_related("user", "delivery_zone").prefetch_related("items", "status_history").order_by("-created_at"):
            same_phone = seed_phone and self._normalize_phone(order.shipping_phone) == seed_phone
            same_name = seed_name and self._normalize_name(order.shipping_full_name) == seed_name
            if same_phone or same_name or order.pk == seed.pk:
                matches.append(order)
        return matches

    def list(self, request, *args, **kwargs):
        search = request.query_params.get("search", "").strip().lower()
        status_filter = request.query_params.get("status", "")
        source_filter = request.query_params.get("source", "")
        min_orders = int(request.query_params.get("min_orders") or 0)

        all_orders = list(Order.objects.select_related("user", "delivery_zone").prefetch_related("items", "status_history").order_by("-created_at"))
        registered = DeveloperUserSerializer(self.get_queryset(), many=True).data
        rows = []
        registered_keys = set()
        for row in registered:
            account_phone = self._normalize_phone(row.get("phone"))
            account_name = self._normalize_name(f"{row.get('first_name', '')} {row.get('last_name', '')}")
            keys = {key for key in [f"phone:{account_phone}" if account_phone else "", f"name:{account_name}" if account_name else ""] if key}
            registered_keys.update(keys)
            matched_orders = [
                order for order in all_orders
                if order.user_id == row["id"] or self._client_key_for_order(order) in keys
            ]
            row["source"] = "ACCOUNT"
            row["order_count"] = len(matched_orders)
            row["total_spent"] = str(sum((order.total or Decimal("0.00")) for order in matched_orders) or Decimal("0.00"))
            rows.append(row)

        guest_groups = {}
        guest_orders = [order for order in all_orders if not order.user_id]
        for order in guest_orders:
            key = self._client_key_for_order(order)
            if key in registered_keys:
                continue
            group = guest_groups.setdefault(key, {
                "id": f"guest-{order.pk}",
                "email": order.guest_email,
                "username": "",
                "first_name": order.shipping_full_name,
                "last_name": "",
                "phone": order.shipping_phone,
                "role": "CUSTOMER",
                "status": "GUEST",
                "date_joined": order.created_at,
                "last_login": None,
                "order_count": 0,
                "total_spent": Decimal("0.00"),
                "source": "GUEST",
            })
            group["order_count"] += 1
            group["total_spent"] += order.total or Decimal("0.00")
            if order.created_at > group["date_joined"]:
                group["date_joined"] = order.created_at
                group["first_name"] = order.shipping_full_name
                group["email"] = order.guest_email
                group["phone"] = order.shipping_phone

        for row in guest_groups.values():
            row["date_joined"] = row["date_joined"].isoformat()
            row["total_spent"] = str(row["total_spent"])
            rows.append(row)

        if source_filter:
            rows = [row for row in rows if row.get("source") == source_filter]
        if status_filter:
            rows = [row for row in rows if row.get("status") == status_filter]
        if search:
            rows = [
                row for row in rows
                if search in " ".join(str(row.get(field) or "").lower() for field in ["email", "username", "first_name", "last_name", "phone"]).lower()
            ]
        if min_orders:
            rows = [row for row in rows if int(row.get("order_count") or 0) >= min_orders]

        rows.sort(key=lambda row: str(row.get("date_joined") or ""), reverse=True)
        page = self.paginate_queryset(rows)
        if page is not None:
            return self.get_paginated_response(page)
        return Response({"count": len(rows), "results": rows})

    @action(detail=True, methods=["get"], permission_classes=[IsAdminRole])
    def orders(self, request, pk=None):
        if str(pk).startswith("guest-"):
            seed = Order.objects.get(pk=str(pk).replace("guest-", "", 1), user__isnull=True)
            return Response(OrderSerializer(self._matching_orders_for_seed(seed), many=True).data)
        customer = self.get_object()
        customer_phone = self._normalize_phone(customer.phone)
        customer_name = self._normalize_name(f"{customer.first_name} {customer.last_name}")
        matches = []
        for order in Order.objects.select_related("user", "delivery_zone").prefetch_related("items", "status_history").order_by("-created_at"):
            same_phone = customer_phone and self._normalize_phone(order.shipping_phone) == customer_phone
            same_name = customer_name and self._normalize_name(order.shipping_full_name) == customer_name
            if order.user_id == customer.id or order.guest_email.lower() == customer.email.lower() or same_phone or same_name:
                matches.append(order)
        return Response(OrderSerializer(matches, many=True).data)

    @action(detail=True, methods=["patch"], permission_classes=[IsAdminRole])
    def status(self, request, pk=None):
        customer = self.get_object()
        next_status = request.data.get("status")
        if next_status not in User.Status.values:
            return Response({"status": "Statut invalide."}, status=400)
        before = {"email": customer.email, "status": customer.status}
        customer.status = next_status
        customer.save(update_fields=["status"])
        AuditLog.objects.create(
            actor=request.user,
            action="CUSTOMER_STATUS_UPDATED",
            entity="User",
            entity_id=str(customer.pk),
            before=before,
            after={"email": customer.email, "status": customer.status},
            ip_address=client_ip(request),
        )
        return Response(DeveloperUserSerializer(customer, context={"request": request}).data)


class DeveloperDashboardView(APIView):
    permission_classes = [IsDeveloper]

    def get(self, request):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        date_from = parse_date(request.query_params.get("date_from") or "") or today - timedelta(days=13)
        date_to = parse_date(request.query_params.get("date_to") or "") or today
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        range_start = timezone.make_aware(datetime.combine(date_from, time.min))
        range_end = timezone.make_aware(datetime.combine(date_to, time.max))
        today_start = timezone.make_aware(datetime.combine(today, time.min))
        today_end = timezone.make_aware(datetime.combine(today, time.max))
        month_start_dt = timezone.make_aware(datetime.combine(month_start, time.min))

        base_orders = Order.objects.select_related("user", "delivery_zone").filter(created_at__gte=range_start, created_at__lte=range_end)
        city = str(request.query_params.get("city", "")).strip()
        search = str(request.query_params.get("search", "")).strip()
        status_filter = str(request.query_params.get("status", "")).strip()
        if city:
            base_orders = base_orders.filter(shipping_city__iexact=city)
        if search:
            base_orders = base_orders.filter(
                Q(order_number__icontains=search)
                | Q(shipping_full_name__icontains=search)
                | Q(shipping_phone__icontains=search)
                | Q(guest_email__icontains=search)
                | Q(tracking_number__icontains=search)
            )

        delivered_events = OrderStatusHistory.objects.select_related("order").filter(created_at__gte=range_start, created_at__lte=range_end, to_status=Order.Status.DELIVERED)
        if city:
            delivered_events = delivered_events.filter(order__shipping_city__iexact=city)
        if search:
            delivered_events = delivered_events.filter(
                Q(order__order_number__icontains=search)
                | Q(order__shipping_full_name__icontains=search)
                | Q(order__shipping_phone__icontains=search)
                | Q(order__guest_email__icontains=search)
                | Q(order__tracking_number__icontains=search)
            )

        status_breakdown_qs = base_orders.values("status").annotate(total=Count("id")).order_by("status")
        filtered_orders = base_orders
        if status_filter:
            filtered_orders = filtered_orders.filter(status=status_filter)
            if status_filter != Order.Status.DELIVERED:
                delivered_events = delivered_events.none()

        delivered = Order.objects.filter(status=Order.Status.DELIVERED)
        orders_by_status = {row["status"]: row["total"] for row in status_breakdown_qs}
        total_filtered = filtered_orders.count()
        delivered_order_ids = list(delivered_events.values_list("order_id", flat=True).distinct())
        delivered_count = len(delivered_order_ids)
        cancelled_count = filtered_orders.filter(status=Order.Status.CANCELLED).count()
        active_for_rate = max(total_filtered - cancelled_count, delivered_count + cancelled_count)
        delivery_rate = (delivered_count / active_for_rate * 100) if active_for_rate else 0
        filtered_revenue = Order.objects.filter(pk__in=delivered_order_ids).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
        filtered_expenses = Expense.objects.filter(date__gte=date_from, date__lte=date_to)
        if search:
            filtered_expenses = filtered_expenses.filter(Q(category__icontains=search) | Q(reference__icontains=search) | Q(notes__icontains=search))
        expenses_total = filtered_expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        delivered_cost = Decimal("0.00")
        for item in OrderItem.objects.select_related("product").filter(order_id__in=delivered_order_ids):
            cost_price = item.product.cost_price if item.product and item.product.cost_price is not None else Decimal("0.00")
            delivered_cost += cost_price * item.quantity
        gross_profit = filtered_revenue - delivered_cost
        sales_by_day = [
            {
                "day": (date_from + timedelta(days=offset)).isoformat(),
                "sales": Order.objects.filter(
                    pk__in=delivered_events.filter(
                        created_at__gte=timezone.make_aware(datetime.combine(date_from + timedelta(days=offset), time.min)),
                        created_at__lte=timezone.make_aware(datetime.combine(date_from + timedelta(days=offset), time.max)),
                    ).values_list("order_id", flat=True).distinct()
                ).aggregate(total=Sum("total"))["total"] or 0,
                "orders": filtered_orders.filter(
                    created_at__gte=timezone.make_aware(datetime.combine(date_from + timedelta(days=offset), time.min)),
                    created_at__lte=timezone.make_aware(datetime.combine(date_from + timedelta(days=offset), time.max)),
                ).count(),
            }
            for offset in range((date_to - date_from).days + 1)
        ]
        top_products = (
            OrderItem.objects.filter(order__in=filtered_orders)
            .values("product_name", "sku")
            .annotate(sales_count=Sum("quantity"), revenue=Sum("total"))
            .order_by("-sales_count", "-revenue")[:8]
        )
        latest_orders = filtered_orders.order_by("-created_at").values("id", "order_number", "status", "shipping_full_name", "shipping_city", "total", "created_at")[:8]
        city_breakdown = (
            filtered_orders.values("shipping_city")
            .annotate(order_count=Count("id"), revenue=Sum("total"))
            .order_by("-order_count", "shipping_city")[:8]
        )
        cities = list(Order.objects.exclude(shipping_city="").values_list("shipping_city", flat=True).distinct().order_by("shipping_city"))
        data = {
            **dashboard_metrics(),
            "revenue_total": delivered.aggregate(total=Sum("total"))["total"] or 0,
            "revenue_today": delivered.filter(updated_at__gte=today_start, updated_at__lte=today_end).aggregate(total=Sum("total"))["total"] or 0,
            "revenue_month": delivered.filter(updated_at__gte=month_start_dt).aggregate(total=Sum("total"))["total"] or 0,
            "filtered_revenue": filtered_revenue,
            "filtered_expenses": expenses_total,
            "gross_profit": gross_profit,
            "net_profit": gross_profit - expenses_total,
            "filtered_orders": total_filtered,
            "new_orders": filtered_orders.filter(status=Order.Status.PENDING).count(),
            "confirmed_orders": filtered_orders.filter(status=Order.Status.CONFIRMED).count(),
            "preparing_orders": filtered_orders.filter(status=Order.Status.PREPARING).count(),
            "shipped_orders": filtered_orders.filter(status__in=[Order.Status.SHIPPED, Order.Status.OUT_FOR_DELIVERY]).count(),
            "delivered_orders": delivered_count,
            "cancelled_orders": cancelled_count,
            "returned_orders": filtered_orders.filter(status__in=[Order.Status.RETURN_REQUESTED, Order.Status.RETURNED, Order.Status.REFUNDED]).count(),
            "delivery_rate": round(delivery_rate, 1),
            "products_total": Product.objects.count(),
            "products_active": Product.objects.filter(status=Product.Status.ACTIVE).count(),
            "customers_total": get_user_model().objects.filter(role=User.Role.CUSTOMER).count(),
            "customers_new": get_user_model().objects.filter(role=User.Role.CUSTOMER, date_joined__date__gte=month_start).count(),
            "orders_by_status": orders_by_status,
            "sales_by_day": sales_by_day,
            "top_products": list(top_products),
            "latest_orders": list(latest_orders),
            "city_breakdown": list(city_breakdown),
            "cities": cities,
            "status_options": [{"value": value, "label": label} for value, label in Order.Status.choices],
            "filters": {"date_from": date_from, "date_to": date_to, "city": city, "status": status_filter, "search": search},
            "unread_notifications": CustomerNotification.objects.filter(is_read=False).count(),
        }
        return Response(data)


class DeveloperAnalyticsView(APIView):
    permission_classes = [IsDeveloper]

    def _filters(self, request):
        today = timezone.localdate()
        date_from = parse_date(request.query_params.get("date_from") or "") or today - timedelta(days=29)
        date_to = parse_date(request.query_params.get("date_to") or "") or today
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        return {
            "date_from": date_from,
            "date_to": date_to,
            "range_start": timezone.make_aware(datetime.combine(date_from, time.min)),
            "range_end": timezone.make_aware(datetime.combine(date_to, time.max)),
            "city": str(request.query_params.get("city", "")).strip(),
            "search": str(request.query_params.get("search", "")).strip(),
        }

    def _order_filters(self, qs, filters, prefix=""):
        city_field = f"{prefix}shipping_city"
        if filters["city"]:
            qs = qs.filter(**{f"{city_field}__iexact": filters["city"]})
        if filters["search"]:
            lookup_prefix = prefix
            qs = qs.filter(
                Q(**{f"{lookup_prefix}order_number__icontains": filters["search"]})
                | Q(**{f"{lookup_prefix}shipping_full_name__icontains": filters["search"]})
                | Q(**{f"{lookup_prefix}shipping_phone__icontains": filters["search"]})
                | Q(**{f"{lookup_prefix}guest_email__icontains": filters["search"]})
                | Q(**{f"{lookup_prefix}tracking_number__icontains": filters["search"]})
            )
        return qs

    def _cancel_reason(self, note):
        marker = "Annulation:"
        text = str(note or "")
        if marker not in text:
            return "Non precisee"
        reason = text.split(marker, 1)[1].strip().split(".", 1)[0].strip()
        return reason or "Non precisee"

    def get(self, request):
        filters = self._filters(request)
        created_orders = Order.objects.filter(created_at__gte=filters["range_start"], created_at__lte=filters["range_end"])
        created_orders = self._order_filters(created_orders, filters)
        expenses = Expense.objects.select_related("supplier", "created_by").filter(date__gte=filters["date_from"], date__lte=filters["date_to"])
        if filters["search"]:
            expenses = expenses.filter(Q(category__icontains=filters["search"]) | Q(reference__icontains=filters["search"]) | Q(notes__icontains=filters["search"]) | Q(supplier__name__icontains=filters["search"]))
        confirmed_events = OrderStatusHistory.objects.select_related("order").filter(created_at__gte=filters["range_start"], created_at__lte=filters["range_end"], to_status=Order.Status.CONFIRMED)
        delivered_events = OrderStatusHistory.objects.select_related("order").filter(created_at__gte=filters["range_start"], created_at__lte=filters["range_end"], to_status=Order.Status.DELIVERED)
        cancelled_events = OrderStatusHistory.objects.select_related("order").filter(created_at__gte=filters["range_start"], created_at__lte=filters["range_end"], to_status=Order.Status.CANCELLED)
        confirmed_events = self._order_filters(confirmed_events, filters, "order__")
        delivered_events = self._order_filters(delivered_events, filters, "order__")
        cancelled_events = self._order_filters(cancelled_events, filters, "order__")

        created_count = created_orders.count()
        confirmed_ids = list(confirmed_events.values_list("order_id", flat=True).distinct())
        delivered_ids = list(delivered_events.values_list("order_id", flat=True).distinct())
        cancelled_ids = list(cancelled_events.values_list("order_id", flat=True).distinct())
        delivered_orders = Order.objects.filter(pk__in=delivered_ids)
        delivered_revenue = delivered_orders.aggregate(total=Sum("total"))["total"] or Decimal("0.00")

        day_rows = []
        for offset in range((filters["date_to"] - filters["date_from"]).days + 1):
            day = filters["date_from"] + timedelta(days=offset)
            day_start = timezone.make_aware(datetime.combine(day, time.min))
            day_end = timezone.make_aware(datetime.combine(day, time.max))
            day_rows.append(
                {
                    "day": day.isoformat(),
                    "created": created_orders.filter(created_at__gte=day_start, created_at__lte=day_end).count(),
                    "confirmed": confirmed_events.filter(created_at__gte=day_start, created_at__lte=day_end).values("order_id").distinct().count(),
                    "delivered": delivered_events.filter(created_at__gte=day_start, created_at__lte=day_end).values("order_id").distinct().count(),
                    "cancelled": cancelled_events.filter(created_at__gte=day_start, created_at__lte=day_end).values("order_id").distinct().count(),
                }
            )

        cancellation_reasons = {}
        for order in Order.objects.filter(pk__in=cancelled_ids):
            reason = self._cancel_reason(order.internal_note)
            cancellation_reasons[reason] = cancellation_reasons.get(reason, 0) + 1

        delivery_hours = []
        for event in delivered_events:
            if event.order.created_at:
                delivery_hours.append((event.created_at - event.order.created_at).total_seconds() / 3600)
        avg_delivery_hours = round(sum(delivery_hours) / len(delivery_hours), 1) if delivery_hours else 0

        margin_rows = []
        totals = {"revenue": Decimal("0.00"), "cost": Decimal("0.00"), "profit": Decimal("0.00"), "units": 0}
        for item in OrderItem.objects.select_related("product", "order").filter(order_id__in=delivered_ids):
            cost_price = item.product.cost_price if item.product and item.product.cost_price is not None else Decimal("0.00")
            revenue = item.total or Decimal("0.00")
            cost = cost_price * item.quantity
            profit = revenue - cost
            totals["revenue"] += revenue
            totals["cost"] += cost
            totals["profit"] += profit
            totals["units"] += item.quantity
            margin_rows.append(
                {
                    "product_name": item.product_name,
                    "sku": item.sku,
                    "quantity": item.quantity,
                    "revenue": revenue,
                    "cost": cost,
                    "profit": profit,
                    "margin_rate": round((profit / revenue * 100), 1) if revenue else 0,
                }
            )
        grouped = {}
        for row in margin_rows:
            group = grouped.setdefault(row["sku"], {**row, "quantity": 0, "revenue": Decimal("0.00"), "cost": Decimal("0.00"), "profit": Decimal("0.00")})
            group["quantity"] += row["quantity"]
            group["revenue"] += row["revenue"]
            group["cost"] += row["cost"]
            group["profit"] += row["profit"]
            group["margin_rate"] = round((group["profit"] / group["revenue"] * 100), 1) if group["revenue"] else 0
        expenses_total = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        expenses_by_category = list(expenses.values("category").annotate(amount=Sum("amount"), count=Count("id")).order_by("-amount", "category"))
        latest_expenses = list(expenses.order_by("-date", "-created_at").values("id", "category", "amount", "date", "reference", "notes")[:10])
        net_profit = totals["profit"] - expenses_total

        return Response(
            {
                "filters": {"date_from": filters["date_from"], "date_to": filters["date_to"], "city": filters["city"], "search": filters["search"]},
                "cities": list(Order.objects.exclude(shipping_city="").values_list("shipping_city", flat=True).distinct().order_by("shipping_city")),
                "orders": {
                    "created": created_count,
                    "confirmed": len(confirmed_ids),
                    "delivered": len(delivered_ids),
                    "cancelled": len(cancelled_ids),
                    "confirmation_rate": round((len(confirmed_ids) / created_count * 100), 1) if created_count else 0,
                    "delivery_rate": round((len(delivered_ids) / max(created_count - len(cancelled_ids), len(delivered_ids)) * 100), 1) if (created_count or delivered_ids) else 0,
                    "cancel_rate": round((len(cancelled_ids) / created_count * 100), 1) if created_count else 0,
                    "avg_delivery_hours": avg_delivery_hours,
                    "delivered_revenue": delivered_revenue,
                },
                "daily": day_rows,
                "cancellation_reasons": [{"reason": reason, "count": count} for reason, count in sorted(cancellation_reasons.items(), key=lambda item: item[1], reverse=True)],
                "city_breakdown": list(created_orders.values("shipping_city").annotate(order_count=Count("id"), revenue=Sum("total")).order_by("-order_count")[:10]),
                "margins": {
                    "revenue": totals["revenue"],
                    "cost": totals["cost"],
                    "gross_profit": totals["profit"],
                    "profit": net_profit,
                    "expenses": expenses_total,
                    "units": totals["units"],
                    "margin_rate": round((net_profit / totals["revenue"] * 100), 1) if totals["revenue"] else 0,
                    "products": sorted(grouped.values(), key=lambda row: row["profit"], reverse=True)[:20],
                },
                "expenses": {
                    "total": expenses_total,
                    "count": expenses.count(),
                    "by_category": expenses_by_category,
                    "latest": latest_expenses,
                },
            }
        )


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
    permission_classes = [IsAdminRole]

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

    def post(self, request):
        variant_id = request.data.get("variant_id")
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"reason": "Motif obligatoire."}, status=400)
        try:
            quantity = int(request.data.get("quantity"))
        except (TypeError, ValueError):
            return Response({"quantity": "Quantite invalide."}, status=400)
        with transaction.atomic():
            inventory = Inventory.objects.select_for_update().select_related("variant__product").get(variant_id=variant_id)
            before = inventory.quantity
            inventory.quantity = max(quantity, 0)
            inventory.save(update_fields=["quantity"])
            delta = inventory.quantity - before
            StockMovement.objects.create(variant=inventory.variant, movement_type=StockMovement.Type.ADJUSTMENT, quantity=delta, reason=reason, actor=request.user)
            AuditLog.objects.create(actor=request.user, action="STOCK_ADJUSTED", entity="Inventory", entity_id=str(inventory.pk), before={"quantity": before}, after={"quantity": inventory.quantity, "reason": reason}, ip_address=client_ip(request))
        return Response({"variant_id": variant_id, "quantity": inventory.quantity, "delta": delta})


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [IsDeveloper]
    search_fields = ["name", "base_url"]
    filterset_fields = ["is_active"]
    ordering_fields = ["name", "created_at", "percentage_margin"]

    def get_queryset(self):
        return Supplier.objects.annotate(product_count=Count("external_products")).order_by("name")

    def perform_create(self, serializer):
        supplier = serializer.save()
        AuditLog.objects.create(actor=self.request.user, action="SUPPLIER_CREATED", entity="Supplier", entity_id=str(supplier.pk), after={"name": supplier.name}, ip_address=client_ip(self.request))


class ExpenseViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    permission_classes = [IsDeveloper]
    search_fields = ["category", "reference", "notes", "supplier__name"]
    filterset_fields = ["category", "supplier"]
    ordering_fields = ["date", "amount", "created_at"]

    def get_queryset(self):
        return Expense.objects.select_related("supplier", "created_by").order_by("-date", "-created_at")

    def perform_create(self, serializer):
        expense = serializer.save(created_by=self.request.user)
        AuditLog.objects.create(actor=self.request.user, action="EXPENSE_CREATED", entity="Expense", entity_id=str(expense.pk), after={"amount": str(expense.amount), "category": expense.category}, ip_address=client_ip(self.request))


class RefundViewSet(viewsets.ModelViewSet):
    serializer_class = RefundSerializer
    permission_classes = [IsOrderManager]
    search_fields = ["order__order_number", "reference", "reason"]
    filterset_fields = ["status", "method"]
    ordering_fields = ["created_at", "amount"]

    def get_queryset(self):
        return Refund.objects.select_related("order", "processed_by").order_by("-created_at")

    def perform_create(self, serializer):
        refund = serializer.save(processed_by=self.request.user, processed_at=timezone.now())
        AuditLog.objects.create(actor=self.request.user, action="REFUND_CREATED", entity="Refund", entity_id=str(refund.pk), after={"amount": str(refund.amount), "order": refund.order_id}, ip_address=client_ip(self.request))


class DeveloperExportView(APIView):
    permission_classes = [IsAdminRole]

    def _date_filtered(self, request, qs, field="created_at"):
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(**{f"{field}__date__gte": date_from})
        if date_to:
            qs = qs.filter(**{f"{field}__date__lte": date_to})
        return qs

    def get(self, request, kind):
        rows = []
        if kind == "products":
            headers = ["sku", "name", "status", "regular_price", "current_price", "category", "brand"]
            qs = Product.objects.select_related("category", "brand")
            if request.query_params.get("status"):
                qs = qs.filter(status=request.query_params["status"])
            if request.query_params.get("category"):
                qs = qs.filter(category_id=request.query_params["category"])
            if request.query_params.get("brand"):
                qs = qs.filter(brand_id=request.query_params["brand"])
            rows = [[product.sku, product.name, product.status, product.regular_price, product.current_price, product.category.name, product.brand.name if product.brand else ""] for product in qs]
        elif kind == "orders":
            headers = ["order_number", "status", "customer", "city", "total", "created_at"]
            qs = self._date_filtered(request, Order.objects.all())
            if request.query_params.get("status"):
                qs = qs.filter(status=request.query_params["status"])
            if request.query_params.get("city"):
                qs = qs.filter(shipping_city__iexact=request.query_params["city"])
            rows = [[order.order_number, order.status, order.shipping_full_name, order.shipping_city, order.total, order.created_at] for order in qs]
        elif kind == "customers":
            headers = ["email", "first_name", "last_name", "role", "status", "date_joined"]
            qs = self._date_filtered(request, get_user_model().objects.filter(role=User.Role.CUSTOMER), "date_joined")
            if request.query_params.get("status"):
                qs = qs.filter(status=request.query_params["status"])
            rows = [[user.email, user.first_name, user.last_name, user.role, user.status, user.date_joined] for user in qs]
        elif kind == "staff":
            if request.user.role != User.Role.SUPER_ADMIN:
                return Response({"detail": "Export staff reserve au Developer."}, status=403)
            headers = ["email", "first_name", "last_name", "role", "status", "is_staff", "date_joined"]
            qs = self._date_filtered(request, get_user_model().objects.exclude(role=User.Role.CUSTOMER), "date_joined")
            if request.query_params.get("role"):
                qs = qs.filter(role=request.query_params["role"])
            rows = [[user.email, user.first_name, user.last_name, user.role, user.status, user.is_staff, user.date_joined] for user in qs]
        elif kind == "expenses":
            headers = ["category", "amount", "date", "supplier", "reference", "created_by"]
            qs = Expense.objects.select_related("supplier", "created_by")
            if request.query_params.get("category"):
                qs = qs.filter(category__iexact=request.query_params["category"])
            if request.query_params.get("supplier"):
                qs = qs.filter(supplier_id=request.query_params["supplier"])
            if request.query_params.get("date_from"):
                qs = qs.filter(date__gte=request.query_params["date_from"])
            if request.query_params.get("date_to"):
                qs = qs.filter(date__lte=request.query_params["date_to"])
            rows = [[expense.category, expense.amount, expense.date, expense.supplier.name if expense.supplier else "", expense.reference, expense.created_by.email if expense.created_by else ""] for expense in qs]
        elif kind == "stock":
            headers = ["product", "sku", "quantity", "reserved_quantity", "available_quantity", "low_stock_threshold", "state"]
            qs = Inventory.objects.select_related("variant__product").order_by("variant__product__name")
            state = request.query_params.get("state")
            if state == "low":
                qs = qs.filter(quantity__lte=F("variant__product__low_stock_threshold"), quantity__gt=0)
            elif state == "out":
                qs = qs.filter(quantity=0)
            rows = [
                [
                    inv.variant.product.name,
                    inv.variant.sku,
                    inv.quantity,
                    inv.reserved_quantity,
                    inv.available_quantity,
                    inv.variant.product.low_stock_threshold,
                    "out" if inv.quantity == 0 else "low" if inv.quantity <= inv.variant.product.low_stock_threshold else "ok",
                ]
                for inv in qs
            ]
        elif kind == "coupons":
            headers = ["code", "discount_type", "value", "minimum_amount", "is_active", "starts_at", "ends_at"]
            qs = Coupon.objects.all()
            if request.query_params.get("is_active") in {"true", "false"}:
                qs = qs.filter(is_active=request.query_params["is_active"] == "true")
            rows = [[coupon.code, coupon.discount_type, coupon.value, coupon.minimum_amount, coupon.is_active, coupon.starts_at, coupon.ends_at] for coupon in qs]
        elif kind == "suppliers":
            if request.user.role != User.Role.SUPER_ADMIN:
                return Response({"detail": "Export fournisseurs reserve au Developer."}, status=403)
            headers = ["name", "base_url", "percentage_margin", "fixed_cost", "minimum_profit", "is_active"]
            qs = Supplier.objects.all()
            if request.query_params.get("is_active") in {"true", "false"}:
                qs = qs.filter(is_active=request.query_params["is_active"] == "true")
            rows = [[supplier.name, supplier.base_url, supplier.percentage_margin, supplier.fixed_cost, supplier.minimum_profit, supplier.is_active] for supplier in qs]
        elif kind == "margins":
            headers = ["sku", "product", "sales_count", "current_price", "cost_price", "estimated_margin"]
            qs = Product.objects.all()
            rows = [
                [
                    product.sku,
                    product.name,
                    product.sales_count,
                    product.current_price,
                    product.cost_price or Decimal("0.00"),
                    (product.current_price - (product.cost_price or Decimal("0.00"))) * product.sales_count,
                ]
                for product in qs
            ]
        else:
            return Response({"detail": "Export inconnu."}, status=404)
        file_format = request.query_params.get("file_format", "csv")
        AuditLog.objects.create(actor=request.user, action="REPORT_EXPORTED", entity=kind, after={"format": file_format, "filters": dict(request.query_params)}, ip_address=client_ip(request))
        if file_format == "pdf":
            lines = [f"DOLPHIN - Rapport {kind}", f"Genere le {timezone.localtime(timezone.now()):%Y-%m-%d %H:%M}", ""]
            lines.append(" | ".join(headers))
            lines.extend(" | ".join(str(value) for value in row) for row in rows[:120])
            return simple_pdf_response(f"dolphin-{kind}.pdf", lines)
        if file_format == "xlsx":
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = kind[:31]
            sheet.append(headers)
            for row in rows:
                sheet.append(row)
            stream = BytesIO()
            workbook.save(stream)
            stream.seek(0)
            response = HttpResponse(stream.read(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response["Content-Disposition"] = f'attachment; filename="dolphin-{kind}.xlsx"'
            return response
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="dolphin-{kind}.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
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
