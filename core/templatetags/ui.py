from decimal import Decimal

from django import template

register = template.Library()


@register.simple_tag
def tr(text):
    """{% tr "Dashboard" %} -> the Bangla text when the user's language is Bangla."""
    from ..i18n import tr as _tr

    return _tr(text)


def _group_bd(n):
    """Bangladeshi digit grouping: 1,00,000."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


@register.filter
def taka(value, decimals=0):
    try:
        v = Decimal(str(value or 0))
    except Exception:
        return "৳0"
    neg = v < 0
    v = abs(v)
    whole = int(v)
    out = _group_bd(whole)
    if decimals:
        out += "." + f"{v - whole:.{int(decimals)}f}".split(".")[1]
    return ("-" if neg else "") + "৳" + out


@register.filter
def pct_cap(value):
    try:
        return min(100, max(0, int(value)))
    except (TypeError, ValueError):
        return 0
