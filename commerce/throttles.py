from rest_framework.throttling import ScopedRateThrottle


class AuthThrottle(ScopedRateThrottle):
    scope = "auth"

