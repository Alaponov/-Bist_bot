from rest_framework import generics

from .models import Order
from .serializers import OrderSerializer, DetailOrderSerializer
from rest_framework.permissions import IsAuthenticated

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Order
from .serializers import OrderSerializer, DetailOrderSerializer


class OrderListCreateView(generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user

        # Админ видит все заказы
        if user.is_admin():
            return Order.objects.all()

        # Заказчик видит только свои заказы
        return Order.objects.filter(user=user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class OrderRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DetailOrderSerializer
    permission_classes = (IsAuthenticated,)

    def get_queryset(self):
        user = self.request.user

        # Админ видит все заказы
        if user.is_admin():
            return Order.objects.all()

        # Заказчик видит только свои заказы
        return Order.objects.filter(user=user)






