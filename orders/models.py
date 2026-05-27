from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils import timezone


class Order(models.Model):
    class Status(models.TextChoices):
        NEW = 'NEW', '🆕 Новый'
        IN_PROGRESS = 'IN_PROGRESS', '⚙️ В процессе'
        DONE = 'DONE', '✅ Выполнен'
        CANCELED = 'CANCELED', '❌ Отменён'

    class Priority(models.IntegerChoices):
        LOW = 1, 'Низкий'
        MEDIUM = 2, 'Средний'
        HIGH = 3, 'Высокий'
        URGENT = 4, 'Срочный'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='orders'
    )

    text = models.TextField(
        max_length=2000,
        verbose_name="Описание заказа"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW
    )

    # ✅ НОВОЕ: Приоритет
    priority = models.IntegerField(
        choices=Priority.choices,
        default=Priority.MEDIUM
    )

    # ✅ НОВОЕ: Даты
    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    started_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Когда заказ был взят в работу"
    )

    completed_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Когда заказ был завершён"
    )

    deadline = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Крайний срок выполнения"
    )

    # ✅ НОВОЕ: Прикрепления и метаданные
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_orders',
        help_text="Кому назначен заказ"
    )

    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        help_text="Стоимость заказа"
    )

    tags = models.CharField(
        max_length=200,
        blank=True,
        help_text="Теги для категоризации"
    )

    is_urgent = models.BooleanField(
        default=False
    )

    notes = models.TextField(
        blank=True,
        help_text="Заметки администратора"
    )

    def __str__(self):
        return f'Order #{self.id} - {self.user.username} [{self.get_status_display()}]'

    # ✅ НОВОЕ: Методы
    def is_overdue(self):
        """Проверяет, просрочен ли заказ"""
        if self.deadline and self.status in [self.Status.NEW, self.Status.IN_PROGRESS]:
            return timezone.now() > self.deadline
        return False

    def get_duration(self):
        """Сколько времени заказ выполняется"""
        start = self.started_at or self.created_at
        end = self.completed_at or timezone.now()
        return end - start

    def mark_in_progress(self):
        """Отметить заказ как выполняемый"""
        self.status = self.Status.IN_PROGRESS
        self.started_at = timezone.now()
        self.save()

    def mark_done(self):
        """
        Отметить заказ как выполненный.
        Использует атомарное обновление счётчика для избежания race conditions.
        """
        self.status = self.Status.DONE
        self.completed_at = timezone.now()
        self.save()

        # Атомарное обновление счётчика - безопасно для параллельных операций
        from django.contrib.auth import get_user_model
        User = get_user_model()
        User.objects.filter(pk=self.user.pk).update(
            completed_orders=F('completed_orders') + 1
        )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['priority']),
        ]


# ✅ НОВОЕ: Модель для отзывов на заказы
class OrderReview(models.Model):
    RATING_CHOICES = [(i, str(i)) for i in range(1, 6)]

    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name='review'
    )

    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews_given'
    )

    rating = models.IntegerField(choices=RATING_CHOICES)

    comment = models.TextField(
        blank=True,
        max_length=500
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        ordering = ['-created_at']

