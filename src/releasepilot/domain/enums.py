"""Domain enumerations for ReleasePilot."""

from __future__ import annotations

from enum import StrEnum


class ChangeCategory(StrEnum):
    """Classification category for a change item."""

    BREAKING = "breaking"
    SECURITY = "security"
    FEATURE = "feature"
    IMPROVEMENT = "improvement"
    BUGFIX = "bugfix"
    PERFORMANCE = "performance"
    DEPRECATION = "deprecation"
    DOCUMENTATION = "documentation"
    INFRASTRUCTURE = "infrastructure"
    REFACTOR = "refactor"
    OTHER = "other"

    @property
    def display_label(self) -> str:
        """Human-readable label for rendering."""
        return _CATEGORY_LABELS[self]

    @property
    def sort_order(self) -> int:
        """Deterministic display order — higher-impact categories first."""
        return _CATEGORY_ORDER[self]


_CATEGORY_LABELS: dict[ChangeCategory, str] = {
    ChangeCategory.BREAKING: "⚠️ Breaking Changes",
    ChangeCategory.SECURITY: "🔒 Security",
    ChangeCategory.FEATURE: "✨ New Features",
    ChangeCategory.IMPROVEMENT: "🔧 Improvements",
    ChangeCategory.BUGFIX: "🐛 Bug Fixes",
    ChangeCategory.PERFORMANCE: "⚡ Performance",
    ChangeCategory.DEPRECATION: "📦 Deprecations",
    ChangeCategory.DOCUMENTATION: "📝 Documentation",
    ChangeCategory.INFRASTRUCTURE: "🏗️ Infrastructure",
    ChangeCategory.REFACTOR: "♻️ Refactoring",
    ChangeCategory.OTHER: "📋 Other Changes",
}

_CATEGORY_ORDER: dict[ChangeCategory, int] = {
    ChangeCategory.BREAKING: 0,
    ChangeCategory.SECURITY: 1,
    ChangeCategory.FEATURE: 2,
    ChangeCategory.IMPROVEMENT: 3,
    ChangeCategory.BUGFIX: 4,
    ChangeCategory.PERFORMANCE: 5,
    ChangeCategory.DEPRECATION: 6,
    ChangeCategory.DOCUMENTATION: 7,
    ChangeCategory.INFRASTRUCTURE: 8,
    ChangeCategory.REFACTOR: 9,
    ChangeCategory.OTHER: 10,
}


class Audience(StrEnum):
    """Target audience for release notes."""

    TECHNICAL = "technical"
    USER = "user"
    SUMMARY = "summary"
    CHANGELOG = "changelog"
    CUSTOMER = "customer"
    EXECUTIVE = "executive"
    NARRATIVE = "narrative"
    CUSTOMER_NARRATIVE = "customer-narrative"


class OutputFormat(StrEnum):
    """Output format for rendered release notes."""

    MARKDOWN = "markdown"
    PLAINTEXT = "plaintext"
    JSON = "json"
    PDF = "pdf"
    DOCX = "docx"


class Importance(StrEnum):
    """Importance level of a change item."""

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
    NOISE = "noise"
