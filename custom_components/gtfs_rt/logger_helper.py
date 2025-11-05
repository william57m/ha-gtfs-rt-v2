"""Logging utilities module."""

import logging
from typing import List, Callable


class LoggerHelper:
    """Helper class for consistent logging formatting."""

    @staticmethod
    def log_with_indent(
        logger_func: Callable, data: List[str], indent_level: int
    ) -> None:
        """Log data with consistent indentation."""
        indents = "   " * indent_level
        message = f"{indents}{': '.join(str(x) for x in data)}"
        logger_func(message)

    @staticmethod
    def log_info(
        data: List[str], indent_level: int = 0, logger: logging.Logger = None
    ) -> None:
        """Log info message with indentation."""
        if logger is None:
            logger = logging.getLogger(__name__)
        LoggerHelper.log_with_indent(logger.info, data, indent_level)

    @staticmethod
    def log_error(
        data: List[str], indent_level: int = 0, logger: logging.Logger = None
    ) -> None:
        """Log error message with indentation."""
        if logger is None:
            logger = logging.getLogger(__name__)
        LoggerHelper.log_with_indent(logger.error, data, indent_level)

    @staticmethod
    def log_debug(
        data: List[str], indent_level: int = 0, logger: logging.Logger = None
    ) -> None:
        """Log debug message with indentation (space separated for debug)."""
        if logger is None:
            logger = logging.getLogger(__name__)
        indents = "   " * indent_level
        message = f"{indents}{' '.join(str(x) for x in data)}"
        logger.debug(message)
