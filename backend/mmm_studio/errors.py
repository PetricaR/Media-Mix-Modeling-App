"""Error messages that are safe to show to users: no local paths, no stack frames."""

from __future__ import annotations

import re

# Absolute POSIX paths (e.g. /app/.venv/lib/..., /tmp/mmm_studio_x/...) and Windows drive paths
_PATH = re.compile(r"(?:(?<=\s)|(?<=[\'\"(=])|^)(?:/[\w.@+-]+){2,}/?|[A-Za-z]:\\[^\s'\"]+")
MAX_LEN = 500


def public_error(exc: BaseException) -> str:
    """`Type: message` with file-system paths replaced by `<path>` and the length capped."""
    text = f"{type(exc).__name__}: {exc}"
    text = _PATH.sub("<path>", text)
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 1] + "…"
