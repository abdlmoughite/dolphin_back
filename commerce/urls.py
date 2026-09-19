from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    AddressViewSet,
    AdminDashboardView,
    AuditLogViewSet,
    BrandViewSet,
    CartViewSet,
    CategoryViewSet,
    ChangePasswordView,
    CheckoutView,
    CustomerAdminViewSet,
    CouponViewSet,
    DeliveryZoneViewSet,
    DeveloperAnalyticsView,
    DeveloperDashboardView,
    DeveloperExportView,
    DeveloperInventoryView,
    DeveloperSystemView,
    DolphinTokenObtainPairView,
    ExpenseViewSet,
    HomepageBannerViewSet,
    HomeSectionViewSet,
    LogoutView,
    MeView,
    NewsletterSubscribeView,
    NotificationViewSet,
    OrderViewSet,
    PasswordResetRequestView,
    ProductViewSet,
    ProductImportViewSet,
    PromotionViewSet,
    RefundViewSet,
    RegisterView,
    ReturnRequestViewSet,
    StaffViewSet,
    SupplierViewSet,
    SupportTicketViewSet,
    WishlistViewSet,
)

router = DefaultRouter()
router.register("categories", CategoryViewSet, basename="category")
router.register("brands", BrandViewSet, basename="brand")
router.register("products", ProductViewSet, basename="product")
router.register("admin/product-imports", ProductImportViewSet, basename="product-import")
router.register("admin/customers", CustomerAdminViewSet, basename="admin-customer")
router.register("cart", CartViewSet, basename="cart")
router.register("orders", OrderViewSet, basename="order")
router.register("addresses", AddressViewSet, basename="address")
router.register("wishlist", WishlistViewSet, basename="wishlist")
router.register("promotions", PromotionViewSet, basename="promotion")
router.register("coupons", CouponViewSet, basename="coupon")
router.register("delivery-zones", DeliveryZoneViewSet, basename="delivery-zone")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("support", SupportTicketViewSet, basename="support")
router.register("returns", ReturnRequestViewSet, basename="return")
router.register("banners", HomepageBannerViewSet, basename="banner")
router.register("home-sections", HomeSectionViewSet, basename="home-section")
router.register("admin/staff", StaffViewSet, basename="staff")
router.register("developer/audit-logs", AuditLogViewSet, basename="developer-audit-log")
router.register("developer/suppliers", SupplierViewSet, basename="developer-supplier")
router.register("developer/expenses", ExpenseViewSet, basename="developer-expense")
router.register("refunds", RefundViewSet, basename="refund")

urlpatterns = [
    path("auth/register/", RegisterView.as_view()),
    path("auth/login/", DolphinTokenObtainPairView.as_view()),
    path("auth/refresh/", TokenRefreshView.as_view()),
    path("auth/logout/", LogoutView.as_view()),
    path("auth/password/change/", ChangePasswordView.as_view()),
    path("auth/password/reset/", PasswordResetRequestView.as_view()),
    path("auth/me/", MeView.as_view()),
    path("newsletter/subscribe/", NewsletterSubscribeView.as_view()),
    path("checkout/", CheckoutView.as_view()),
    path("admin/dashboard/", AdminDashboardView.as_view()),
    path("developer/dashboard/", DeveloperDashboardView.as_view()),
    path("developer/analytics/", DeveloperAnalyticsView.as_view()),
    path("developer/system/", DeveloperSystemView.as_view()),
    path("developer/inventory/", DeveloperInventoryView.as_view()),
    path("developer/export/<str:kind>/", DeveloperExportView.as_view(), name="developer-export"),
    path("reports/export/<str:kind>/", DeveloperExportView.as_view(), name="report-export"),
    path("", include(router.urls)),
]
