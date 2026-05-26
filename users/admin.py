from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Role & Telegram', {
            'fields': ('role', 'telegram_id', 'is_telegram_verified')
        }),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Role & Telegram', {
            'fields': ('role', 'telegram_id')
        }),
    )

    list_display = ('username', 'email', 'role', 'telegram_id', 'is_active')
    list_filter = BaseUserAdmin.list_filter + ('role', 'is_telegram_verified')
    search_fields = ('username', 'email', 'telegram_id')
