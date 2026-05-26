from django.conf import settings
from django.db import models


class Order(models.Model):

    class Status(models.TextChoices):
        NEW = 'NEW', 'New'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        DONE = 'DONE', 'Done'
        CANCELED = 'CANCELED', 'Canceled'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='orders'
    )

    text = models.TextField()

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f'Order #{self.id} - {self.user.username}'