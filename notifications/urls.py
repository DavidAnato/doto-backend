from django.urls import path
from rest_framework.routers import DefaultRouter

from .device_views import DeviceTokenViewSet
from .views import NotificationViewSet, UserEventsView

router = DefaultRouter()
router.register("notifications", NotificationViewSet, basename="notification")
router.register("device-tokens", DeviceTokenViewSet, basename="device-token")

urlpatterns = [
    path("events/", UserEventsView.as_view(), name="user-events"),
    path("patient/events/", UserEventsView.as_view(), name="patient-events"),
]
urlpatterns += router.urls
