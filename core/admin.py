from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
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


@admin.register(models.PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "status", "user", "plan", "amount", "method", "transaction_id", "phone", "name")
    list_filter = ("status", "method", "plan")
    search_fields = ("user__email", "email", "name", "phone", "transaction_id", "sender_number")
    date_hierarchy = "created_at"
    actions = ["approve", "reject"]
    readonly_fields = ("user", "plan", "amount", "method", "name", "email", "phone", "sender_number", "transaction_id", "note",
                       "proof", "created_at", "reviewed_at", "reviewed_by")
    fields = ("status", "admin_note", "proof", "user", "plan", "amount", "method", "name", "email", "phone", "sender_number",
              "transaction_id", "note", "created_at", "reviewed_at", "reviewed_by")

    @admin.display(description="Screenshot")
    def proof(self, obj):
        if not obj.pk:
            return "-"
        url = reverse("payment_screenshot", args=[obj.pk])
        return format_html('<a href="{0}" target="_blank"><img src="{0}" style="max-width:420px;max-height:560px;border:1px solid #ccc;border-radius:8px"></a>', url)

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).defer("screenshot")

    def save_model(self, request, obj, form, change):
        """Changing the status in the form approves/rejects through the same path as the actions, so a plan is activated once."""
        new_status = obj.status
        if change and form.initial.get("status") != "pending":
            obj.status = form.initial.get("status", obj.status)  # a reviewed payment is final
            return super().save_model(request, obj, form, change)
        if change and "status" in form.changed_data and new_status in ("approved", "rejected"):
            obj.status = "pending"  # let review() make the transition (and activate the plan)
            note = obj.admin_note
            obj.save(update_fields=["status"])
            obj.review(new_status, request.user, note)
            if note:
                obj.admin_note = note
                obj.save(update_fields=["admin_note"])
            return
        super().save_model(request, obj, form, change)

    @admin.action(description="Approve selected payments (activates the plan)")
    def approve(self, request, queryset):
        n = sum(p.review("approved", request.user) for p in queryset.select_related("user"))
        self.message_user(request, f"{n} payment(s) approved and plan(s) activated.", messages.SUCCESS)

    @admin.action(description="Reject selected payments")
    def reject(self, request, queryset):
        n = sum(p.review("rejected", request.user, "We could not verify this payment. Please contact us or submit it again.") for p in queryset)
        self.message_user(request, f"{n} payment(s) rejected.", messages.WARNING)
