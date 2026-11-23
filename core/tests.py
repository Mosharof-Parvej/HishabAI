import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai
from .models import AICall, Budget, Category, Transaction, User


class AppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("a@example.com", "s3cret-pass-9", name="Nusrat J")
        self.c = Client()
        self.c.force_login(self.user)

    def test_signup_login_flow(self):
        c = Client()
        r = c.post(reverse("signup"), {"email": "New@Example.com", "password": "very-long-pass-1", "name": "N"})
        self.assertRedirects(r, reverse("dashboard"))
        u = User.objects.get(email="new@example.com")
        self.assertEqual(u.categories.count(), 12)
        self.assertEqual(u.trial_days_left, 30)
        c.post(reverse("logout"))
        self.assertEqual(c.post(reverse("login"), {"email": "new@example.com", "password": "bad"}).status_code, 200)
        self.assertRedirects(c.post(reverse("login"), {"email": "new@example.com", "password": "very-long-pass-1"}), reverse("dashboard"))

    def test_weak_password_and_duplicate_rejected(self):
        r = Client().post(reverse("signup"), {"email": "x@example.com", "password": "123"})
        self.assertContains(r, "too short")
        r = Client().post(reverse("signup"), {"email": "a@example.com", "password": "very-long-pass-1"})
        self.assertContains(r, "already exists")

    def test_pages_require_login_and_render(self):
        self.assertEqual(Client().get(reverse("dashboard")).status_code, 302)
        for name in ["dashboard", "smart_entry", "transactions", "budgets", "assistant", "subscription", "settings"]:
            self.assertEqual(self.c.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.c.get(reverse("budgets") + "?view=yearly&month=2026-03").status_code, 200)

    def test_save_validates_and_scopes_to_user(self):
        today = timezone.localdate().isoformat()
        good = {"type": "expense", "amount": "250", "category": "Food", "date": today, "note": "Lunch", "ai": True, "confidence": 98}
        r = self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": [good]}), content_type="application/json")
        self.assertEqual(r.json(), {"saved": 1})
        for bad in [{**good, "amount": "-5"}, {**good, "category": "Salary"}, {**good, "date": "2999-01-01"}, {**good, "type": "x"}]:
            r = self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": [bad]}), content_type="application/json")
            self.assertEqual(r.status_code, 400)
        self.assertEqual(Transaction.objects.count(), 1)
        other = User.objects.create_user("b@example.com", "s3cret-pass-9")
        self.assertEqual(Transaction.objects.filter(user=other).count(), 0)
        t = Transaction.objects.get()
        Client().force_login(other)
        oc = Client(); oc.force_login(other)
        oc.post(reverse("transaction_delete", args=[t.pk]))
        self.assertEqual(Transaction.objects.count(), 1)  # cannot delete someone else's

    @patch("core.ai.ask")
    def test_extract_normalises_ai_output(self, ask):
        ask.return_value = json.dumps({"transactions": [
            {"type": "expense", "amount": 250, "category": "Food", "date": "2020-01-02", "note": "Lunch", "confidence": 98},
            {"type": "income", "amount": "5000", "category": "Nonsense", "date": "bad", "note": "Tuition", "confidence": 500},
            {"type": "expense", "amount": -1, "category": "Food"}]})
        r = self.c.post(reverse("api_extract"), json.dumps({"text": "x"}), content_type="application/json")
        rows = r.json()["transactions"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["category"], "Other Income")
        self.assertEqual(rows[1]["confidence"], 100)
        self.assertEqual(rows[1]["date"], timezone.localdate().isoformat())

    @patch("core.ai.ask", side_effect=ai.AIUnavailable("down"))
    def test_ai_failure_is_json_503(self, _):
        r = self.c.post(reverse("api_extract"), json.dumps({"text": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 503)

    def test_expired_trial_blocks_ai(self):
        self.user.trial_ends_at = timezone.now() - timedelta(days=1)
        self.user.save()
        r = self.c.post(reverse("api_chat"), json.dumps({"message": "hi"}), content_type="application/json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(self.c.get(reverse("dashboard")).status_code, 200)  # data stays viewable

    @patch("core.ai.ask", return_value="You spent ৳250 on food.")
    def test_chat_and_insight_cache(self, ask):
        Transaction.objects.create(user=self.user, kind="expense", amount=250, category=Category.objects.get(user=self.user, name="Food"))
        r = self.c.post(reverse("api_chat"), json.dumps({"message": "food?"}), content_type="application/json")
        self.assertIn("250", r.json()["answer"])
        self.c.get(reverse("api_insight")); self.c.get(reverse("api_insight"))
        self.assertEqual(ask.call_count, 2)  # chat + ONE insight (second was cached)
        self.assertContains(self.c.get(reverse("assistant")), "food?")

    @override_settings(AI_DAILY_LIMIT=1)
    def test_daily_cap(self):
        AICall.objects.create(user=self.user)
        with override_settings(GEMINI_API_KEY="k"):
            with self.assertRaises(ai.AIQuotaExceeded):
                ai.ask(self.user, "s", "p")

    @override_settings(GEMINI_API_KEY="g", GROK_API_KEY="x", AI_PRIMARY="gemini")
    def test_fallback_to_second_provider(self):
        with patch("core.ai._gemini", side_effect=ai.requests.ConnectionError()), patch("core.ai._grok", return_value="ok"):
            self.assertEqual(ai.ask(self.user, "s", "p"), "ok")

    def test_budgets_status_and_save(self):
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, kind="expense", amount=900, category=food)
        self.c.post(reverse("budgets_save"), {f"limit_{food.id}": "1000"})
        self.assertEqual(Budget.objects.get().limit, 1000)
        self.assertContains(self.c.get(reverse("budgets")), "90%")

    def test_csv_export_neutralises_formulas(self):
        Transaction.objects.create(user=self.user, kind="expense", amount=5, note="=HYPERLINK(1)")
        body = self.c.get(reverse("transactions_csv")).content.decode()
        self.assertIn("'=HYPERLINK", body)

    def test_subscription_demo(self):
        self.c.post(reverse("subscription_choose"), {"plan": "yearly"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_paid)
        self.assertEqual(self.user.plan, "yearly")

    def test_taka_filter_uses_bd_grouping(self):
        from .templatetags.ui import taka
        self.assertEqual(taka(100000), "৳1,00,000")
        self.assertEqual(taka(28350), "৳28,350")
        self.assertEqual(taka(-540), "-৳540")
