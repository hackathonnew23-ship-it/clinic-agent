"""Errors raised by the consent / OTP / payment steps. Each has a machine .code."""
from __future__ import annotations


class FlowError(Exception):
    def __init__(self, code: str, message: str, **extra) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra
