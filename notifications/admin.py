from django.contrib import admin

from .models import DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "title", "read_at", "created_at")
    list_filter = ("type",)
    search_fields = ("title", "body", "user__username")
    readonly_fields = ("created_at",)


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "platform", "app", "enabled", "updated_at")
    list_filter = ("platform", "enabled", "app")
