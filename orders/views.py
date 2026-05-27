from rest_framework import generics, filters
from django_filters.rest_framework import DjangoFilterBackend
from .models import Order
from .serializers import OrderSerializer, DetailOrderSerializer
from rest_framework.permissions import IsAuthenticated
from permissions import IsOrderOwner


class OrderListCreateView(generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    permission_classes = (IsAuthenticated,)


    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter
    ]

    filterset_fields = ['status', 'priority', 'created_at']
    search_fields = ['text', 'tags']
    ordering_fields = ['created_at', 'deadline', 'priority']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        queryset = Order.objects.all()

        # Заказчик видит только свои заказы
        if user.is_customer():
            queryset = queryset.filter(user=user)


        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)

        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class OrderRetrieveUpdateDestroyView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DetailOrderSerializer
    permission_classes = (IsAuthenticated, IsOrderOwner)

    def get_queryset(self):
        user = self.request.user
        if user.is_admin():
            return Order.objects.all()
        return Order.objects.filter(user=user)





