from django.db import migrations

# (name, kind, colour, emoji) - same list as core.models.DEFAULT_CATEGORIES at the time of this migration
DEFAULTS = [
    ("Food", "expense", "#22C55E", "🍚"), ("Rent", "expense", "#8B5CF6", "🏠"), ("Bills & Utilities", "expense", "#F59E0B", "🧾"),
    ("Mobile & Internet", "expense", "#06B6D4", "📱"), ("Education", "expense", "#6366F1", "🎓"), ("Transport", "expense", "#0EA5E9", "🚌"),
    ("Shopping", "expense", "#EC4899", "🛍️"), ("Health", "expense", "#EF4444", "🏥"), ("Family", "expense", "#14B8A6", "👪"),
    ("Gift", "expense", "#D97706", "🎁"), ("Entertainment", "expense", "#F97316", "🎬"), ("Others", "expense", "#94A3B8", "🏷️"),
    ("Salary", "income", "#22C55E", "💼"), ("Tuition", "income", "#0EA5E9", "📚"), ("Business", "income", "#8B5CF6", "🏪"),
    ("Other Income", "income", "#94A3B8", "💰"),
]


def forwards(apps, schema_editor):
    """Idempotent: add missing default categories and put an emoji on existing defaults. Colours and names of
    categories the user already has are left alone."""
    User, Category = apps.get_model("core", "User"), apps.get_model("core", "Category")
    for user in User.objects.all():
        for name, kind, color, emoji in DEFAULTS:
            cat, created = Category.objects.get_or_create(user=user, name=name, defaults={"kind": kind, "color": color, "emoji": emoji})
            if not created and not cat.emoji:
                cat.emoji = emoji
                cat.save(update_fields=["emoji"])


class Migration(migrations.Migration):
    dependencies = [("core", "0006_category_emoji")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
