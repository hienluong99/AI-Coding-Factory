"""Custom exceptions for delivery and Pull Request packaging."""

class DeliveryError(Exception):
    """Base exception for delivery and PR operations."""
    pass


class QualityGateNotPassedError(DeliveryError):
    """Raised when attempting to deliver/PR a task whose Quality Gate has not passed 100%."""
    pass


class ScopeViolationError(DeliveryError):
    """Raised when diff violates file scope boundaries."""
    pass
