"""Request locale, shared by every router that returns generated text.

Content language follows the client's Accept-Language header rather than a
stored preference: the same account may read on a Vietnamese phone and an
English one, and the prompt templates are keyed by locale.
"""

from fastapi import Request

SUPPORTED = ("vi", "en")
DEFAULT = "en"


def request_locale(request: Request) -> str:
    lang = (request.headers.get("Accept-Language") or DEFAULT)[:2].lower()
    return lang if lang in SUPPORTED else DEFAULT
