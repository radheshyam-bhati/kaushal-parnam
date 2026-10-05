"""Template helpers for Kaushal Parinam."""

from __future__ import annotations

from django import template

register = template.Library()


@register.filter(name='fields')
def fields(form, names):
    """``form|fields:"name dob gender"`` -> the bound fields, in order.

    Django templates cannot loop over a comma-separated tuple
    (``{% for f in a, b %}`` is a syntax error), and grouping a form section by
    hand-written markup means writing the same field block five times. This keeps
    the markup in one place.
    """
    if not form:
        return []
    return [form[name] for name in names.split() if name in form.fields]


@register.filter(name='attr')
def attr(obj, name):
    return getattr(obj, name, '')


@register.filter(name='get_item')
def get_item(dictionary, key):
    """Dict lookup by variable, for small lookup tables in templates."""
    if dictionary is None:
        return None
    return dictionary.get(key)


@register.filter(name='percent_of')
def percent_of(value, total):
    try:
        return round(100.0 * float(value) / float(total), 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0

@register.filter(name='humanise')
def humanise(value):
    """Turn a snake_case code into readable words.

    Django 6 removed the builtin ``replace`` filter, so the two-argument version
    is reimplemented here rather than reaching for a third-party library.
    """
    return str(value or '').replace('_', ' ')
