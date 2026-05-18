class BankingAppError(Exception):
    """Base project exception."""


class ConfigurationError(BankingAppError):
    """Raised when configuration is invalid."""


class BankAdapterError(BankingAppError):
    """Raised when bank adapter fails."""


class ExportError(BankingAppError):
    """Raised when exporter fails."""
