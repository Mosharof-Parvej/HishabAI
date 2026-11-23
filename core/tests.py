import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import ai
from .models import AICall, Account, Budget, Category, Debt, DebtPayment, PaymentRequest, Transaction, User


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
        self.assertEqual(u.categories.count(), 16)
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

    def test_dashboard_month_navigation_and_recent_ranges(self):
        from . import services
        today = timezone.localdate()
        food = Category.objects.get(user=self.user, name="Food")
        prev = services.shift_month(today.replace(day=1), -1)
        Transaction.objects.create(user=self.user, kind="expense", amount=700, category=food, date=prev.replace(day=3), note="OldMonth")
        Transaction.objects.create(user=self.user, kind="income", amount=1000, date=today, note="TodayIn", category=Category.objects.get(user=self.user, name="Salary"))
        r = self.c.get(reverse("dashboard"))
        self.assertEqual((r.context["cur"]["income"], r.context["cur"]["expense"]), (1000, 0))
        self.assertTrue(r.context["is_current"])
        self.assertEqual(r.context["all_time"], 300)   # 1000 income - 700 expense across months
        r = self.c.get(reverse("dashboard"), {"month": prev.strftime("%Y-%m")})
        self.assertEqual(r.context["cur"]["expense"], 700)
        self.assertFalse(r.context["is_current"])
        self.assertEqual(self.c.get(reverse("dashboard"), {"month": "2999-01"}).context["month"], today.replace(day=1))  # no future months
        for rng, expect in [("day", ["TodayIn"]), ("week", ["TodayIn"]), ("month", ["TodayIn"])]:
            self.assertEqual([t.note for t in self.c.get(reverse("dashboard"), {"range": rng}).context["recent"]], expect, rng)
        week = self.c.get(reverse("dashboard")).context["range_start"]
        self.assertEqual(week.weekday(), 5)            # weeks start on Saturday
        self.assertContains(self.c.get(reverse("dashboard")), "Good ")

    def test_categories_have_emoji_and_can_be_added(self):
        self.assertEqual(Category.objects.get(user=self.user, name="Food").label, "🍚 Food")
        self.c.post(reverse("categories_add"), {"name": "Pets", "emoji": "🐶", "kind": "expense"})
        self.c.post(reverse("categories_add"), {"name": "pets", "emoji": "x", "kind": "expense"})   # duplicate (case-insensitive)
        self.c.post(reverse("categories_add"), {"name": "", "kind": "expense"})
        self.assertEqual(Category.objects.filter(user=self.user, name__iexact="pets").count(), 1)
        self.assertEqual(Category.objects.get(user=self.user, name="Pets").emoji, "🐶")
        # AI fallback is the real "Others", not whichever category happens to be last
        names = {"expense": [c.name for c in Category.objects.filter(user=self.user, kind="expense")], "income": ["Salary", "Other Income"]}
        with patch("core.ai.ask", return_value=json.dumps({"transactions": [{"type": "expense", "amount": 5, "category": "Nonsense", "date": timezone.localdate().isoformat()}]})):
            self.assertEqual(ai.extract_transactions(self.user, "x", names)[0]["category"], "Others")

    def test_language_switch_profile_menu_and_blank_about(self):
        self.assertEqual(self.user.language, "en")                                   # English by default
        page = self.c.get(reverse("dashboard"))
        for label in ["Profile", "Language", "About &amp; contact", "Help", "Theme", "Light", "Dark", "System", "Dashboard"]:
            self.assertContains(page, label)
        self.assertContains(page, "বাংলা")                                              # language options are shown
        self.c.post(reverse("set_language"), {"language": "bn", "next": reverse("transactions")})
        self.user.refresh_from_db()
        self.assertEqual(self.user.language, "bn")
        page = self.c.get(reverse("dashboard"))
        for label in ["ড্যাশবোর্ড", "লেনদেন", "প্রোফাইল", "ভাষা", "আমাদের সম্পর্কে ও যোগাযোগ", "থিম", "আজ AI:"]:
            self.assertContains(page, label)
        self.assertNotContains(page, ">Transactions<")
        r = self.c.post(reverse("set_language"), {"language": "bn", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)  # open redirect refused
        self.c.post(reverse("set_language"), {"language": "xx"})                      # unknown language ignored
        self.user.refresh_from_db(); self.assertEqual(self.user.language, "bn")
        self.c.post(reverse("set_language"), {"language": "en"})
        self.assertContains(self.c.get(reverse("dashboard")), "Dashboard")
        about = self.c.get(reverse("about")).content.decode()
        for name in ["Md. Parvej Mosharof", "Md. Naimur Rahman", "Md. Hridoy Ahmed Mahim", "Daffodil International University (DIU)"]:
            self.assertIn(name, about)
        self.assertEqual(Client().get(reverse("about")).status_code, 200)               # public page
        self.assertEqual(self.c.get(reverse("settings")).status_code, 200)
        self.assertEqual(reverse("settings"), "/profile/")
        self.assertEqual(Client().post(reverse("set_language"), {"language": "bn"}).status_code, 302)  # needs login

    def test_profile_menu_admin_theme_and_android_absent(self):
        page = self.c.get(reverse("dashboard")).content.decode()
        self.assertNotIn("Android", page)                                              # no Android app entry
        self.assertNotIn('href="/admin/"', page)                                      # admin link is staff-only
        self.assertIn('data-theme-set="system"', page)
        self.user.is_staff = True; self.user.is_superuser = True; self.user.save()
        self.assertIn('href="/admin/"', self.c.get(reverse("dashboard")).content.decode())
        self.assertEqual(self.c.get(reverse("admin_overview")).status_code, 200)       # the panel opens for staff
        self.assertEqual(self.c.get("/manage/").status_code, 404)                       # Django's database admin is gone
        other = Client(); other.force_login(User.objects.create_user("n@example.com", "s3cret-pass-9"))
        self.assertEqual(other.get("/admin/").status_code, 404)                         # the panel does not exist for normal users

    def test_bell_has_notification_payload_and_anonymous_header(self):
        today = timezone.localdate()
        Debt.objects.create(user=self.user, direction="lent", person="Karim", amount=2000, due_date=today + timedelta(days=1), remind_days=3)
        page = self.c.get(reverse("dashboard"))
        self.assertEqual(len(page.context["reminders_data"]), 1)
        self.assertIn("Karim", page.context["reminders_data"][0]["title"])
        self.assertContains(page, 'id="notifToggle"')
        anon = Client().get(reverse("help")).content.decode()
        self.assertNotIn("Log out", anon); self.assertNotIn("Transactions", anon)       # public pages show a plain header
        self.assertIn("Log in", anon)

    def test_light_is_default_with_sidebar_and_avatar_only_profile_button(self):
        page = self.c.get(reverse("dashboard")).content.decode()
        self.assertIn('<aside class="side">', page)                                    # navigation lives on the left
        self.assertIn(">Smart Entry<", page)
        self.assertNotIn('class="topnav"', page)
        btn = page.split('class="userbtn"')[1].split("</button>")[0]
        self.assertIn('class="avatar"', btn); self.assertNotIn("Nayeem", btn.replace("Nayeem J", ""))  # no name beside the avatar
        self.assertNotIn(self.user.display_name, btn)
        css = open("core/static/core/css/app.css").read()
        self.assertIn("--primary:#059669", css.split(":root[data-theme=dark]")[0])      # original green, light by default
        self.assertNotIn("max-width:1120px", css)                                       # content is not squeezed into a narrow column

    def test_voice_tries_groq_before_gemini(self):
        order = []
        with override_settings(GROQ_API_KEY="k", GEMINI_API_KEY="g"), \
                patch("core.ai._groq_stt", side_effect=lambda *a: order.append("groq") or "ok"), \
                patch("core.ai._gemini_stt", side_effect=lambda *a: order.append("gemini") or "no"):
            self.assertEqual(ai.transcribe(self.user, b"x" * 900, "audio/webm"), "ok")
        self.assertEqual(order, ["groq"])                                               # Gemini is only the fallback

    def test_stylesheet_has_light_dark_themes_and_reduced_motion(self):
        css = open("core/static/core/css/app.css").read()
        for needle in ['[data-theme=dark]', 'data-theme=system', 'prefers-color-scheme:dark', 'prefers-reduced-motion', 'prefers-reduced-transparency', 'backdrop-filter', '--spring:linear(']:
            self.assertIn(needle, css)
        self.assertNotRegex(css, r"font-size:\d+px")                                   # type scales with the user's text-size setting

    def test_public_pages_and_footer(self):
        for name in ["help", "about", "privacy"]:
            self.assertEqual(Client().get(reverse(name)).status_code, 200, name)
        self.assertContains(self.c.get(reverse("dashboard")), "Privacy")

    def test_manual_form_defaults_to_today_and_cash(self):
        r = self.c.get(reverse("transaction_add"))
        cash = Account.objects.get(user=self.user, name="Cash")
        self.assertEqual(r.context["sel"], str(cash.id))
        self.assertContains(r, f'<option value="{cash.id}" selected>')
        self.assertContains(r, f'value="{timezone.localdate().isoformat()}"')

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
        self.assertIn("Rahim", page); self.assertIn("Karim", page); self.assertNotIn("Far", page.split("<h4>Reminders</h4>")[1].split("All debts")[0])
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

    def test_reminder_on_a_specific_date_without_due_date(self):
        from . import services
        today = timezone.localdate()
        d = lambda n: (today + timedelta(days=n)).isoformat()
        self.c.post(reverse("debt_add"), {"direction": "lent", "person": "Today", "amount": "50", "remind_days": "date", "remind_on": d(0)})
        self.c.post(reverse("debt_add"), {"direction": "lent", "person": "Future", "amount": "50", "remind_days": "date", "remind_on": d(5)})
        self.c.post(reverse("debt_add"), {"direction": "lent", "person": "Missing", "amount": "50", "remind_days": "date", "remind_on": ""})  # date not picked -> refused
        self.assertEqual(sorted(Debt.objects.values_list("person", flat=True)), ["Future", "Today"])
        self.assertEqual([r["debt"].person for r in services.reminders(self.user, today)], ["Today"])
        page = self.c.get(reverse("dashboard"))
        self.assertEqual(len(page.context["reminders_data"]), 1)
        self.assertEqual(page.context["reminders_data"][0]["body"], "Reminder")        # no due date to count from
        fut = Debt.objects.get(person="Future")
        self.c.post(reverse("debt_remind", args=[fut.pk]), {"remind_days": "date", "remind_on": d(0)})   # edit later
        self.assertEqual(sorted(r["debt"].person for r in services.reminders(self.user, today)), ["Future", "Today"])
        self.c.post(reverse("debt_pay", args=[fut.pk]), {"amount": "50"})                # repaid -> no more reminders
        self.assertEqual([r["debt"].person for r in services.reminders(self.user, today)], ["Today"])
        # Smart Entry rows carry the date option too
        row = {"type": "lent", "amount": "10", "date": today.isoformat(), "note": "Sumi", "remind_days": "date", "remind_on": d(2)}
        self.c.post(reverse("api_save_transactions"), json.dumps({"transactions": [row]}), content_type="application/json")
        self.assertEqual(str(Debt.objects.get(person="Sumi").remind_on), d(2))

    def test_repaid_debts_move_to_closed_section(self):
        today = timezone.localdate()
        a = Debt.objects.create(user=self.user, direction="borrowed", person="OpenOne", amount=300)
        b = Debt.objects.create(user=self.user, direction="lent", person="DoneOne", amount=200)
        page = self.c.get(reverse("debts"))
        self.assertEqual(([r["debt"].person for r in page.context["owe"]], [r["debt"].person for r in page.context["owed"]], page.context["closed"]), (["OpenOne"], ["DoneOne"], []))
        self.c.post(reverse("debt_pay", args=[b.pk]), {"amount": "120"})                 # partly repaid: still open
        self.assertEqual(len(self.c.get(reverse("debts")).context["closed"]), 0)
        self.c.post(reverse("debt_pay", args=[b.pk]), {"amount": "80", "date": today.isoformat()})  # fully repaid
        page = self.c.get(reverse("debts"))
        self.assertEqual([r["debt"].person for r in page.context["owed"]], [])           # gone from the active list
        self.assertEqual([r["debt"].person for r in page.context["closed"]], ["DoneOne"])
        self.assertEqual(page.context["closed"][0]["closed_on"], today)
        self.assertContains(page, "Closed"); self.assertContains(page, "Repaid")
        self.assertEqual(page.context["totals"], {"owe": 300, "owed": 0})                # closed debts no longer count

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

    PAY = {"bkash": {"number": "01700000000", "holder": "HishabAI", "kind": "Personal"}, "nagad": {"number": "", "holder": "", "kind": ""},
           "rocket": {"number": "01800000000-5", "holder": "", "kind": "Personal"},
           "bank": {"bank": "City Bank", "holder": "HishabAI Ltd", "number": "1234567890", "branch": "Dhanmondi", "routing": "225260000"}}

    @staticmethod
    def _png(size=(120, 80), color=(10, 150, 90)):
        from io import BytesIO

        from PIL import Image
        buf = BytesIO(); Image.new("RGB", size, color).save(buf, "PNG"); return buf.getvalue()

    def _pay(self, **over):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {"plan": "monthly", "method": "bkash", "name": "Nusrat J", "phone": "01711111111", "email": "n@example.com",
                "sender_number": "01711111111", "transaction_id": "TX123", "note": "",
                "screenshot": SimpleUploadedFile("pay.png", self._png(), content_type="image/png")}
        data.update(over)
        return self.c.post(reverse("payment_start"), {k: v for k, v in data.items() if v is not None})

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_payment_page_shows_only_configured_accounts(self):
        page = self.c.get(reverse("payment_start") + "?plan=yearly")
        self.assertContains(page, "01700000000"); self.assertContains(page, "01800000000-5")      # bKash, Rocket
        self.assertContains(page, "City Bank"); self.assertContains(page, "1234567890")           # bank details
        self.assertEqual([m["key"] for m in page.context["methods"]], ["bkash", "rocket", "bank"])      # unconfigured accounts are not shown
        self.assertContains(page, "৳1,299")
        self.assertRedirects(self.c.get(reverse("payment_start") + "?plan=bogus"), reverse("subscription"))
        self.assertContains(self.c.get(reverse("subscription")), "Take subscription")
        with self.settings(PAYMENT_ACCOUNTS={k: {} for k in self.PAY}):
            page = self.c.get(reverse("subscription"))
            self.assertNotContains(page, "no payment account numbers")                         # normal users do not see the staff hint
            self.assertNotContains(page, "disabled\" href")                                    # and the buttons still work
            self.assertContains(page, reverse("payment_start") + "?plan=monthly")

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_submit_payment_proof_creates_pending_request_and_compresses_screenshot(self):
        r = self._pay()
        self.assertRedirects(r, reverse("subscription"))
        p = PaymentRequest.objects.get()
        self.assertEqual((p.status, p.plan, p.amount, p.method, p.transaction_id, p.email), ("pending", "monthly", 149, "bkash", "TX123", "n@example.com"))
        self.assertTrue(bytes(p.screenshot).startswith(b"\xff\xd8"))                             # stored as JPEG
        self.user.refresh_from_db(); self.assertFalse(self.user.is_paid)                        # nothing activates until staff approve
        self.assertContains(self.c.get(reverse("subscription")), "Pending review")

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_payment_validation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad = [dict(screenshot=None), dict(screenshot=SimpleUploadedFile("x.png", b"not an image at all" * 50, content_type="image/png")),
               dict(phone="abc"), dict(email="nope"), dict(name=""), dict(method="visa"),
               dict(screenshot=SimpleUploadedFile("big.png", b"0" * 4_100_000, content_type="image/png"))]
        for over in bad:
            self.assertContains(self._pay(**over), 'class="msg error"')
        self.assertEqual(PaymentRequest.objects.count(), 0)
        self._pay(transaction_id="")                                                            # transaction id is optional
        self.assertEqual(PaymentRequest.objects.count(), 1)
        self._pay(transaction_id="DUP1")
        self.assertContains(self._pay(transaction_id="dup1"), "already been submitted")         # same id twice (any case)
        self._pay(transaction_id="")
        self.assertContains(self._pay(transaction_id="NEW"), "waiting for review")              # pending cap of 3

    @override_settings(PAYMENT_ACCOUNTS={})
    def test_form_works_even_before_account_numbers_are_configured(self):
        page = self.c.get(reverse("payment_start") + "?plan=yearly")
        self.assertEqual(page.status_code, 200)
        for needle in ['name="phone"', 'name="email"', 'name="transaction_id"', 'type="file"', "Our payment account details will appear here", ".pdf"]:
            self.assertContains(page, needle)
        self.assertEqual(len(page.context["method_choices"]), 4)                                  # bKash, Nagad, Rocket, Bank
        self.assertEqual(self._pay(method="nagad", transaction_id="").status_code, 302)           # submitting works too
        self.assertEqual(PaymentRequest.objects.get().method, "nagad")
        staff_page = Client(); staff_page.force_login(User.objects.create_user("s@example.com", "s3cret-pass-9", is_staff=True))
        self.assertContains(staff_page.get(reverse("subscription")), "no payment account numbers")   # only staff are told to configure them

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_pdf_proof_is_accepted_stored_and_served_safely(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        self.assertEqual(self._pay(screenshot=SimpleUploadedFile("receipt.pdf", pdf, content_type="application/pdf")).status_code, 302)
        p = PaymentRequest.objects.get()
        self.assertEqual((p.proof_type, bytes(p.screenshot)), ("application/pdf", pdf))          # kept exactly as uploaded
        r = self.c.get(reverse("payment_screenshot", args=[p.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf"); self.assertEqual(r["Content-Security-Policy"], "sandbox")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        staff = Client(); staff.force_login(User.objects.create_user("s@example.com", "s3cret-pass-9", is_staff=True))
        self.assertContains(staff.get(reverse("admin_payments")), "Open PDF proof")
        fake = SimpleUploadedFile("receipt.pdf", b"<html><script>alert(1)</script></html>" * 20, content_type="application/pdf")
        self.assertContains(self._pay(screenshot=fake, transaction_id="X"), "PNG, JPG or PDF")      # the file is checked, not its name

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_screenshot_is_private_to_owner_and_staff(self):
        self._pay()
        p = PaymentRequest.objects.get(); url = reverse("payment_screenshot", args=[p.pk])
        self.assertEqual(self.c.get(url)["Content-Type"], "image/jpeg")
        other = Client(); other.force_login(User.objects.create_user("o@example.com", "s3cret-pass-9"))
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(Client().get(url).status_code, 302)
        staff = Client(); staff.force_login(User.objects.create_user("s@example.com", "s3cret-pass-9", is_staff=True))
        self.assertEqual(staff.get(url).status_code, 200)

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_approving_extends_the_plan_and_only_once(self):
        self._pay(); self._pay(transaction_id="B", plan="yearly"); self._pay(transaction_id="C")
        admin = User.objects.create_user("a@x.com", "s3cret-pass-9", is_staff=True)
        monthly, yearly, third = PaymentRequest.objects.order_by("id")
        self.assertTrue(monthly.review("approved", admin))
        self.assertFalse(monthly.review("approved", admin))                                      # twice: no double credit
        self.user.refresh_from_db(); first_end = self.user.paid_until
        self.assertEqual(self.user.plan, "monthly"); self.assertAlmostEqual((first_end - timezone.now()).days, 30, delta=1)
        self.assertTrue(yearly.review("approved", admin))
        self.user.refresh_from_db()
        self.assertAlmostEqual((self.user.paid_until - first_end).days, 365, delta=1)             # added after the paid period
        self.assertEqual((monthly.status, monthly.reviewed_by), ("approved", admin))
        self.assertTrue(third.review("rejected", admin, "Amount did not match"))
        before = User.objects.get(pk=self.user.pk).paid_until
        self.assertFalse(third.review("approved", admin))                                        # a reviewed payment is final
        self.assertEqual((third.status, third.admin_note, User.objects.get(pk=self.user.pk).paid_until), ("rejected", "Amount did not match", before))
        self.assertContains(self.c.get(reverse("subscription")), "Rejected")
        self.assertEqual(Client().get(reverse("dashboard")).status_code, 302)
        ac = Client(); ac.force_login(admin)
        self._pay(transaction_id="D")
        self.assertEqual(ac.get(reverse("dashboard")).context["pending_payments"], 1)             # staff see a pending badge
        self.assertNotIn("pending_payments", self.c.get(reverse("dashboard")).context)

    # ------------------------------------------------------------ admin panel
    def _staff(self, **kw):
        u = User.objects.create_user("staff@example.com", "s3cret-pass-9", is_staff=True, **kw)
        c = Client(); c.force_login(u); return u, c

    def test_admin_panel_is_staff_only(self):
        for name in ["admin_overview", "admin_users", "admin_payments"]:
            self.assertEqual(self.c.get(reverse(name)).status_code, 404, name)      # a normal user gets a 404
            self.assertEqual(Client().get(reverse(name)).status_code, 302, name)    # anonymous -> login
        self.assertEqual(self.c.get(reverse("admin_user", args=[self.user.pk])).status_code, 404)
        self.assertEqual(self.c.post(reverse("admin_user_active", args=[self.user.pk])).status_code, 404)
        staff, sc = self._staff()
        for name in ["admin_overview", "admin_users", "admin_payments"]:
            self.assertEqual(sc.get(reverse(name)).status_code, 200, name)
        self.assertContains(sc.get(reverse("admin_overview")), "Counts only")

    def test_visitor_tracking_counts_guests_and_users_but_not_staff_or_bots(self):
        from .models import VisitorDay
        browser = {"HTTP_USER_AGENT": "Mozilla/5.0 Chrome"}
        g = Client()
        r = g.get(reverse("login"), **browser)
        key = r.cookies["hv"].value
        g.get(reverse("login"), **browser)                                           # same visitor, second page view
        self.assertEqual(VisitorDay.objects.get().views, 2)
        Client().get(reverse("login"), HTTP_USER_AGENT="Googlebot/2.1")              # bots are ignored
        Client().get(reverse("login"))                                               # no user agent -> ignored
        self.assertEqual(VisitorDay.objects.count(), 1)
        self.c.get(reverse("dashboard"), **browser)                                  # a logged-in user is recorded with their id
        self.assertEqual(VisitorDay.objects.filter(user=self.user).count(), 1)
        _, sc = self._staff(); sc.get(reverse("dashboard"), **browser)               # staff are not counted
        self.assertEqual(VisitorDay.objects.count(), 2)
        self.assertEqual(g.post(reverse("api_ping"), {"s": "15"}, **browser).status_code, 204)   # heartbeat adds time
        g.post(reverse("api_ping"), {"s": "999"}, **browser)                         # capped at 30 s per ping
        self.assertEqual(VisitorDay.objects.get(key=key).seconds, 45)
        Client().post(reverse("api_ping"), {"s": "15"}, **browser)                   # no cookie -> nothing recorded
        self.assertEqual(sum(VisitorDay.objects.values_list("seconds", flat=True)), 45)

    def test_overview_numbers(self):
        from .models import VisitorDay
        today = timezone.localdate()
        VisitorDay.objects.create(day=today, key="a" * 24, user=self.user, views=3, seconds=125)
        VisitorDay.objects.create(day=today, key="b" * 24, views=1, seconds=15)
        AICall.objects.create(user=self.user, kind="entry", tokens=1200, provider="gemini")
        AICall.objects.create(user=self.user, kind="voice", provider="groq")
        Transaction.objects.create(user=self.user, kind="expense", amount=5, ai_generated=True)
        Transaction.objects.create(user=self.user, kind="expense", amount=5)
        Debt.objects.create(user=self.user, direction="lent", person="X", amount=1)
        _, sc = self._staff()
        s = sc.get(reverse("admin_overview")).context["s"]
        self.assertEqual((s["visitors"], s["logged"], s["guests"], s["views"]), (2, 1, 1, 4))
        self.assertEqual((s["total_secs"], s["avg_secs"], s["avg_secs_logged"]), (140, 70, 125))
        self.assertEqual((s["ai"]["n"], s["ai"]["t"], s["ai"]["n_cap"]), (2, 1200, 400))
        self.assertEqual(s["top_ai"][0]["email"], "a@example.com"); self.assertEqual(s["top_ai"][0]["n"], 2)
        self.assertEqual(sum(s["charts"]["with_ai"]), 1); self.assertEqual(sum(s["charts"]["by_hand"]), 1); self.assertEqual(sum(s["charts"]["debts"]), 1)
        self.assertEqual(len(s["charts"]["labels"]), 30)
        self.assertEqual(s["users_total"], 2); self.assertEqual(s["adding"], 1)
        from .templatetags.ui import dur
        self.assertEqual((dur(3), dur(125), dur(3900)), ("3s", "2m 05s", "1h 05m"))

    @patch("core.ai._groq", return_value=ai._with_tokens("hello", 321))
    def test_ai_records_tokens_and_app_wide_caps_stop_everyone(self, _):
        with override_settings(GROQ_API_KEY="k", GEMINI_API_KEY=""):
            self.assertEqual(ai.ask(self.user, "s", "p", kind="ask"), "hello")
            call = AICall.objects.get()
            self.assertEqual((call.kind, call.provider, call.tokens), ("ask", "groq", 321))
            with override_settings(AI_APP_DAILY_REQUESTS=1):
                other = User.objects.create_user("o@example.com", "s3cret-pass-9")
                with self.assertRaisesMessage(ai.AIQuotaExceeded, "everyone"):
                    ai.ask(other, "s", "p")
            with override_settings(AI_APP_DAILY_TOKENS=300):
                with self.assertRaises(ai.AIQuotaExceeded):
                    ai.ask(self.user, "s", "p")

    def test_users_list_filter_search_sort_and_deactivate(self):
        _, sc = self._staff()
        quiet = User.objects.create_user("quiet@example.com", "s3cret-pass-9", name="Quiet One")
        Transaction.objects.create(user=self.user, kind="expense", amount=1); Transaction.objects.create(user=self.user, kind="expense", amount=1)
        emails = lambda **q: [u.email for u in sc.get(reverse("admin_users"), q).context["page"]]
        self.assertEqual(set(emails()), {"a@example.com", "quiet@example.com", "staff@example.com"})
        self.assertEqual(emails(q="quiet one"), ["quiet@example.com"])
        self.assertEqual(emails(sort="entries")[0], "a@example.com")
        self.assertEqual(emails(status="staff"), ["staff@example.com"])
        self.assertContains(sc.get(reverse("admin_user", args=[quiet.pk])), "quiet@example.com")
        sc.post(reverse("admin_user_active", args=[quiet.pk]))
        quiet.refresh_from_db(); self.assertFalse(quiet.is_active)
        self.assertEqual(emails(status="deactivated"), ["quiet@example.com"])
        self.assertFalse(Client().login(username="quiet@example.com", password="s3cret-pass-9"))      # cannot log in any more
        sc.post(reverse("admin_user_active", args=[quiet.pk])); quiet.refresh_from_db(); self.assertTrue(quiet.is_active)
        staff = User.objects.get(email="staff@example.com")
        sc.post(reverse("admin_user_active", args=[staff.pk])); staff.refresh_from_db(); self.assertTrue(staff.is_active)   # not yourself
        boss = User.objects.create_user("boss@example.com", "s3cret-pass-9", is_superuser=True)
        sc.post(reverse("admin_user_active", args=[boss.pk])); boss.refresh_from_db(); self.assertTrue(boss.is_active)         # not a superuser

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_review_payments_in_the_admin_panel(self):
        self._pay(); self._pay(transaction_id="B")
        _, sc = self._staff()
        first, second = PaymentRequest.objects.order_by("id")
        page = sc.get(reverse("admin_payments"))
        self.assertContains(page, "TX123"); self.assertContains(page, reverse("payment_screenshot", args=[first.pk]))
        self.assertEqual(sc.get(reverse("admin_overview")).context["s"]["pending_payments"], 2)
        sc.post(reverse("admin_payment_review", args=[first.pk]), {"action": "approve"})
        self.user.refresh_from_db(); self.assertTrue(self.user.is_paid); self.assertEqual(self.user.plan, "monthly")
        end = self.user.paid_until
        sc.post(reverse("admin_payment_review", args=[first.pk]), {"action": "approve"})        # twice -> no double credit
        self.user.refresh_from_db(); self.assertEqual(self.user.paid_until, end)
        sc.post(reverse("admin_payment_review", args=[second.pk]), {"action": "reject", "note": "Amount did not match"})
        second.refresh_from_db(); self.assertEqual((second.status, second.admin_note), ("rejected", "Amount did not match"))
        self.assertContains(self.c.get(reverse("subscription")), "Amount did not match")        # the user sees why
        self.assertEqual(self.c.post(reverse("admin_payment_review", args=[second.pk]), {"action": "approve"}).status_code, 404)  # normal users can't review
        self.assertEqual(len(sc.get(reverse("admin_payments"), {"status": "approved"}).context["page"]), 1)

    def test_superusers_can_promote_and_demote_other_users(self):
        boss = User.objects.create_user("boss@example.com", "s3cret-pass-9", is_staff=True, is_superuser=True); bc = Client(); bc.force_login(boss)
        _, sc = self._staff()                                                           # plain staff
        role = lambda client, target, r: client.post(reverse("admin_user_role", args=[target.pk]), {"role": r})
        role(bc, self.user, "staff"); self.user.refresh_from_db()
        self.assertEqual((self.user.is_staff, self.user.is_superuser), (True, False))
        role(bc, self.user, "superuser"); self.user.refresh_from_db()
        self.assertEqual((self.user.is_staff, self.user.is_superuser), (True, True))
        self.assertContains(bc.get(reverse("admin_user", args=[self.user.pk])), "Save role")        # the role control is shown to superusers
        role(bc, self.user, "user"); self.user.refresh_from_db()
        self.assertEqual((self.user.is_staff, self.user.is_superuser), (False, False))
        role(bc, self.user, "admin"); self.user.refresh_from_db(); self.assertFalse(self.user.is_staff)   # unknown role ignored
        role(bc, boss, "user"); boss.refresh_from_db(); self.assertTrue(boss.is_superuser)               # cannot change own role
        staff = User.objects.get(email="staff@example.com")
        role(sc, self.user, "superuser"); self.user.refresh_from_db(); self.assertFalse(self.user.is_superuser)   # staff cannot promote
        self.assertNotContains(sc.get(reverse("admin_user", args=[self.user.pk])), "Save role")
        role(sc, staff, "superuser"); staff.refresh_from_db(); self.assertFalse(staff.is_superuser)
        self.assertEqual(role(self.c, self.user, "superuser").status_code, 404)                          # normal users: no panel at all

    @override_settings(PAYMENT_ACCOUNTS=PAY)
    def test_unsubscribe_and_cancel_pending_payment(self):
        self.assertEqual(self.c.post(reverse("subscription_cancel")).status_code, 302)           # nothing to cancel: harmless
        self.assertNotContains(self.c.get(reverse("subscription")), "Unsubscribe")
        self.user.activate_plan("yearly")
        page = self.c.get(reverse("subscription"))
        self.assertContains(page, "Unsubscribe"); self.assertContains(page, "no refund")           # warns before ending the plan
        self.c.post(reverse("subscription_cancel"))
        self.user.refresh_from_db()
        self.assertEqual((self.user.is_paid, self.user.plan, self.user.paid_until), (False, "", None))
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())                           # account and records are untouched
        self.assertEqual(Client().post(reverse("subscription_cancel")).status_code, 302)          # login required
        # withdraw a payment that is still waiting
        self._pay(); self._pay(transaction_id="B")
        first, second = PaymentRequest.objects.order_by("id")
        self.assertContains(self.c.get(reverse("subscription")), "Cancel request")
        other = Client(); other.force_login(User.objects.create_user("o@example.com", "s3cret-pass-9"))
        other.post(reverse("payment_cancel", args=[first.pk]))                                    # someone else's request
        first.refresh_from_db(); self.assertEqual(first.status, "pending")
        self.c.post(reverse("payment_cancel", args=[first.pk]))
        first.refresh_from_db(); self.assertEqual(first.status, "cancelled")
        admin = User.objects.create_user("a@x.com", "s3cret-pass-9", is_staff=True)
        self.assertFalse(first.review("approved", admin))                                         # a cancelled request can't be approved
        self.user.refresh_from_db(); self.assertFalse(self.user.is_paid)
        self.assertContains(self.c.get(reverse("subscription")), "Cancelled")
        self.c.post(reverse("payment_cancel", args=[second.pk])); self.c.post(reverse("payment_cancel", args=[second.pk]))   # idempotent

    def test_bell_badge_clears_when_opened_and_returns_for_new_reminders(self):
        today = timezone.localdate()
        Debt.objects.create(user=self.user, direction="lent", person="One", amount=10, due_date=today, remind_days=0)
        page = self.c.get(reverse("dashboard"))
        self.assertEqual(page.context["reminders_unread"], 1)
        self.assertContains(page, 'class="dotc" data-unread')                                      # the number badge is shown
        self.assertTrue(page.context["reminders"][0]["unread"])
        self.assertEqual(self.c.post(reverse("api_notifications_seen")).json(), {"ok": True})     # opening the bell
        page = self.c.get(reverse("dashboard"))
        self.assertEqual(page.context["reminders_unread"], 0)
        self.assertNotContains(page, "data-unread>")                                               # badge gone (bell and sidebar)
        self.assertEqual(len(page.context["reminders"]), 1)                                       # the reminder itself is still listed
        Debt.objects.create(user=self.user, direction="borrowed", person="Two", amount=5, due_date=today, remind_days=0)
        self.assertEqual(self.c.get(reverse("dashboard")).context["reminders_unread"], 1)         # only the new one is unseen
        self.user.refresh_from_db(); self.user.bell_seen_on = today - timedelta(days=1); self.user.save()
        self.assertEqual(self.c.get(reverse("dashboard")).context["reminders_unread"], 2)         # a new day starts unseen again
        self.c.get(reverse("debts"))                                                               # visiting Debts also counts as seen
        self.assertEqual(self.c.get(reverse("dashboard")).context["reminders_unread"], 0)
        self.assertEqual(Client().post(reverse("api_notifications_seen")).status_code, 302)       # login required

    def test_taka_filter_uses_bd_grouping(self):
        from .templatetags.ui import taka
        self.assertEqual(taka(100000), "৳1,00,000")
        self.assertEqual(taka(28350), "৳28,350")
        self.assertEqual(taka(-540), "-৳540")
