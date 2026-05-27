from rest_framework.permissions import IsAuthenticated, BasePermission
from rest_framework.exceptions import PermissionDenied

class IsAdmin(IsAuthenticated):
    """Только администраторы"""
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_admin()

class IsCustomer(IsAuthenticated):
    """Только заказчики"""
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.is_customer()

# ✅ НОВОЕ: Разрешения на уровне объектов
class IsOrderOwner(IsAuthenticated):
    """Только владелец заказа или администратор"""
    def has_object_permission(self, request, view, obj):
        return obj.user == request.user or request.user.is_admin()

class IsAssignedToOrder(IsAuthenticated):
    """Только назначенный исполнитель или администратор"""
    def has_object_permission(self, request, view, obj):
        return obj.assigned_to == request.user or request.user.is_admin()

class CanDeleteOrder(IsAuthenticated):
    """Только автор заказа или администратор"""
    def has_permission(self, request, view):
        if request.method == 'DELETE':
            order = view.get_object()
            return order.user == request.user or request.user.is_admin()
        return True

# ✅ НОВОЕ: Ограничение по активности
class IsActiveUser(IsAuthenticated):
    """Проверка активности пользователя"""
    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not request.user.is_active_in_bot:
            raise PermissionDenied("Ваш аккаунт деактивирован администратором")
        return True