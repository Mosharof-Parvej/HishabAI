import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai
from .models import AICall, Account, Budget, Category, Debt, DebtPayment, Transaction, User


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
        self.assertEqual(u.trial_days_left, 15)
        self.assertEqual(list(u.accounts.values_list('name', flat=True)), ['Cash', 'bKash', 'Nagad', 'Rocket', 'Bank', 'Card'])
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
        for name in ["dashboard", "smart_entry", "transactions", "transaction_add", "budgets", "ask", "accounts", "debts", "subscription", "settings"]:
            self.assertEqual(self.c.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.c.get(reverse("budgets") + "?view=yearly&month=2026-03").status_code, 200)

    def test_save_validates_and_scopes_to_user(self):
        today = timezone.localdate().isoformat()
        good = {"type": "expense", "amount": "250", "category": "Food", "date": today, "note": "Lunch", "ai": True, "confidence": 98}
        r = self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": [good]}), content_type="application/json")
        self.assertEqual(r.json(), {"saved": 1, "debts": 0})
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

    @patch("core.ai.ask")
    def test_extract_and_save_debts_from_text(self, ask):
        today = timezone.localdate()
        ask.return_value = json.dumps({"transactions": [
            {"type": "lent", "amount": 500, "person": "Rahim", "due_date": (today + timedelta(days=5)).isoformat(), "date": today.isoformat(), "category": "Food", "confidence": 90},
            {"type": "borrowed", "amount": 2000, "person": "Karim", "account": "Cash", "date": today.isoformat(), "due_date": "garbage"},
            {"type": "lent", "amount": 100, "date": today.isoformat()},  # no person -> dropped
            {"type": "expense", "amount": 50, "category": "Food", "date": today.isoformat(), "note": "Tea"}]})
        rows = self.c.post(reverse("api_extract"), json.dumps({"text": "x"}), content_type="application/json").json()["transactions"]
        self.assertEqual([r["type"] for r in rows], ["lent", "borrowed", "expense"])
        self.assertEqual((rows[0]["category"], rows[0]["note"], rows[1]["due_date"]), (None, "Rahim", ""))
        r = self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": rows}), content_type="application/json")
        self.assertEqual(r.json(), {"saved": 3, "debts": 2})
        self.assertEqual(sorted(Debt.objects.values_list("person", "direction")), [("Karim", "borrowed"), ("Rahim", "lent")])
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(Debt.objects.get(person="Karim").account.name, "Cash")
        bad = [{"type": "lent", "amount": "10", "date": today.isoformat(), "note": ""}]  # person missing
        self.assertEqual(self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": bad}), content_type="application/json").status_code, 400)

    @patch("core.ai.ask", side_effect=ai.AIUnavailable("down"))
    def test_ai_failure_is_json_503(self, _):
        r = self.c.post(reverse("api_extract"), json.dumps({"text": "x"}), content_type="application/json")
        self.assertEqual(r.status_code, 503)

    def test_expired_trial_blocks_ai(self):
        self.user.trial_ends_at = timezone.now() - timedelta(days=1)
        self.user.save()
        r = self.c.post(reverse("api_ask"), json.dumps({"question": "hi"}), content_type="application/json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(self.c.get(reverse("dashboard")).status_code, 200)  # data stays viewable

    @patch("core.ai.ask", return_value="Spending dipped 5% this month.")
    def test_insight_is_cached_per_day(self, ask):
        Transaction.objects.create(user=self.user, kind="expense", amount=250, category=Category.objects.get(user=self.user, name="Food"))
        self.c.get(reverse("api_insight")); self.c.get(reverse("api_insight"))
        self.assertEqual(ask.call_count, 1)

    @patch("core.ai.ask")
    def test_ask_runs_filter_against_database(self, ask):
        food = Category.objects.get(user=self.user, name="Food")
        bk = Account.objects.get(user=self.user, name="bKash")
        today = timezone.localdate()
        Transaction.objects.create(user=self.user, kind="expense", amount=250, category=food, account=bk, date=today, note="Lunch")
        Transaction.objects.create(user=self.user, kind="expense", amount=100, category=Category.objects.get(user=self.user, name="Transport"), date=today)
        Transaction.objects.create(user=self.user, kind="income", amount=900, category=Category.objects.get(user=self.user, name="Salary"), date=today)
        other = User.objects.create_user("o@example.com", "s3cret-pass-9")
        Transaction.objects.create(user=other, kind="expense", amount=777, category=Category.objects.get(user=other, name="Food"), date=today)
        # AI may hallucinate names; unknown categories/accounts must be ignored, never trusted
        ask.return_value = json.dumps({"intent": "transactions", "title": "Food spend", "kind": "expense", "categories": ["Food", "Made Up"],
                                       "accounts": ["bKash", "Nope"], "sort": "date", "limit": 500})
        d = self.c.post(reverse("api_ask"), json.dumps({"question": "food?"}), content_type="application/json").json()
        self.assertEqual(d["summary"]["expense"], 250.0)
        self.assertEqual(d["summary"]["count"], 1)
        self.assertEqual([r["note"] for r in d["rows"]], ["Lunch"])
        ask.return_value = json.dumps({"intent": "transactions", "title": "All"})
        d = self.c.post(reverse("api_ask"), json.dumps({"question": "all"}), content_type="application/json").json()
        self.assertEqual((d["summary"]["income"], d["summary"]["expense"], d["summary"]["balance"]), (900.0, 350.0, 550.0))

    @patch("core.ai.ask", return_value="{\"transactions\": []}")
    def test_ai_usage_meter_and_header(self, _):
        for _i in range(3):
            AICall.objects.create(user=self.user)
        page = self.c.get(reverse("smart_entry"))
        self.assertEqual((page.context["ai_usage"]["used"], page.context["ai_usage"]["limit"]), (3, 60))
        self.assertContains(page, "AI today:")
        r = self.c.post(reverse("api_extract"), json.dumps({"text": "x"}), content_type="application/json")
        self.assertEqual(r["X-AI-Used"], "3")   # ask() is mocked so no extra call was counted; header reflects real usage
        other = User.objects.create_user("o@example.com", "s3cret-pass-9")
        AICall.objects.create(user=other)
        from . import services
        self.assertEqual(services.ai_usage(self.user)["used"], 3)   # per user

    @patch("core.ai._groq_stt", return_value="  আজ ২৫০ টাকা  ")
    def test_transcribe_endpoint(self, stt):
        from django.core.files.uploadedfile import SimpleUploadedFile
        up = lambda n=2000, t="audio/webm": SimpleUploadedFile("voice", b"x" * n, content_type=t)
        with override_settings(GROQ_API_KEY="k"):
            r = self.c.post(reverse("api_transcribe"), {"audio": up()})
            self.assertEqual(r.json(), {"text": "আজ ২৫০ টাকা"})
            self.assertEqual(AICall.objects.filter(user=self.user).count(), 1)
            self.assertEqual(self.c.post(reverse("api_transcribe"), {"audio": up(100)}).status_code, 400)          # too small
            self.assertEqual(self.c.post(reverse("api_transcribe"), {"audio": up(t="text/plain")}).status_code, 400)  # not audio
            self.assertEqual(self.c.post(reverse("api_transcribe"), {"audio": up(5_000_000)}).status_code, 400)    # too big
            self.assertEqual(Client().post(reverse("api_transcribe"), {"audio": up()}).status_code, 401)           # logged out
        with override_settings(GROQ_API_KEY="k", GEMINI_API_KEY="g"), patch("core.ai._groq_stt", side_effect=ai.requests.ConnectionError()), \
                patch("core.ai._gemini_stt", return_value="fallback ok"):
            self.assertEqual(self.c.post(reverse("api_transcribe"), {"audio": up()}).json(), {"text": "fallback ok"})

    def test_manual_add_validates_and_saves(self):
        food = Category.objects.get(user=self.user, name="Food")
        cash = Account.objects.get(user=self.user, name="Cash")
        good = {"kind": "expense", "amount": "120", "category": food.id, "account": cash.id, "date": timezone.localdate().isoformat(), "note": "Tea"}
        self.assertRedirects(self.c.post(reverse("transaction_add"), good), reverse("transactions"))
        t = Transaction.objects.get()
        self.assertEqual((t.amount, t.account, t.ai_generated), (120, cash, False))
        for bad in [{**good, "amount": "0"}, {**good, "kind": "income"}, {**good, "date": "2999-01-01"}]:  # income + Food category mismatch
            self.assertContains(self.c.post(reverse("transaction_add"), bad), "Please enter a valid")
        self.assertEqual(Transaction.objects.count(), 1)

    def test_accounts_add_validates_against_catalog(self):
        r = self.c.post(reverse("accounts_add"), {"kind": "wallet", "provider": "bkash", "name": "bKash 2", "opening_balance": "500"})
        self.assertRedirects(r, reverse("accounts"))
        a = Account.objects.get(name="bKash 2")
        self.assertEqual((a.provider, a.opening_balance, a.kind), ("bkash", 500, "wallet"))
        for bad in [{"kind": "bank", "provider": "bkash"}, {"kind": "wallet", "provider": "nope"}, {"kind": "wallet", "provider": "bkash", "name": "bKash"}]:  # wrong kind / unknown / duplicate name
            self.c.post(reverse("accounts_add"), bad)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 7)  # 6 defaults + "bKash 2"
        self.assertContains(self.c.get(reverse("accounts")), "core/logos/bkash.png")

    def test_edit_default_account_sets_balance_name_and_logo(self):
        from . import services
        bank = Account.objects.get(user=self.user, name="Bank")
        Transaction.objects.create(user=self.user, kind="income", amount=300, account=bank)
        self.c.post(reverse("accounts_edit", args=[bank.pk]), {"name": "BRAC Salary", "balance": "1000", "provider": "brac"})
        bank.refresh_from_db()
        self.assertEqual((bank.name, bank.provider), ("BRAC Salary", "brac"))
        self.assertEqual(dict(services.account_balances(self.user))[bank], 1000)   # typed balance is the CURRENT balance
        self.assertEqual(bank.opening_balance, 700)
        self.c.post(reverse("accounts_edit", args=[bank.pk]), {"name": "Cash", "balance": "1000", "provider": "brac"})  # duplicate name
        self.c.post(reverse("accounts_edit", args=[bank.pk]), {"name": "X", "balance": "1000", "provider": "bkash"})   # wallet logo on a bank
        bank.refresh_from_db()
        self.assertEqual((bank.name, bank.provider), ("BRAC Salary", "brac"))
        other = User.objects.create_user("o@example.com", "s3cret-pass-9")
        self.c.post(reverse("accounts_edit", args=[Account.objects.get(user=other, name="Cash").pk]), {"name": "Hacked"})
        self.assertFalse(Account.objects.filter(name="Hacked").exists())
        self.assertEqual(Account.objects.get(user=self.user, name="Card").logo, "core/logos/card.svg")   # generic icon

    def test_account_balance_includes_transactions_and_debts(self):
        from . import services
        cash = Account.objects.get(user=self.user, name="Cash")
        cash.opening_balance = 1000; cash.save()
        Transaction.objects.create(user=self.user, kind="income", amount=500, account=cash)
        Transaction.objects.create(user=self.user, kind="expense", amount=200, account=cash)
        borrowed = Debt.objects.create(user=self.user, direction="borrowed", person="A", amount=300, account=cash)
        lent = Debt.objects.create(user=self.user, direction="lent", person="B", amount=100, account=cash)
        DebtPayment.objects.create(debt=borrowed, amount=50, account=cash)   # I repay: -50
        DebtPayment.objects.create(debt=lent, amount=40, account=cash)       # they repay me: +40
        # 1000 +500 -200 +300(borrowed in) -100(lent out) -50 +40
        self.assertEqual(dict(services.account_balances(self.user))[cash], 1490)

    def test_debt_reminders_and_repayment(self):
        today = timezone.localdate()
        late = Debt.objects.create(user=self.user, direction="borrowed", person="Rahim", amount=500, due_date=today - timedelta(days=2))
        Debt.objects.create(user=self.user, direction="lent", person="Karim", amount=200, due_date=today + timedelta(days=2))
        Debt.objects.create(user=self.user, direction="lent", person="Far", amount=200, due_date=today + timedelta(days=30))
        page = self.c.get(reverse("dashboard")).content.decode()
        self.assertIn("Rahim", page); self.assertIn("Karim", page); self.assertNotIn("Far", page.split("Repayment reminders")[1].split("</details>")[0])
        self.c.post(reverse("debt_pay", args=[late.pk]), {"amount": "999"})   # more than remaining: rejected
        self.assertEqual(late.payments.count(), 0)
        self.c.post(reverse("debt_pay", args=[late.pk]), {"amount": "500"})
        self.assertEqual(late.payments.count(), 1)
        from . import services
        self.assertEqual([r["debt"].person for r in services.reminders(self.user, today)], ["Karim"])  # settled debt no longer reminds

    def test_reminder_option_per_debt(self):
        from . import services
        today = timezone.localdate()
        due = (today + timedelta(days=5)).isoformat()
        base = {"direction": "lent", "person": "Sumi", "amount": "100", "due_date": due}
        self.c.post(reverse("debt_add"), {**base, "remind_days": "7"})
        self.c.post(reverse("debt_add"), {**base, "person": "Quiet", "remind_days": ""})
        self.c.post(reverse("debt_add"), {**base, "person": "Later", "remind_days": "1"})
        self.c.post(reverse("debt_add"), {**base, "person": "Bad", "remind_days": "99", "due_date": ""})
        self.assertEqual(sorted(Debt.objects.values_list("person", "remind_days")), [("Bad", None), ("Later", 1), ("Quiet", None), ("Sumi", 7)])
        self.assertEqual([r["debt"].person for r in services.reminders(self.user, today)], ["Sumi"])   # only the 7-day one has started
        later = Debt.objects.get(person="Later")
        self.c.post(reverse("debt_remind", args=[later.pk]), {"remind_days": "7"})
        self.assertEqual(sorted(r["debt"].person for r in services.reminders(self.user, today)), ["Later", "Sumi"])
        self.c.post(reverse("debt_remind", args=[later.pk]), {"remind_days": ""})
        self.assertEqual([r["debt"].person for r in services.reminders(self.user, today)], ["Sumi"])

    def test_debts_are_private(self):
        other = User.objects.create_user("o@example.com", "s3cret-pass-9")
        d = Debt.objects.create(user=other, direction="lent", person="X", amount=10)
        self.c.post(reverse("debt_delete", args=[d.pk]))
        self.c.post(reverse("debt_pay", args=[d.pk]), {"amount": "5"})
        self.assertEqual((Debt.objects.count(), DebtPayment.objects.count()), (1, 0))

    def test_catalog_logos_exist_and_cover_bd_providers(self):
        from . import catalog
        for key in ["bkash", "nagad", "brac", "city", "dbbl", "ebl", "ibbl", "sonali", "visa", "mastercard", "cash"]:
            self.assertTrue(catalog.has_logo(key), key)
        self.assertGreaterEqual(len(catalog.for_kind("bank")), 55)
        self.assertGreaterEqual(len(catalog.for_kind("wallet")), 10)

    @override_settings(AI_DAILY_LIMIT=1)
    def test_daily_cap(self):
        AICall.objects.create(user=self.user)
        with override_settings(GEMINI_API_KEY="k"):
            with self.assertRaises(ai.AIQuotaExceeded):
                ai.ask(self.user, "s", "p")

    @override_settings(GEMINI_API_KEY="g", GROQ_API_KEY="x", AI_PRIMARY="gemini")
    def test_fallback_to_second_provider(self):
        with patch("core.ai._gemini", side_effect=ai.requests.ConnectionError()), patch("core.ai._groq", return_value="ok"):
            self.assertEqual(ai.ask(self.user, "s", "p"), "ok")

    def test_budgets_status_and_save(self):
        food = Category.objects.get(user=self.user, name="Food")
        Transaction.objects.create(user=self.user, kind="expense", amount=900, category=food)
        self.c.post(reverse("budgets_save"), {f"limit_{food.id}": "1000"})
        self.assertEqual(Budget.objects.get().limit, 1000)
        self.assertContains(self.c.get(reverse("budgets")), "90%")

    def test_transactions_date_range_filter(self):
        today = timezone.localdate()
        for days, amt in [(0, 10), (5, 20), (20, 40)]:
            Transaction.objects.create(user=self.user, kind="expense", amount=amt, date=today - timedelta(days=days), note=f"n{amt}")
        url = reverse("transactions")
        both = lambda a, b: self.c.get(url, {"from": (today - timedelta(days=a)).isoformat(), "to": (today - timedelta(days=b)).isoformat()})
        r = both(6, 0)
        self.assertEqual((r.context["sums"]["count"], r.context["sums"]["expense"]), (2, 30))
        self.assertEqual(both(0, 6).context["sums"]["count"], 2)                        # reversed range is swapped
        self.assertEqual(self.c.get(url, {"from": "garbage"}).context["sums"]["count"], 3)  # bad date ignored
        csv_body = self.c.get(reverse("transactions_csv"), {"from": (today - timedelta(days=6)).isoformat()}).content.decode()
        self.assertIn("n10", csv_body); self.assertNotIn("n40", csv_body)               # export honours the range

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
