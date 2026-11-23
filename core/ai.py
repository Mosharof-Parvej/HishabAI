"""Thin AI layer: Gemini and Groq over plain HTTP, with automatic fallback.

No SDKs on purpose - keeps the serverless bundle small. Groq uses the OpenAI-compatible
chat-completions format, so GROQ_BASE_URL/GROQ_MODEL can also point at OpenRouter, xAI Grok, etc.
"""
import base64
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.utils import timezone

from .models import AICall

# Primary gets a short timeout so a slow/overloaded Gemini falls through to Groq quickly.
GEMINI_TIMEOUT = 12
GROQ_TIMEOUT = 30


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
        timeout=GEMINI_TIMEOUT,
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
        timeout=GROQ_TIMEOUT,
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


def _gate(user, providers):
    """Refuse when no key is configured or the user's daily cap is used; otherwise count this call."""
    if not providers:
        raise AIUnavailable("No AI key configured. Set GEMINI_API_KEY and/or GROQ_API_KEY.")
    since = timezone.now() - timedelta(hours=24)
    if AICall.objects.filter(user=user, created_at__gte=since).count() >= settings.AI_DAILY_LIMIT:
        raise AIQuotaExceeded("Daily AI limit reached. Try again tomorrow.")
    AICall.objects.create(user=user)


def ask(user, system, prompt, json_mode=False):
    """Call the primary provider, fall back to the other. Enforces a per-user daily cap."""
    providers = _providers()
    _gate(user, providers)
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


# ---------- speech to text (mic button) ----------

# A mixed Bangla/English prompt makes Whisper write Bangla in Bengali script (not Devanagari) and keep English as English.
STT_PROMPT = "আজ বাজারে ৫৪০ টাকা খরচ হলো। I paid 200 taka for lunch. bKash, Nagad, rickshaw, salary, ধার দিলাম।"
GEMINI_STT = "Transcribe this audio exactly as spoken. It may be Bangla, English or Banglish. Write Bangla in Bengali script. Output only the transcript."


def _groq_stt(audio, mime):
    r = requests.post(
        f"{settings.GROQ_BASE_URL.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
        files={"file": ("voice." + (mime.split("/")[-1].split(";")[0] or "webm"), audio, mime)},
        data={"model": settings.GROQ_STT_MODEL, "temperature": "0", "response_format": "json", "prompt": STT_PROMPT},
        timeout=GROQ_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["text"]


def _gemini_stt(audio, mime):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent",
        headers={"x-goog-api-key": settings.GEMINI_API_KEY},
        json={"contents": [{"role": "user", "parts": [{"text": GEMINI_STT},
                                                      {"inline_data": {"mime_type": mime.split(";")[0], "data": base64.b64encode(audio).decode()}}]}],
              "generationConfig": {"temperature": 0}},
        timeout=GROQ_TIMEOUT,
    )
    r.raise_for_status()
    return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])


def transcribe(user, audio, mime):
    """Speech -> text. Whisper (Groq) is the dedicated speech model so it goes first; Gemini is the fallback."""
    impls = []
    if settings.GROQ_API_KEY:
        impls.append(("groq", _groq_stt))
    if settings.GEMINI_API_KEY:
        impls.append(("gemini", _gemini_stt))
    _gate(user, impls)
    errors = []
    for name, fn in impls:
        try:
            return fn(audio, mime).strip()
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            errors.append(f"{name}: {e.__class__.__name__}")
    raise AIUnavailable("Speech recognition failed (" + "; ".join(errors) + ")")


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
Return ONLY JSON: {"transactions":[{"type":"expense|income|borrowed|lent","amount":number,"category":string|null,"account":string|null,"date":"YYYY-MM-DD","note":string,"person":string|null,"due_date":"YYYY-MM-DD"|null,"confidence":0-100}]}
Debts: type "borrowed" = the user RECEIVED money to pay back later (ধার নিলাম, ধার করলাম, loan নিলাম, borrowed from X);
type "lent" = the user GAVE money to be paid back (ধার দিলাম, ঋণ দিলাম, lent to X). For these set 'person' (the other party's name, required),
'category' null, and 'due_date' only if a repay date is stated (resolve relative dates from TODAY). Ordinary spending/earning stays expense/income.
Rules: amounts are in BDT (taka/tk/টাকা/৳); convert Bangla digits to numbers. One object per distinct transaction.
Resolve relative dates (আজ=today, গতকাল/yesterday, কাল is ambiguous: use yesterday if past tense) using TODAY.
'category' MUST be exactly one of the provided names for that type. 'note' is a short English label.
'account' is the wallet/bank/cash the money moved through ONLY if the text says so (e.g. "bkash e", "cash", "card diye"); it MUST be exactly one of the provided ACCOUNTS, else null.
Set confidence low when the amount, type or category is guessed. Ignore text that is not a transaction."""


def extract_transactions(user, text, categories, accounts=()):
    today = timezone.localdate()
    prompt = (
        f"TODAY: {today.isoformat()}\n"
        f"EXPENSE CATEGORIES: {', '.join(categories['expense'])}\n"
        f"INCOME CATEGORIES: {', '.join(categories['income'])}\n"
        f"ACCOUNTS: {', '.join(accounts) or 'none'}\n"
        f"TEXT:\n{text[:2000]}"
    )
    data = _parse_json(ask(user, EXTRACT_SYSTEM, prompt, json_mode=True))
    rows = []
    for t in (data.get("transactions") or [])[:20]:
        try:
            amount = Decimal(str(t["amount"])).quantize(Decimal("0.01"))
            if amount <= 0 or amount > Decimal("999999999"):
                continue
            kind = t.get("type") if t.get("type") in ("income", "borrowed", "lent") else "expense"
            is_debt = kind in ("borrowed", "lent")
            person = str(t.get("person") or "").strip()[:80]
            if is_debt and not person:
                continue
            cat = None if is_debt else t.get("category")
            if not is_debt and cat not in categories[kind]:
                cat = "Others" if kind == "expense" else "Other Income"
                if cat not in categories[kind]:
                    cat = categories[kind][-1]
            try:
                d = datetime.strptime(str(t.get("date")), "%Y-%m-%d").date()
            except ValueError:
                d = today
            if d > today:
                d = today
            conf = max(0, min(100, int(t.get("confidence", 70))))
            acct = t.get("account") if t.get("account") in accounts else None
            due = None
            if is_debt and t.get("due_date"):
                try:
                    due = datetime.strptime(str(t["due_date"]), "%Y-%m-%d").date()
                    due = due if due >= d else None
                except ValueError:
                    due = None
            rows.append(
                {"type": kind, "amount": str(amount), "category": cat, "account": acct, "date": d.isoformat(),
                 "note": person if is_debt else str(t.get("note", ""))[:200], "due_date": due.isoformat() if due else "",
                 "confidence": conf}
            )
        except (KeyError, InvalidOperation, ValueError, TypeError):
            continue
    return rows


QUERY_SYSTEM = """You turn a personal-finance question (Bangla, English or Banglish) into a JSON search filter that a database will run.
Return ONLY JSON: {"intent":"transactions|debts","title":string,"date_from":"YYYY-MM-DD"|null,"date_to":"YYYY-MM-DD"|null,
"kind":"expense|income"|null,"categories":[string],"accounts":[string],"search":string|null,"sort":"date|amount|oldest","limit":1-100,
"direction":"borrowed|lent"|null,"person":string|null}
Rules: resolve relative dates using TODAY ("this month" = first day of this month..TODAY, "last month" = the whole previous month,
"this year" = Jan 1..TODAY, "last week" = previous 7 days). No time mentioned -> null dates (all time).
'categories' and 'accounts' MUST be exact names from the provided lists, otherwise []. 'search' is a keyword for the note text.
"highest/biggest/most" -> sort "amount". Questions about loans, borrowed or lent money, who owes whom (ধার, দেনা, পাওনা, loan, owe) -> intent "debts":
'direction' = "borrowed" if the user owes, "lent" if others owe the user, null if unclear; 'person' if a name is mentioned.
'title' is a short heading in the same language as the question. Never answer the question yourself; only produce the filter."""


def _date_or_none(v, today):
    try:
        d = datetime.strptime(str(v), "%Y-%m-%d").date()
    except ValueError:
        return None
    return min(d, today)


def parse_query(user, question, categories, accounts):
    """AI -> validated filter dict. The AI only builds the filter; numbers always come from the database."""
    today = timezone.localdate()
    prompt = (f"TODAY: {today.isoformat()} ({today.strftime('%A')})\n"
              f"CATEGORIES: {', '.join(categories['expense'] + categories['income'])}\nACCOUNTS: {', '.join(accounts) or 'none'}\n"
              f"QUESTION: {question[:500]}")
    raw = _parse_json(ask(user, QUERY_SYSTEM, prompt, json_mode=True))
    cat_all = {c.lower(): c for c in categories["expense"] + categories["income"]}
    acc_all = {a.lower(): a for a in accounts}
    f = {
        "intent": "debts" if raw.get("intent") == "debts" else "transactions",
        "title": str(raw.get("title") or question)[:100],
        "date_from": _date_or_none(raw.get("date_from"), today),
        "date_to": _date_or_none(raw.get("date_to"), today),
        "kind": raw.get("kind") if raw.get("kind") in ("expense", "income") else None,
        "categories": [cat_all[c.lower()] for c in (raw.get("categories") or []) if str(c).lower() in cat_all],
        "accounts": [acc_all[a.lower()] for a in (raw.get("accounts") or []) if str(a).lower() in acc_all],
        "search": (str(raw["search"])[:60] if raw.get("search") else None),
        "sort": raw.get("sort") if raw.get("sort") in ("date", "amount", "oldest") else "date",
        "direction": raw.get("direction") if raw.get("direction") in ("borrowed", "lent") else None,
        "person": (str(raw["person"])[:60] if raw.get("person") else None),
    }
    try:
        f["limit"] = max(1, min(100, int(raw.get("limit") or 50)))
    except (TypeError, ValueError):
        f["limit"] = 50
    return f


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
