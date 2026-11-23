"""Thin AI layer: Gemini and Groq over plain HTTP, with automatic fallback.

No SDKs on purpose - keeps the serverless bundle small. Groq uses the OpenAI-compatible
chat-completions format, so GROQ_BASE_URL/GROQ_MODEL can also point at OpenRouter, xAI Grok, etc.
"""
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.utils import timezone

from .models import AICall

TIMEOUT = 40


class AIUnavailable(Exception):
    pass


class AIQuotaExceeded(Exception):
    pass


def _gemini(system, prompt, json_mode):
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        json=body,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])


def _groq(system, prompt, json_mode):
    body = {
        "model": settings.GROQ_MODEL,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(
        f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        json=body,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _providers():
    available = []
    if settings.GEMINI_API_KEY:
        available.append(("gemini", _gemini))
    if settings.GROQ_API_KEY:
        available.append(("groq", _groq))
    available.sort(key=lambda p: p[0] != settings.AI_PRIMARY)  # primary first, other = fallback
    return available


def ask(user, system, prompt, json_mode=False):
    """Call the primary provider, fall back to the other. Enforces a per-user daily cap."""
    providers = _providers()
    if not providers:
        raise AIUnavailable("No AI key configured. Set GEMINI_API_KEY and/or GROQ_API_KEY.")
    since = timezone.now() - timedelta(hours=24)
    if AICall.objects.filter(user=user, created_at__gte=since).count() >= settings.AI_DAILY_LIMIT:
        raise AIQuotaExceeded("Daily AI limit reached. Try again tomorrow.")
    AICall.objects.create(user=user)
    errors = []
    for name, fn in providers:
        try:
            text = fn(system, prompt, json_mode).strip()
            if text:
                return text
            errors.append(f"{name}: empty response")
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            errors.append(f"{name}: {e.__class__.__name__}")
    raise AIUnavailable("AI providers failed (" + "; ".join(errors) + ")")


def _parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise AIUnavailable("AI returned invalid JSON.")


# ---------- features ----------

EXTRACT_SYSTEM = """You extract personal finance transactions from text written in Bangla, English or Banglish.
Return ONLY JSON: {"transactions":[{"type":"expense|income","amount":number,"category":string,"date":"YYYY-MM-DD","note":string,"confidence":0-100}]}
Rules: amounts are in BDT (taka/tk/টাকা/৳); convert Bangla digits to numbers. One object per distinct transaction.
Resolve relative dates (আজ=today, গতকাল/yesterday, কাল is ambiguous: use yesterday if past tense) using TODAY.
'category' MUST be exactly one of the provided names for that type. 'note' is a short English label.
Set confidence low when the amount, type or category is guessed. Ignore text that is not a transaction."""


def extract_transactions(user, text, categories):
    today = timezone.localdate()
    prompt = (
        f"TODAY: {today.isoformat()}\n"
        f"EXPENSE CATEGORIES: {', '.join(categories['expense'])}\n"
        f"INCOME CATEGORIES: {', '.join(categories['income'])}\n"
        f"TEXT:\n{text[:2000]}"
    )
    data = _parse_json(ask(user, EXTRACT_SYSTEM, prompt, json_mode=True))
    rows = []
    for t in (data.get("transactions") or [])[:20]:
        try:
            amount = Decimal(str(t["amount"])).quantize(Decimal("0.01"))
            if amount <= 0 or amount > Decimal("999999999"):
                continue
            kind = "income" if t.get("type") == "income" else "expense"
            cat = t.get("category")
            if cat not in categories[kind]:
                cat = categories[kind][-1]  # "Others"
            try:
                d = datetime.strptime(str(t.get("date")), "%Y-%m-%d").date()
            except ValueError:
                d = today
            if d > today:
                d = today
            conf = max(0, min(100, int(t.get("confidence", 70))))
            rows.append(
                {"type": kind, "amount": str(amount), "category": cat, "date": d.isoformat(),
                 "note": str(t.get("note", ""))[:200], "confidence": conf}
            )
        except (KeyError, InvalidOperation, ValueError, TypeError):
            continue
    return rows


CHAT_SYSTEM = """You are HishabAI, a friendly personal finance assistant inside an expense tracker used in Bangladesh.
Answer ONLY from the JSON data provided (the user's own records, amounts in BDT, shown with the ৳ sign).
Reply in the same language as the user's question (Bangla or English). Be concise (max ~120 words), use concrete numbers,
and never invent figures. If the data cannot answer, say so. You are not a licensed financial advisor; give practical
budgeting tips only, no investment/tax/legal advice. Plain text, no markdown tables."""


def chat(user, question, context, history):
    convo = "\n".join(f"{m.role.upper()}: {m.content}" for m in history[-6:])
    prompt = f"USER DATA:\n{json.dumps(context, ensure_ascii=False)}\n\nCONVERSATION:\n{convo}\nUSER: {question[:1000]}"
    return ask(user, CHAT_SYSTEM, prompt)


INSIGHT_SYSTEM = """You write one short financial insight (max 2 sentences, under 45 words) for a Bangladeshi expense app
dashboard, based ONLY on the JSON data. Mention a concrete number. Plain text, no markdown."""


def insight(user, context):
    lang = "Bangla" if user.language == "bn" else "English"
    return ask(user, INSIGHT_SYSTEM, f"Write in {lang}.\nDATA:\n{json.dumps(context, ensure_ascii=False)}")


BUDGET_SYSTEM = """You are a budgeting coach for a Bangladeshi user. From the JSON data (this month's spend, budgets, last month),
return ONLY JSON: {"tips":[string,string,string]}. Each tip: one sentence, concrete numbers in ৳, actionable
(e.g. move a limit, cut a category). Exactly 3 tips."""


def budget_suggestions(user, context):
    lang = "Bangla" if user.language == "bn" else "English"
    data = _parse_json(ask(user, BUDGET_SYSTEM, f"Write in {lang}.\nDATA:\n{json.dumps(context, ensure_ascii=False)}", json_mode=True))
    return [str(t) for t in (data.get("tips") or [])][:3]
