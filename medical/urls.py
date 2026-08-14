from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BonExamenViewSet,
    ConstanteVitaleViewSet,
    ConsultationViewSet,
    ExamCatalogView,
    ExamenViewSet,
    OrdonnanceViewSet,
)

router = DefaultRouter()
router.register("consultations", ConsultationViewSet, basename="consultation")
router.register("ordonnances", OrdonnanceViewSet, basename="ordonnance")
router.register("examens", ExamenViewSet, basename="examen")
router.register("exam-orders", BonExamenViewSet, basename="exam-order")
router.register("constantes", ConstanteVitaleViewSet, basename="constante")

urlpatterns = [
    path("exam-catalog/", ExamCatalogView.as_view(), name="exam-catalog"),
]
urlpatterns += router.urls
