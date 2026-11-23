from django.db import migrations


def backfill(apps, schema_editor):
    """Give every existing user a Cash account and attach their account-less transactions to it."""
    User = apps.get_model("core", "User")
    Account = apps.get_model("core", "Account")
    Transaction = apps.get_model("core", "Transaction")
    for user in User.objects.all():
        cash, _ = Account.objects.get_or_create(user=user, name="Cash", defaults={"kind": "cash", "provider": "cash"})
        Transaction.objects.filter(user=user, account__isnull=True).update(account=cash)


class Migration(migrations.Migration):
    dependencies = [("core", "0002_accounts_debts")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
