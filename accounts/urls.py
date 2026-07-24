from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    IdCardOcrView,
    LogoutView,
    MePhotoView,
    MeView,
    PatientLoginView,
    PatientPasswordChangeView,
    PatientPinLoginView,
    PatientRegisterView,
    ProLoginView,
    RequestOtpView,
    SetPinView,
    StructureSanteViewSet,
    UserViewSet,
    VerifyPinView,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("structures", StructureSanteViewSet, basename="structure")

urlpatterns = [
    path("otp/", RequestOtpView.as_view(), name="request-otp"),
    path("login/", ProLoginView.as_view(), name="pro-login"),
    path("patient/login/", PatientLoginView.as_view(), name="patient-login"),
    path("patient/register/", PatientRegisterView.as_view(), name="patient-register"),
    path("patient/ocr-id/", IdCardOcrView.as_view(), name="patient-ocr-id"),
    path("patient/password-change/", PatientPasswordChangeView.as_view(), name="patient-password-change"),
    path("patient/pin/", PatientPinLoginView.as_view(), name="patient-pin-login"),
    path("patient/set-pin/", SetPinView.as_view(), name="patient-set-pin"),
    path("set-pin/", SetPinView.as_view(), name="set-pin"),
    path("verify-pin/", VerifyPinView.as_view(), name="verify-pin"),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("me/photo/", MePhotoView.as_view(), name="me-photo"),
    path("", include(router.urls)),
]
