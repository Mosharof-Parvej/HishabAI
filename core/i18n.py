"""Tiny English -> Bangla UI dictionary. A string missing here simply shows in English.

Used by the {% tr "..." %} template tag and by views for strings built in Python. Covers the shared shell
(navigation, profile menu, footer), the dashboard and the profile page; other pages are still English.
"""
from django.utils.translation import get_language

BN = {
    # navigation & menu
    "Dashboard": "ড্যাশবোর্ড", "Transactions": "লেনদেন", "Accounts": "অ্যাকাউন্ট", "Debts": "ধার-দেনা",
    "Categories & Budgets": "ক্যাটাগরি ও বাজেট", "Ask AI": "AI-কে জিজ্ঞেস করুন", "Profile": "প্রোফাইল", "Language": "ভাষা",
    "Smart Entry": "স্মার্ট এন্ট্রি", "Upgrade Plan": "প্ল্যান আপগ্রেড করুন", "Log in": "লগ ইন",
    "About us": "আমাদের সম্পর্কে", "About & contact": "আমাদের সম্পর্কে ও যোগাযোগ", "Admin panel": "অ্যাডমিন প্যানেল", "Theme": "থিম",
    "Light": "লাইট", "Dark": "ডার্ক", "System": "সিস্টেম", "Reminders": "রিমাইন্ডার", "All debts": "সব ধার-দেনা",
    "Turn on notifications": "নোটিফিকেশন চালু করুন", "Notifications on": "নোটিফিকেশন চালু ✓", "Notifications blocked in browser settings": "ব্রাউজার সেটিংসে নোটিফিকেশন বন্ধ",
    "No reminders right now. Add a pay-back date to a debt to get one.": "এখন কোনো রিমাইন্ডার নেই। রিমাইন্ডার পেতে কোনো ধারে ফেরত দেওয়ার তারিখ দিন।", "Subscription": "সাবস্ক্রিপশন", "Log out": "লগ আউট", "Help": "সহায়তা", "Privacy": "গোপনীয়তা",
    "Made by": "তৈরি করেছেন", "Free trial": "ফ্রি ট্রায়াল", "plan": "প্ল্যান", "until": "পর্যন্ত", "days left": "দিন বাকি", "of": "/",
    "Repayment reminders": "পরিশোধের রিমাইন্ডার", "No repayments due soon.": "শীঘ্রই পরিশোধের কিছু নেই।",
    "Manage debts & reminders": "ধার ও রিমাইন্ডার দেখুন", "You owe": "আপনি দেনা", "Owes you": "আপনার পাওনা",
    "overdue": "বকেয়া", "Due today": "আজ শেষ দিন", "in": "আর",
    "Your free trial has ended.": "আপনার ফ্রি ট্রায়াল শেষ হয়েছে।",
    "Your records are safe, but AI features are paused.": "আপনার তথ্য নিরাপদ আছে, তবে AI সুবিধা বন্ধ।", "Choose a plan": "প্ল্যান বেছে নিন",
    # dashboard
    "Good morning": "সুপ্রভাত", "Good afternoon": "শুভ অপরাহ্ন", "Good evening": "শুভ সন্ধ্যা", "Good night": "শুভ রাত্রি",
    "Tell me what you spent or earned — বাংলা, English, Banglish or any language": "আপনি কী খরচ বা আয় করেছেন বলুন — বাংলা, English, Banglish বা যেকোনো ভাষায়",
    "Add manually": "নিজে যোগ করুন", "Add with AI": "AI দিয়ে যোগ করুন", "← Prev": "← আগের", "Next →": "পরের →",
    "Income": "আয়", "Expense": "খরচ", "Balance this month": "এই মাসের ব্যালেন্স", "All-time balance": "সর্বমোট ব্যালেন্স",
    "Saved": "সঞ্চয়:", "Overspent": "অতিরিক্ত খরচ:", "of income": "(আয়ের)", "Manage": "পরিচালনা",
    "Owed to me": "আমার পাওনা", "I owe": "আমার দেনা", "Where your money went": "আপনার টাকা কোথায় গেল",
    "No expenses yet this month.": "এই মাসে এখনও কোনো খরচ নেই।", "Day by day": "দিনে দিনে", "Budgets": "বাজেট",
    "Set budgets": "বাজেট ঠিক করুন", "No budgets yet — set a monthly limit per category.": "এখনও কোনো বাজেট নেই — প্রতিটি ক্যাটাগরির জন্য মাসিক সীমা দিন।",
    "AI insight": "AI ইনসাইট", "Analysing your spending…": "আপনার খরচ বিশ্লেষণ চলছে…", "Recent transactions": "সাম্প্রতিক লেনদেন",
    "See all": "সব দেখুন", "Day": "দিন", "Week": "সপ্তাহ", "Month": "মাস", "No transactions in this period.": "এই সময়ে কোনো লেনদেন নেই।",
    "Add a few transactions and I'll share insights here.": "কিছু লেনদেন যোগ করুন, এখানে ইনসাইট দেখাব।",
    "AI today:": "আজ AI:", "% used": "% ব্যবহৃত", "requests": "অনুরোধ",
    # profile page
    "Your profile and data": "আপনার প্রোফাইল ও তথ্য", "Email": "ইমেইল", "Name": "নাম", "Save": "সেভ করুন",
    "Your data": "আপনার তথ্য", "Download everything you've recorded.": "আপনি যা কিছু লিখেছেন সব ডাউনলোড করুন।",
    "Export all transactions (CSV)": "সব লেনদেন এক্সপোর্ট (CSV)", "Delete account": "অ্যাকাউন্ট মুছুন",
    "Language for the app and AI replies": "অ্যাপ ও AI-এর উত্তরের ভাষা",
}


def tr(text, lang=None):
    return BN.get(text, text) if (lang or get_language()) == "bn" else text
