from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def normalize_redis_url(url: object) -> str:
    """Ensure rediss URLs include an explicit ssl_cert_reqs setting.

    Hosted Redis providers often return ``rediss://`` URLs without the query
    parameter Celery expects. Defaulting to ``CERT_NONE`` keeps the URL usable
    for managed TLS endpoints without requiring local CA bundle setup.
    """

    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in url):
        return ""
    if any(char.isspace() for char in url):
        return ""

    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and port <= 0:
        return ""
    if parsed.fragment:
        return ""

    if not parsed.hostname:
        return ""

    if parsed.scheme == "redis":
        return url
    if parsed.scheme != "rediss":
        return ""

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    ssl_cert_reqs = next(
        (
            value.strip()
            for key, value in query_pairs
            if key == "ssl_cert_reqs" and value.strip()
        ),
        "CERT_NONE",
    )
    normalized_pairs = [
        (key, value) for key, value in query_pairs if key != "ssl_cert_reqs"
    ]
    normalized_pairs.append(("ssl_cert_reqs", ssl_cert_reqs))
    return urlunparse(parsed._replace(query=urlencode(normalized_pairs)))
