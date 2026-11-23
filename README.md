# HishabAI — AI-powered personal expense tracker (Django)

Bangla/English natural-language expense entry, dashboard, budgets & reports, AI assistant and a 15-day trial → paid plans, accounts (cash / bKash / Nagad / banks / cards), debts with repayment reminders, and AskHishabAI.
Stack: Django 5.2 · Neon Postgres · Vercel · Gemini + Groq (free-tier AI, automatic fallback).

## 1. Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
touch .env                       # then add the keys listed in section 2 and the table in section 6 (KEY=value, one per line)
python manage.py migrate
python manage.py runserver
```
Open http://localhost:8000 → Sign up. Without `DATABASE_URL` it uses SQLite. Run tests: `python manage.py test`.

## 2. Get the free AI keys

You need **at least one**. With both, if the first fails or hits its limit the app automatically falls back to the other.

### Gemini (recommended primary — free, no card)
1. Go to https://aistudio.google.com/apikey and sign in with a Google account.
2. Click **Create API key** (pick or create a Google Cloud project when asked).
3. Copy the key → `GEMINI_API_KEY`.
4. `GEMINI_MODEL=gemini-flash-latest` (alias for the current Flash model). Free-tier limits change; see AI Studio → *Rate limits*. If the model name stops working, choose a current Flash model from https://ai.google.dev/gemini-api/docs/models.

> Free-tier prompts may be used by Google to improve its products — keep that in mind for real users' financial data.

### Groq (free backup — no card)
Groq is a different company from xAI's Grok; it runs open models (e.g. gpt-oss, Llama) on a free tier.
1. Go to https://console.groq.com and sign up (Google, GitHub or email).
2. Open https://console.groq.com/keys → **Create API Key** → copy it (starts with `gsk_`; shown once) → `GROQ_API_KEY`.
3. Default model is `openai/gpt-oss-120b`; if it is retired, pick a current one from the console's *Models* page and set `GROQ_MODEL`.

> The Groq slot uses the OpenAI-style API, so `GROQ_BASE_URL`/`GROQ_MODEL` can also point to another compatible provider (e.g. xAI Grok with `https://api.x.ai/v1`, which is paid).

`AI_PRIMARY=gemini` (or `groq`) picks which runs first. `AI_DAILY_LIMIT=60` caps AI calls per user per 24h so one user can't burn your free quota.

## 3. Neon database (free)
1. https://neon.tech → sign up → **Create project** (Singapore is closest to Bangladesh).
2. Click **Connect** → enable **Connection pooling** → copy the connection string (`postgresql://…-pooler….neon.tech/neondb?sslmode=require`). This is `DATABASE_URL`.
3. Create the tables **once**, from your computer:
   ```bash
   DATABASE_URL='postgresql://…' python manage.py migrate
   ```
   Re-run after any future model change.

## 4. Google sign-in (optional)
1. https://console.cloud.google.com → create a project → **APIs & Services → OAuth consent screen** (External; app name + your email).
2. **Credentials → Create credentials → OAuth client ID → Web application**.
3. Authorized redirect URIs (add both):
   - `http://localhost:8000/auth/google/callback/`
   - `https://YOUR-APP.vercel.app/auth/google/callback/` (plus your custom domain, if any)
4. Copy Client ID / secret → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. Without them the Google button is hidden.

## 5. Push to GitHub
```bash
git add -A && git commit -m "HishabAI: initial version"
gh repo create hishabai --private --source=. --push     # or create on github.com, then git remote add + git push
```
`.env` is git-ignored — never commit keys.

## 6. Deploy on Vercel
1. https://vercel.com/new → import the GitHub repo. Django is auto-detected via `manage.py`; no `vercel.json` needed, and static files are collected and served from the CDN automatically.
2. Add **Environment Variables**:

| Name | Value |
|---|---|
| `SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DATABASE_URL` | Neon pooled connection string |
| `GEMINI_API_KEY` / `GROQ_API_KEY` | from section 2 |
| `AI_PRIMARY`, `GEMINI_MODEL`, `GROQ_MODEL`, `GROQ_BASE_URL` | optional (defaults: `gemini`, `gemini-flash-latest`, `openai/gpt-oss-120b`, `https://api.groq.com/openai/v1`) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | optional |
| `CSRF_TRUSTED_ORIGINS`, `ALLOWED_HOSTS` | only for a custom domain, e.g. `https://expense.example.com`, `expense.example.com` |

   (Alternative: add the **Neon** integration from Vercel's Marketplace; it injects `DATABASE_URL`.)
3. **Deploy**, open the URL, sign up and try *Smart Entry*: `আজ দুপুরে খাবারে ২৫০ টাকা, রিকশা ৬০ টাকা`.

Never set `DEBUG=1` in production.

## 7. Before charging real customers
- **Payments are a demo.** `PAYMENTS_DEMO_MODE=1` (default) activates a plan instantly without taking money. Integrate a gateway (SSLCommerz, bKash, Nagad…), activate plans from its verified webhook, then set `PAYMENTS_DEMO_MODE=0`.
- Free AI tiers have rate limits and no SLA; use a paid key for real traffic.
- Add a privacy policy: transaction text goes to Google/xAI for AI features. Chat/insights only send anonymous aggregates (no name or email).

## Accounts, banks & logos
`core/catalog.py` lists Bangladeshi mobile wallets (bKash, Nagad, Rocket, Upay…), ~60 banks and card networks. Logos are in `core/static/core/logos/`
(fetched from each provider's own site or Wikipedia). They are trademarks of their owners and are used only to identify the provider. Providers without a
logo file show an initial badge; drop a `<key>.png` (128×128) into that folder to add or replace one.

## Reminders
Debts due within 3 days (or overdue) show in the bell icon, the dashboard and the Debts menu badge. These are in-app reminders; email/SMS/push are not included.

## Project map
`core/ai.py` providers + prompts · `core/services.py` analytics · `core/views.py` pages & API · `core/templates/core/` UI · `core/tests.py`
