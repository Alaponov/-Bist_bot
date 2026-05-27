from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        admin = 'admin', 'Admin'
        customer = 'customer', 'Customer'

    telegram_id = models.BigIntegerField(
        null=True,
        blank=True,
        unique=True
    )

    is_telegram_verified = models.BooleanField(
        default=False
    )

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.customer
    )


    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True
    )

    avatar = models.ImageField(
        upload_to='avatars/',
        blank=True,
        null=True
    )

    bio = models.TextField(
        blank=True,
        max_length=500
    )

    # Статистика
    total_orders = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)]
    )

    completed_orders = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)]
    )

    rating = models.FloatField(
        default=5.0,
        validators=[MinValueValidator(0), MaxValueValidator(5)]
    )

    is_active_in_bot = models.BooleanField(
        default=True,
        help_text="Пользователь активен в боте"
    )

    last_activity = models.DateTimeField(
        default=timezone.now
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        null=True,
        blank=True
    )

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"

    def is_admin(self):
        return self.role == self.Role.admin

    def is_customer(self):
        return self.role == self.Role.customer


    def update_activity(self):
        self.last_activity = timezone.now()
        self.save(update_fields=['last_activity'])

    def get_stats(self):
        return {
            'total_orders': self.total_orders,
            'completed_orders': self.completed_orders,
            'rating': self.rating,
            'success_rate': round(
                (self.completed_orders / self.total_orders * 100)
                if self.total_orders > 0 else 0
            )
        }

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['telegram_id']),
            models.Index(fields=['role']),
        ]