"""requests sessions with a default timeout and safe retries."""

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_TIMEOUT = 30


class _TimeoutAdapter(HTTPAdapter):
    def __init__(self, timeout: float, **kwargs: Any):
        self.timeout = timeout
        super().__init__(**kwargs)

    def send(self, request: requests.PreparedRequest, **kwargs: Any) -> requests.Response:  # type: ignore[override]
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        return super().send(request, **kwargs)


def session(*, timeout: float = DEFAULT_TIMEOUT, retries: int = 3) -> requests.Session:
    """A Session that times out and retries connection errors and 502/503/504.

    Only GET and HEAD are retried: a retried write could apply twice.
    """
    retry = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        raise_on_status=False,
    )
    s = requests.Session()
    adapter = _TimeoutAdapter(timeout, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s
