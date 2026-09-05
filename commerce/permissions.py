from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import User


ADMIN_ROLES = {
    User.Role.SUPER_ADMIN,
    User.Role.MANAGER,
    User.Role.ORDER_OPERATOR,
    User.Role.CUSTOMER_SUPPORT,
}
CATALOG_MANAGERS = {User.Role.SUPER_ADMIN, User.Role.MANAGER}
ORDER_MANAGERS = {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.ORDER_OPERATOR}
SUPPORT_ROLES = {User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.CUSTOMER_SUPPORT}


class IsActiveUser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.status == User.Status.ACTIVE)


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ADMIN_ROLES)


class IsDeveloper(BasePermission):
    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.status == User.Status.ACTIVE
            and request.user.role == User.Role.SUPER_ADMIN
            and request.user.is_staff
            and request.user.is_superuser
        )


class IsAdminOrDeveloper(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in {User.Role.SUPER_ADMIN, User.Role.MANAGER})


class CanManageUsers(IsDeveloper):
    pass


class CanViewReports(IsAdminRole):
    pass


class IsCatalogManagerOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and request.user.role in CATALOG_MANAGERS)


class IsOrderManager(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in ORDER_MANAGERS)


class CanManageProducts(IsCatalogManagerOrReadOnly):
    pass


class CanManageOrders(IsOrderManager):
    pass


class IsSelfOrAdmin(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj == request.user or request.user.role in ADMIN_ROLES
