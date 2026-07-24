from rest_framework.routers import DefaultRouter

from .views import (
    ConstanteVitaleViewSet,
    ConsultationViewSet,
    ExamenViewSet,
    OrdonnanceViewSet,
)

router = DefaultRouter()
router.register("consultations", ConsultationViewSet, basename="consultation")
router.register("ordonnances", OrdonnanceViewSet, basename="ordonnance")
router.register("examens", ExamenViewSet, basename="examen")
router.register("constantes", ConstanteVitaleViewSet, basename="constante")

urlpatterns = router.urls
