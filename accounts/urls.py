from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    ContractsView,
    HospitalCatalogView,
    IdCardOcrView,
    LogoutView,
    MePhotoView,
    MeView,
    PatientLoginView,
    PatientPasswordChangeView,
    PatientPinLoginView,
    PatientRegisterView,
    ProLoginView,
    ProRegisterView,
    RequestOtpView,
    SetPinView,
    StructureSanteViewSet,
    UserViewSet,
    VerifyPinView,
)
from .kyc_views import (
    AdminAffiliationViewSet,
    AdminKycViewSet,
    MyAffiliationViewSet,
    MyKycSubmitView,
    MyKycUploadView,
    MyKycView,
)

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("structures", StructureSanteViewSet, basename="structure")
router.register("kyc", AdminKycViewSet, basename="kyc-admin")
router.register("affiliations", AdminAffiliationViewSet, basename="affiliation-admin")
router.register("me/affiliations", MyAffiliationViewSet, basename="my-affiliation")

urlpatterns = [
    path("otp/", RequestOtpView.as_view(), name="request-otp"),
    path("login/", ProLoginView.as_view(), name="pro-login"),
    path("pro/register/", ProRegisterView.as_view(), name="pro-register"),
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
    path("kyc/me/", MyKycView.as_view(), name="kyc-me"),
    path("kyc/me/submit/", MyKycSubmitView.as_view(), name="kyc-me-submit"),
    path("kyc/me/upload/<str:kind>/", MyKycUploadView.as_view(), name="kyc-me-upload"),
    path("hospitals/", HospitalCatalogView.as_view(), name="hospital-catalog"),
    path("contracts/", ContractsView.as_view(), name="api-contracts"),
    path("", include(router.urls)),
]
