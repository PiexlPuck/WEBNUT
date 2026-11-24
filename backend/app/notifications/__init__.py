"""Extensible notification provider abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class NotificationMessage:
    """Structured notification payload passed to NotificationProvider instances."""

    event_type: str
    title: str
    body: str
    priority: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryResult:
    """Outcome of delivering a notification through a provider."""

    provider: str
    success: bool
    status_code: int | None = None
    error: str | None = None
    response_body: str | None = None


class NotificationProvider(ABC):
    """Abstract base class for pluggable notification providers."""

    provider_type: str

    @abstractmethod
    async def send(self, message: NotificationMessage) -> DeliveryResult:
        """Deliver a notification message asynchronously."""
