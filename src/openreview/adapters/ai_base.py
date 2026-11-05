"""Base protocols and data models for local and remote AI review providers per TRD Section 3.4."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from openreview.schemas.models import Confidence, Severity


class AIStatus(str, Enum):
    """Execution status of an AI model provider call."""

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass(frozen=True)
class CodeContextBlock:
    """A bounded segment of code from a changed file provided as context."""

    path: str
    start_line: int
    end_line: int
    content: str
    language: str = ""

    def __post_init__(self) -> None:
        if self.start_line < 1:
            raise ValueError(f"start_line must be >= 1, got {self.start_line}")
        if self.end_line < self.start_line:
            raise ValueError(f"end_line ({self.end_line}) cannot be less than start_line ({self.start_line})")
        if not self.path.strip():
            raise ValueError("path cannot be empty")


@dataclass(frozen=True)
class AIReviewRequest:
    """Execution request sent to an AI model provider."""

    blocks: list[CodeContextBlock]
    model: str
    system_prompt: str = ""
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: int = 30
    extra_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model identifier cannot be empty")
        if self.temperature < 0.0 or self.temperature > 2.0:
            raise ValueError(f"temperature must be between 0.0 and 2.0, got {self.temperature}")
        if self.timeout_seconds <= 0:
            raise ValueError(f"timeout_seconds must be > 0, got {self.timeout_seconds}")


@dataclass(frozen=True)
class AIReviewSuggestion:
    """An unvalidated suggestion produced by an AI review provider."""

    path: str
    title: str
    explanation: str
    start_line: int | None = None
    end_line: int | None = None
    severity: Severity = Severity.MEDIUM
    confidence: Confidence = Confidence.MEDIUM
    recommendation: str | None = None
    rule_id: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Suggestion title cannot be empty")
        if not self.path.strip():
            raise ValueError("Suggestion path cannot be empty")


@dataclass(frozen=True)
class AIReviewResponse:
    """Normalized response returned by an AI review provider."""

    provider_name: str
    model_name: str
    status: AIStatus
    duration_seconds: float
    suggestions: list[AIReviewSuggestion] = field(default_factory=list)
    raw_response: str | None = None
    error_message: str | None = None

    @property
    def is_successful(self) -> bool:
        """True if the provider returned valid suggestions or completed without error."""
        return self.status == AIStatus.SUCCESS


@runtime_checkable
class ModelProvider(Protocol):
    """Protocol that all AI review model providers (Ollama, etc.) must implement."""

    @property
    def name(self) -> str:
        """Unique provider identifier (e.g. 'ollama', 'mock')."""
        ...

    def is_available(self) -> bool:
        """Check whether the provider service/daemon is running and reachable."""
        ...

    def generate_suggestions(self, request: AIReviewRequest) -> AIReviewResponse:
        """Query the model with bounded code context and return review suggestions."""
        ...
