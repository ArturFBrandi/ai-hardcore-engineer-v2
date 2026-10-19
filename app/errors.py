"""Application error hierarchy.

Every error carries a stable machine code and an HTTP status; the API layer translates them into
the uniform ``ErrorResponse`` envelope without leaking internals.
"""

from __future__ import annotations


class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    public_detail: str = "An internal error occurred."

    def __init__(self, detail: str | None = None) -> None:
        super().__init__(detail or self.public_detail)
        self.detail = detail or self.public_detail


class ConfigurationError(AppError):
    code = "configuration_error"
    status_code = 500
    public_detail = "Service misconfigured."


class AuthenticationError(AppError):
    code = "unauthorized"
    status_code = 401
    public_detail = "Authentication required."


class AuthorizationError(AppError):
    code = "forbidden"
    status_code = 403
    public_detail = "You are not allowed to perform this action."


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404
    public_detail = "Resource not found."


class ConflictError(AppError):
    code = "conflict"
    status_code = 409
    public_detail = "The resource is busy or in a conflicting state."


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    public_detail = "Too many requests."

    def __init__(self, retry_after: int, detail: str | None = None) -> None:
        super().__init__(detail)
        self.retry_after = retry_after


class DependencyUnavailableError(AppError):
    code = "dependency_unavailable"
    status_code = 503
    public_detail = "A required dependency is unavailable. Please retry later."


class ProviderError(AppError):
    """LLM provider failure (after retries / breaker open)."""

    code = "llm_provider_error"
    status_code = 503
    public_detail = "The language model provider is unavailable."

    def __init__(self, detail: str | None = None, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.retryable = retryable


class RetrievalError(AppError):
    code = "retrieval_error"
    status_code = 503
    public_detail = "The knowledge base is unavailable."


class ToolError(AppError):
    code = "tool_error"
    status_code = 500
    public_detail = "A tool failed."

    def __init__(self, detail: str | None = None, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.retryable = retryable


class ExternalSearchError(AppError):
    code = "web_search_error"
    status_code = 503
    public_detail = "External search is unavailable."


class ApprovalRequired(AppError):
    code = "approval_required"
    status_code = 403
    public_detail = "This action requires human approval."


class EscalationRequired(AppError):
    code = "escalation_required"
    status_code = 200
    public_detail = "Escalation required."
