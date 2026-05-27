from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Notification(models.Model):
    class Type(models.TextChoices):
        ORDER_CREATED = 'order_created', 'Заказ создан'
        ORDER_UPDATED = 'order_updated', 'Заказ обновлён'
        ORDER_COMPLETED = 'order_completed', 'Заказ выполнен'
        ORDER_ASSIGNED = 'order_assigned', 'Вам назначен заказ'
        MESSAGE = 'message', 'Сообщение'

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications'
    )

    notification_type = models.CharField(
        max_length=50,
        choices=Type.choices
    )

    title = models.CharField(max_length=200)
    message = models.TextField()

    related_order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    is_read = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', '-created_at']),
        ]