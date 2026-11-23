from django.db import migrations

DEFAULTS = [("Cash", "cash", "cash"), ("bKash", "wallet", "bkash"), ("Nagad", "wallet", "nagad"),
            ("Rocket", "wallet", "rocket"), ("Bank", "bank", ""), ("Card", "card", "")]


def add_defaults(apps, schema_editor):
    """Idempotent: only creates default accounts a user does not already have (matched by name)."""
    User, Account = apps.get_model("core", "User"), apps.get_model("core", "Account")
    for user in User.objects.all():
        for name, kind, provider in DEFAULTS:
            Account.objects.get_or_create(user=user, name=name, defaults={"kind": kind, "provider": provider})


class Migration(migrations.Migration):
    dependencies = [("core", "0004_debt_remind_days")]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
