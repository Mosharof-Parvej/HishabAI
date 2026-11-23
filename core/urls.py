from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.dashboard, name="dashboard"),
    path("login/", v.login_view, name="login"),
    path("signup/", v.signup_view, name="signup"),
    path("logout/", v.logout_view, name="logout"),
    path("auth/google/", v.google_start, name="google_start"),
    path("auth/google/callback/", v.google_callback, name="google_callback"),
    path("smart-entry/", v.smart_entry, name="smart_entry"),
    path("transactions/", v.transactions, name="transactions"),
    path("transactions/export.csv", v.transactions_csv, name="transactions_csv"),
    path("transactions/<int:pk>/delete/", v.transaction_delete, name="transaction_delete"),
    path("budgets/", v.budgets, name="budgets"),
    path("budgets/save/", v.budgets_save, name="budgets_save"),
    path("assistant/", v.assistant, name="assistant"),
    path("subscription/", v.subscription, name="subscription"),
    path("subscription/choose/", v.subscription_choose, name="subscription_choose"),
    path("settings/", v.settings_view, name="settings"),
    path("api/insight/", v.api_insight, name="api_insight"),
    path("api/extract/", v.api_extract, name="api_extract"),
    path("api/transactions/", v.api_save_transactions, name="api_save_transactions"),
    path("api/budget-suggestions/", v.api_budget_suggestions, name="api_budget_suggestions"),
    path("api/chat/", v.api_chat, name="api_chat"),
]
