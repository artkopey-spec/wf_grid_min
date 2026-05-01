"""
Custom exceptions for SuperTrend Optimizer.

This module defines all custom exception classes used throughout the application.
"""


class SuperTrendOptimizerError(Exception):
    """Base exception for all SuperTrend Optimizer errors."""
    pass


class DataValidationError(SuperTrendOptimizerError):
    """Raised when data validation fails."""
    pass


class ConfigError(SuperTrendOptimizerError):
    """Raised when configuration is invalid or missing."""
    pass

