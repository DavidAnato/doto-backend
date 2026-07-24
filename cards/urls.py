from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DashboardView, DodoCardViewSet, HubDashboardView, HubEventsView, ScanView

router = DefaultRouter()
router.register("dodocards", DodoCardViewSet, basename="dodocard")

# Actions patient imbriquées (garantit le matching avant {pk})
dodocard_mine_pdf = DodoCardViewSet.as_view({"get": "mine_pdf"})
dodocard_report_loss = DodoCardViewSet.as_view({"post": "report_loss"})
dodocard_request_reissue = DodoCardViewSet.as_view({"post": "request_reissue"})

urlpatterns = [
    path("dodocards/scan/", ScanView.as_view(), name="dodocard-scan"),
    path("dodocards/mine/pdf/", dodocard_mine_pdf, name="dodocard-mine-pdf"),
    path("dodocards/mine/report-loss/", dodocard_report_loss, name="dodocard-report-loss"),
    path("dodocards/mine/reissue/", dodocard_request_reissue, name="dodocard-request-reissue"),
    path("admin/dashboard/", DashboardView.as_view(), name="dashboard"),
    path("hub/dashboard/", HubDashboardView.as_view(), name="hub-dashboard"),
    path("hub/events/", HubEventsView.as_view(), name="hub-events"),
]
urlpatterns += router.urls
