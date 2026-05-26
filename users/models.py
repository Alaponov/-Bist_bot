from django.contrib.auth.models import AbstractUser
from django.db import models

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

    def __str__(self):
        return self.username

    def is_admin(self):
        return self.role == self.Role.admin

    def is_customer(self):
        return self.role == self.Role.customer