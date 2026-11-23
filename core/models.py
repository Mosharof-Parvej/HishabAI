from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

DEFAULT_CATEGORIES = [
    # (name, kind, color)
    ("Food", "expense", "#059669"),
    ("Bills & Utilities", "expense", "#F59E0B"),
    ("Education", "expense", "#8B5CF6"),
    ("Transport", "expense", "#EAB308"),
    ("Shopping", "expense", "#EC4899"),
    ("Health", "expense", "#0EA5E9"),
    ("Entertainment", "expense", "#F97316"),
    ("Others", "expense", "#94A3B8"),
    ("Salary", "income", "#059669"),
    ("Tuition", "income", "#0EA5E9"),
    ("Business", "income", "#8B5CF6"),
    ("Other Income", "income", "#94A3B8"),
]


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.trial_ends_at = timezone.now() + timedelta(days=settings.TRIAL_DAYS)
        user.save(using=self._db)
        user.seed_categories()
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=2, choices=[("bn", "বাংলা"), ("en", "English")], default="en")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    plan = models.CharField(max_length=10, blank=True)  # "", "monthly", "yearly"
    paid_until = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.name or self.email.split("@")[0]

    @property
    def initials(self):
        parts = self.display_name.split()
        return "".join(p[0] for p in parts[:2]).upper() or "U"

    @property
    def is_paid(self):
        return bool(self.paid_until and self.paid_until > timezone.now())

    @property
    def trial_days_left(self):
        if not self.trial_ends_at:
            return 0
        return max(0, (self.trial_ends_at - timezone.now()).days + 1) if self.trial_ends_at > timezone.now() else 0

    @property
    def has_access(self):
        return self.is_paid or self.trial_days_left > 0

    def seed_categories(self):
        Category.objects.bulk_create(
            [Category(user=self, name=n, kind=k, color=c) for n, k, c in DEFAULT_CATEGORIES],
            ignore_conflicts=True,
        )


class Category(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=40)
    kind = models.CharField(max_length=7, choices=[("expense", "Expense"), ("income", "Income")])
    color = models.CharField(max_length=7, default="#94A3B8")

    class Meta:
        unique_together = [("user", "name")]
        ordering = ["id"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Transaction(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    kind = models.CharField(max_length=7, choices=[("expense", "Expense"), ("income", "Income")])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    note = models.CharField(max_length=200, blank=True)
    ai_generated = models.BooleanField(default=False)
    ai_confidence = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["user", "date"])]


class Budget(models.Model):
    """Recurring monthly limit for one expense category."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budgets")
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    limit = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        unique_together = [("user", "category")]


class ChatMessage(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat")
    role = models.CharField(max_length=9, choices=[("user", "user"), ("assistant", "assistant")])
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class AIInsight(models.Model):
    """One cached AI-generated text per user/kind/day, so free-tier quota is not burnt on reloads."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20)
    day = models.DateField()
    text = models.TextField()

    class Meta:
        unique_together = [("user", "kind", "day")]


class AICall(models.Model):
    """Counts AI calls per user to protect the shared free-tier API keys."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]
