# src/cuea/errors.py


class CueaError(Exception):
    """Base exception for cuea library."""

class AuthError(CueaError):
    """Authentication or authorization failure (401/403)."""

class RateLimit(CueaError):
    """Rate limit reached (429)."""

class TransportError(CueaError):
    """Network or transport-level error (timeouts, connection issues, 5xx)."""

class NotFound(CueaError):
    """Requested resource not found (404)."""

class BadRequest(CueaError):
    """Client error, bad request (4xx other than 401/403/404)."""
