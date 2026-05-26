from rest_framework import serializers

from .models import Order


class OrderSerializer(serializers.ModelSerializer):
    user_details = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id',
            'text',
            'status',
            'created_at',
            'user_details',
        )

        read_only_fields = (
            'id',
            'created_at',
            'user_details',
        )

    def get_user_details(self, obj):
        return {
            'id': obj.user.id,
            'username': obj.user.username,
        }


class DetailOrderSerializer(serializers.ModelSerializer):
    user_details = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = (
            'id',
            'user',
            'user_details',
            'text',
            'status',
            'created_at',
        )

        read_only_fields = (
            'id',
            'created_at',
            'user_details',
        )

    def get_user_details(self, obj):
        return {
            'id': obj.user.id,
            'username': obj.user.username,
        }

