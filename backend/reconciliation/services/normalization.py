"""Reference and name normalisation (brief §6, PRD §6.1, D-11, D-12). Pure functions."""
import re

LEGAL_SUFFIXES = frozenset({"LLC", "LP", "LTD", "INC"})

_NOT_ALPHANUMERIC = re.compile(r"[^A-Z0-9]")
_NON_ALPHANUMERIC_RUN = re.compile(r"[^A-Z0-9]+")


def normalize_reference(text):
    """Uppercase and remove everything except letters and digits: 'cc-1003' -> 'CC1003'."""
    return _NOT_ALPHANUMERIC.sub("", text.upper())


def normalize_name(text, drop_suffix=True):
    """
    Uppercase, replace everything except letters and digits with a space, collapse spaces, and
    (for investor names) drop one trailing LLC/LP/LTD/INC. TRUST is kept. Returns a tuple of words.
    Descriptions are normalised with drop_suffix=False (D-12).
    """
    words = tuple(_NON_ALPHANUMERIC_RUN.sub(" ", text.upper()).split())
    if drop_suffix and words and words[-1] in LEGAL_SUFFIXES:
        words = words[:-1]
    return words


def name_found(name_words, description_words):
    """True if name_words appear as a contiguous run of whole words in description_words."""
    size = len(name_words)
    if size == 0:
        return False  # an empty name never matches (D-12)
    return any(
        description_words[start:start + size] == name_words
        for start in range(len(description_words) - size + 1)
    )
