from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    AttributeValue,
    AuditLog,
    Brand,
    Cart,
    Category,
    Coupon,
    CustomerAddress,
    DeliveryZone,
    HomepageBanner,
    Inventory,
    Order,
    OrderItem,
    Product,
    ProductAttribute,
    ProductImage,
    ProductImportJob,
    ProductImportRow,
    ProductReview,
    ProductVariant,
    Promotion,
    SupportTicket,
    Supplier,
    SupplierProduct,
    User,
)


@admin.register(User)
class DolphinUserAdmin(UserAdmin):
    list_display = ("email", "username", "role", "status", "is_staff")
    list_filter = ("role", "status", "is_staff")
    fieldsets = UserAdmin.fieldsets + (("DOLPHIN", {"fields": ("role", "status", "phone", "avatar", "email_verified_at", "token_version")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("DOLPHIN", {"fields": ("email", "role", "status")}),)


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class InventoryInline(admin.StackedInline):
    model = Inventory
    extra = 0
    can_delete = False


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "is_active")
    inlines = [InventoryInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "category", "regular_price", "promotional_price", "status", "featured")
    list_filter = ("status", "featured", "new_arrival", "bestseller", "category", "brand")
    search_fields = ("name", "sku", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "display_order", "is_active", "is_archived")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    readonly_fields = ("product_name", "sku", "unit_price", "quantity", "total")
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "user", "guest_email", "status", "payment_method", "shipping_city", "total", "created_at")
    list_filter = ("status", "payment_method", "shipping_city")
    search_fields = ("order_number", "user__email", "guest_email", "shipping_phone", "tracking_number")
    inlines = [OrderItemInline]


admin.site.register(CustomerAddress)
admin.site.register(ProductAttribute)
admin.site.register(AttributeValue)
admin.site.register(Cart)
admin.site.register(Coupon)
admin.site.register(Promotion)
admin.site.register(DeliveryZone)
admin.site.register(ProductReview)
admin.site.register(SupportTicket)
admin.site.register(HomepageBanner)
admin.site.register(AuditLog)
admin.site.register(ProductImportJob)
admin.site.register(ProductImportRow)
admin.site.register(Supplier)
admin.site.register(SupplierProduct)
