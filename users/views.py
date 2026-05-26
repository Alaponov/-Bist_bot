from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.authtoken.models import Token
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Order


class LoginView(ObtainAuthToken):

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data,
            context={'request': request}
        )

        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        telegram_id = request.data.get('telegram_id')

        if telegram_id:
            user.telegram_id = telegram_id
            user.is_telegram_verified = True
            user.save()

        token, created = Token.objects.get_or_create(
            user=user
        )

        return Response({
            'token': token.key,
            'user_id': user.id,
            'username': user.username,
            'role': user.role,
        })



class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        orders_count = Order.objects.filter(
            user=request.user
        ).count()

        return Response({
            'username': request.user.username,
            'orders_count': orders_count,
            'role': request.user.role,
        })
