from __future__ import annotations


class ProviderError(Exception):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class ProviderBadRequest(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass
