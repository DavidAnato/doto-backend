"""Admin Django — chargé via include() après que config.urls soit importé."""
from django.contrib import admin

admin.site.site_header = "DOTO+ Admin"
admin.site.site_title = "DOTO+"
admin.site.index_title = "Administration de la plateforme"

urlpatterns = admin.site.get_urls()
