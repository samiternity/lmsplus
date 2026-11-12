"""Suppress legacy scraper diagnostics only inside connector document operations."""

from contextvars import ContextVar
from functools import wraps
import logging


_private_operation = ContextVar('lms_mcp_private_document_operation', default=False)


class PrivateDocumentFilter(logging.Filter):
    def filter(self, record):
        return not _private_operation.get()


def private_document_operation(function):
    @wraps(function)
    def run(*args, **kwargs):
        marker = _private_operation.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _private_operation.reset(marker)
    return run


def install_scraper_filters():
    for name in ('app.services.lms_scraper_service', 'lms', 'lms.errors', 'lms.performance',
                 'lms.security', 'lms.activity'):
        logger = logging.getLogger(name)
        if not any(isinstance(f, PrivateDocumentFilter) for f in logger.filters):
            logger.addFilter(PrivateDocumentFilter())
