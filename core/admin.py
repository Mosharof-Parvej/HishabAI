from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import models


@admin.register(models.User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "name", "language", "plan", "trial_ends_at", "paid_until", "is_staff", "date_joined")
    list_filter = ("plan", "language", "is_staff", "is_active")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name", "language")}),
        ("Plan", {"fields": ("plan", "trial_ends_at", "paid_until")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )

    def has_add_permission(self, request):  # people sign up on the site; staff are promoted with is_staff
        return False


@admin.register(models.Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "kind", "amount", "category", "account", "note", "ai_generated")
    list_filter = ("kind", "ai_generated", "date")
    search_fields = ("note", "user__email")
    date_hierarchy = "date"
    raw_id_fields = ("user", "category", "account")


@admin.register(models.Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "kind", "provider", "opening_balance")
    list_filter = ("kind",)
    search_fields = ("name", "user__email")


@admin.register(models.Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ("person", "user", "direction", "amount", "date", "due_date", "remind_days")
    list_filter = ("direction",)
    search_fields = ("person", "user__email")


@admin.register(models.AICall)
class AICallAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at")
    date_hierarchy = "created_at"
    search_fields = ("user__email",)


admin.site.register(models.Category)
admin.site.register(models.Budget)
admin.site.register(models.DebtPayment)
