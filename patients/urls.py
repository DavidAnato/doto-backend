from django.urls import path
from rest_framework.routers import DefaultRouter

from .access_views import (
    AccessRequestApproveView,
    AccessRequestCancelView,
    AccessRequestCreateView,
    AccessRequestDenyView,
    AccessRequestDetailView,
    AccessRequestListView,
    AccessRequestRevokeView,
)
from .appointment_views import AppointmentViewSet
from .block_views import AccessBlockLiftView, AccessBlockListCreateView, AdminForceRevokeView
from .views import MonDossierView, PatientViewSet

router = DefaultRouter()
router.register("patients", PatientViewSet, basename="patient")
router.register("appointments", AppointmentViewSet, basename="appointment")

mon = MonDossierView.as_view(
    {
        "get": "list",
        "patch": "partial_update",
    }
)

urlpatterns = [
    path("patients/me/", mon, name="mon-dossier"),
    path(
        "patients/me/historique/",
        MonDossierView.as_view({"get": "historique"}),
        name="mon-historique",
    ),
    path(
        "patients/me/assurance/",
        MonDossierView.as_view(
            {
                "get": "mon_assurance",
                "put": "mon_assurance",
                "patch": "mon_assurance",
                "delete": "mon_assurance",
            }
        ),
        name="mon-assurance",
    ),
    path("access-requests/", AccessRequestListView.as_view(), name="access-request-list"),
    path("access-requests/create/", AccessRequestCreateView.as_view(), name="access-request-create"),
    path("access-requests/<int:pk>/", AccessRequestDetailView.as_view(), name="access-request-detail"),
    path(
        "access-requests/<int:pk>/approve/",
        AccessRequestApproveView.as_view(),
        name="access-request-approve",
    ),
    path(
        "access-requests/<int:pk>/deny/",
        AccessRequestDenyView.as_view(),
        name="access-request-deny",
    ),
    path(
        "access-requests/<int:pk>/cancel/",
        AccessRequestCancelView.as_view(),
        name="access-request-cancel",
    ),
    path(
        "access-requests/<int:pk>/revoke/",
        AccessRequestRevokeView.as_view(),
        name="access-request-revoke",
    ),
    path(
        "access-requests/<int:pk>/force-revoke/",
        AdminForceRevokeView.as_view(),
        name="access-request-force-revoke",
    ),
    path("access-blocks/", AccessBlockListCreateView.as_view(), name="access-block-list"),
    path("access-blocks/<int:pk>/lift/", AccessBlockLiftView.as_view(), name="access-block-lift"),
]

urlpatterns += router.urls
