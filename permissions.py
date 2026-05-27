from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.exceptions import PermissionDenied


class IsAdmin(IsAuthenticated):
    """Только администраторы"""
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return hasattr(request.user, 'is_admin') and request.user.is_admin()


class IsCustomer(IsAuthenticated):
    """Только заказчики"""
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return hasattr(request.user, 'is_customer') and request.user.is_customer()


# ✅ НОВОЕ: Разрешения на уровне объектов
class IsOrderOwner(IsAuthenticated):
    """Только владелец заказа или администратор"""
    def has_object_permission(self, request, view, obj):
        if not hasattr(request.user, 'is_admin'):
            return obj.user == request.user
        return obj.user == request.user or request.user.is_admin()


class IsAssignedToOrder(IsAuthenticated):
    """Только назначенный исполнитель или администратор"""
    def has_object_permission(self, request, view, obj):
        if not hasattr(request.user, 'is_admin'):
            return obj.assigned_to == request.user
        return obj.assigned_to == request.user or request.user.is_admin()


class CanDeleteOrder(IsAuthenticated):
    """Только автор заказа или администратор"""
    def has_permission(self, request, view):
        if request.method == 'DELETE':
            try:
                order = view.get_object()
                if not hasattr(request.user, 'is_admin'):
                    return order.user == request.user
                return order.user == request.user or request.user.is_admin()
            except Exception:
                return False
        return True


# ✅ НОВОЕ: Ограничение по активности
class IsActiveUser(IsAuthenticated):
    """Проверка активности пользователя"""
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if hasattr(request.user, 'is_active_in_bot') and not request.user.is_active_in_bot:
            raise PermissionDenied("Ваш аккаунт деактивирован администратором")
        return True