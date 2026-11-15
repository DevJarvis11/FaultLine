"""Sensitive-data redaction applied before every LLM call and every stored record."""

from faultline.redaction.rules import redact, redact_text

__all__ = ["redact", "redact_text"]
