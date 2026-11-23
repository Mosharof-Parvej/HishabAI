"""Providers a user can pick when creating an account: Bangladeshi banks, mobile wallets, card networks.

Logos live in static/core/logos/<key>.png (fetched from each provider's own site icon). Logos are
trademarks of their owners and are used only to identify the provider. Entries whose logo file is
missing automatically fall back to a coloured initial badge.
"""
import os
from pathlib import Path

LOGO_DIR = Path(__file__).resolve().parent / "static" / "core" / "logos"

# (key, display name, kind, website domain used to fetch the logo)
_RAW = [
    # ---- Mobile wallets (MFS) ----
    ("bkash", "bKash", "wallet", "bkash.com"),
    ("nagad", "Nagad", "wallet", "nagad.com.bd"),
    ("rocket", "Rocket (DBBL)", "wallet", "rocket.com.bd"),
    ("upay", "Upay", "wallet", "upaybd.com"),
    ("tap", "tap (Trust Axiata Pay)", "wallet", "tap.com.bd"),
    ("mcash", "mCash (Islami Bank)", "wallet", "mycash.com.bd"),
    ("cellfin", "Cellfin (Islami Bank)", "wallet", "cellfinbd.com"),
    ("surecash", "SureCash", "wallet", "surecash.net"),
    ("okwallet", "OK Wallet", "wallet", "okwallet.com.bd"),
    ("ipay", "iPay", "wallet", "ipay.com.bd"),
    ("pathaopay", "Pathao Pay", "wallet", "pathao.com"),
    ("meghnapay", "Meghna Pay", "wallet", "meghnabank.com.bd"),
    # ---- State-owned banks ----
    ("sonali", "Sonali Bank", "bank", "sonalibank.com.bd"),
    ("janata", "Janata Bank", "bank", "janatabank-bd.com"),
    ("agrani", "Agrani Bank", "bank", "agranibank.org"),
    ("rupali", "Rupali Bank", "bank", "rupalibank.org"),
    ("basic", "BASIC Bank", "bank", "basicbanklimited.com"),
    ("bdbl", "Bangladesh Development Bank", "bank", "bdbl.com.bd"),
    ("krishi", "Bangladesh Krishi Bank", "bank", "krishibank.org.bd"),
    ("rakub", "Rajshahi Krishi Unnayan Bank", "bank", "rakub.org.bd"),
    ("probashi", "Probashi Kallyan Bank", "bank", "pkb.gov.bd"),
    ("avub", "Ansar-VDP Unnayan Bank", "bank", "avub.gov.bd"),
    ("pallisanchay", "Palli Sanchay Bank", "bank", "pallisanchaybank.gov.bd"),
    # ---- Private commercial banks ----
    ("abbank", "AB Bank", "bank", "abbl.com"),
    ("alarafah", "Al-Arafah Islami Bank", "bank", "al-arafahbank.com"),
    ("bankasia", "Bank Asia", "bank", "bankasia-bd.com"),
    ("bcbl", "Bangladesh Commerce Bank", "bank", "bcblbd.com"),
    ("brac", "BRAC Bank", "bank", "bracbank.com"),
    ("city", "The City Bank", "bank", "thecitybank.com"),
    ("citizens", "Citizens Bank", "bank", "thecitizensbank.com"),
    ("community", "Community Bank Bangladesh", "bank", "communitybankbd.com"),
    ("dhaka", "Dhaka Bank", "bank", "dhakabankltd.com"),
    ("dbbl", "Dutch-Bangla Bank", "bank", "dutchbanglabank.com"),
    ("ebl", "Eastern Bank (EBL)", "bank", "ebl.com.bd"),
    ("exim", "EXIM Bank", "bank", "eximbankbd.com"),
    ("fsibl", "First Security Islami Bank", "bank", "fsiblbd.com"),
    ("gibl", "Global Islami Bank", "bank", "globalislamibank.com.bd"),
    ("ific", "IFIC Bank", "bank", "ificbank.com.bd"),
    ("ibbl", "Islami Bank Bangladesh", "bank", "islamibankbd.com"),
    ("jamuna", "Jamuna Bank", "bank", "jamunabankbd.com"),
    ("meghna", "Meghna Bank", "bank", "meghnabank.com.bd"),
    ("mercantile", "Mercantile Bank", "bank", "mblbd.com"),
    ("midland", "Midland Bank", "bank", "midlandbankbd.net"),
    ("modhumoti", "Modhumoti Bank", "bank", "modhumotibank.com"),
    ("mtb", "Mutual Trust Bank", "bank", "mtbl.com.bd"),
    ("nbl", "National Bank", "bank", "nblbd.com"),
    ("ncc", "NCC Bank", "bank", "nccbank.com.bd"),
    ("nrb", "NRB Bank", "bank", "nrbbankbd.com"),
    ("nrbc", "NRB Commercial Bank", "bank", "nrbcommercialbank.com"),
    ("nrbglobal", "NRB Global Bank", "bank", "nrbglobalbank.com"),
    ("onebank", "ONE Bank", "bank", "onebank.com.bd"),
    ("padma", "Padma Bank", "bank", "padmabankbd.com"),
    ("premier", "Premier Bank", "bank", "premierbankltd.com"),
    ("prime", "Prime Bank", "bank", "primebank.com.bd"),
    ("pubali", "Pubali Bank", "bank", "pubalibangla.com"),
    ("shahjalal", "Shahjalal Islami Bank", "bank", "shahjalalbank.com.bd"),
    ("sibl", "Social Islami Bank", "bank", "siblbd.com"),
    ("southeast", "Southeast Bank", "bank", "southeastbank.com.bd"),
    ("standardbank", "Standard Bank", "bank", "standardbankbd.com"),
    ("trust", "Trust Bank", "bank", "tblbd.com"),
    ("union", "Union Bank", "bank", "unionbank.com.bd"),
    ("ucb", "United Commercial Bank (UCB)", "bank", "ucb.com.bd"),
    ("uttara", "Uttara Bank", "bank", "uttarabank-bd.com"),
    # ---- Foreign banks operating in Bangladesh ----
    ("scb", "Standard Chartered Bangladesh", "bank", "sc.com"),
    ("hsbc", "HSBC Bangladesh", "bank", "hsbc.com.bd"),
    ("citibank", "Citibank N.A.", "bank", "citi.com"),
    ("ceylon", "Commercial Bank of Ceylon", "bank", "combank.net"),
    ("sbi", "State Bank of India", "bank", "sbi.co.in"),
    ("habib", "Habib Bank", "bank", "hbl.com"),
    ("nbp", "National Bank of Pakistan", "bank", "nbp.com.pk"),
    ("woori", "Woori Bank", "bank", "wooribank.com"),
    ("alfalah", "Bank Alfalah", "bank", "bankalfalah.com"),
    # ---- Card networks ----
    ("visa", "Visa", "card", "visa.com"),
    ("mastercard", "Mastercard", "card", "mastercard.com"),
    ("amex", "American Express", "card", "americanexpress.com"),
    ("unionpay", "UnionPay", "card", "unionpayintl.com"),
    ("jcb", "JCB", "card", "jcb.global"),
    # ---- Cash ----
    ("cash", "Cash", "cash", ""),
]

PROVIDERS = {
    k: {"key": k, "name": n, "kind": kind, "domain": d} for k, n, kind, d in _RAW
}


def has_logo(key):
    return (LOGO_DIR / f"{key}.png").exists() or (LOGO_DIR / f"{key}.svg").exists()


def logo_file(key):
    for ext in ("png", "svg"):
        if (LOGO_DIR / f"{key}.{ext}").exists():
            return f"core/logos/{key}.{ext}"
    return None


def for_kind(kind):
    return [p for p in PROVIDERS.values() if p["kind"] == kind]
