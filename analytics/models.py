from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


class OrderStatistics(models.Model):
    """Статистика по заказам"""
    date = models.DateField(auto_now_add=True, unique=True)

    total_orders = models.IntegerField(default=0)
    new_orders = models.IntegerField(default=0)
    completed_orders = models.IntegerField(default=0)
    canceled_orders = models.IntegerField(default=0)

    total_revenue = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    avg_completion_time = models.DurationField(null=True)

    class Meta:
        ordering = ['-date']


class UserActivity(models.Model):
    """Активность пользователей"""
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()

    login_count = models.IntegerField(default=0)
    orders_created = models.IntegerField(default=0)
    orders_completed = models.IntegerField(default=0)
    api_calls = models.IntegerField(default=0)

    class Meta:
        unique_together = ('user', 'date')
        ordering = ['-date']