"""Pipeline orchestrator.

Wires together all stages of the release notes pipeline:
  Source Collection → Classification → Filtering → Dedup → Grouping → Audience → Rendering

This is the single entry point for generating release notes from settings.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from releasepilot.audience.views import apply_audience
from releasepilot.config.settings import Settings
from releasepilot.domain.models import ChangeItem, ReleaseNotes, ReleaseRange
from releasepilot.pipeline.progress import (
    STAGE_BUILD_RANGE,
    STAGE_CLASSIFYING,
    STAGE_COLLECTING,
    STAGE_COMPOSING,
    STAGE_DEDUPLICATING,
    STAGE_FILTERING,
    STAGE_GROUPING,
    STAGE_RENDERING,
    noop_progress,
)
from releasepilot.processing.classifier import classify
from releasepilot.processing.dedup import deduplicate
from releasepilot.processing.filter import filter_changes
from releasepilot.processing.grouper import (
    extract_breaking_changes,
    extract_highlights,
    group_changes,
)
from releasepilot.rendering.json_renderer import JsonRenderer
from releasepilot.rendering.markdown import MarkdownRenderer
from releasepilot.rendering.plaintext import PlaintextRenderer
from releasepilot.sources.git import GitSourceCollector
from releasepilot.sources.structured import StructuredFileCollector

if TYPE_CHECKING:
    from releasepilot.pipeline.progress import ProgressCallback


class PipelineError(Exception):
    """Raised when the pipeline encounters an unrecoverable error."""


def _compose_title(settings: Settings, fallback: str) -> str:
    """Build the subtitle/title portion (WITHOUT the app name prefix).

    The app name is stored separately on ReleaseRange.app_name so that
    renderers can position it independently (e.g. centered on its own line).
    """
    result = settings.title or fallback

    # Append version suffix if present and not already included
    if settings.version and settings.version not in result:
        result += f" — Version {settings.version}"

    return result


def _repo_name(repo_path: str) -> str:
    """Derive a clean application name from a repository path.

    Examples:
      /home/user/projects/MyApp       → MyApp
      /home/user/projects/my-app.git  → my-app
      .                               → (current directory name)
    """
    from pathlib import Path

    name = Path(repo_path).resolve().name
    if name.endswith(".git"):
        name = name[:-4]
    return name if name and name != "/" else ""


def build_release_range(
    settings: Settings,
    on_progress: ProgressCallback = noop_progress,
) -> ReleaseRange:
    """Construct a ReleaseRange from settings, auto-detecting tags if needed."""
    on_progress(STAGE_BUILD_RANGE)
    from_ref = settings.from_ref
    to_ref = settings.to_ref
    app_name = settings.app_name or _repo_name(settings.repo_path)

    # Date-range mode: no ref detection needed
    if settings.is_date_range:
        if settings.title:
            fallback = settings.title
        elif settings.version:
            fallback = f"Release {settings.version} — since {settings.since_date}"
        else:
            fallback = f"Changes since {settings.since_date}"
        title = _compose_title(settings, fallback)
        return ReleaseRange(
            from_ref=settings.since_date,
            to_ref=settings.branch or to_ref,
            version=settings.version,
            title=title,
            app_name=app_name,
            release_date=date.today() if settings.version else None,
        )

    if not from_ref and not settings.is_file_source:
        git = GitSourceCollector(settings.repo_path)
        from_ref = git.resolve_latest_tag()
        if not from_ref:
            raise PipelineError(
                "No --from ref specified and no tags found. "
                "Use --from <ref> to specify the start of the range, "
                "or try 'releasepilot guide' for an interactive workflow."
            )

    # Ref-based mode
    fallback = settings.title or ""
    title = _compose_title(settings, fallback) if (settings.app_name or settings.title) else settings.title

    return ReleaseRange(
        from_ref=from_ref,
        to_ref=to_ref,
        version=settings.version,
        title=title,
        app_name=app_name,
        release_date=date.today() if settings.version else None,
    )


def collect(
    settings: Settings,
    release_range: ReleaseRange,
    on_progress: ProgressCallback = noop_progress,
) -> list[ChangeItem]:
    """Stage 1: Collect raw change items from configured source."""
    on_progress(STAGE_COLLECTING)
    if settings.is_file_source:
        collector = StructuredFileCollector(settings.source_file)
        return collector.collect(release_range)

    git_collector = GitSourceCollector(settings.repo_path)

    if settings.is_date_range:
        branch = settings.branch or "HEAD"
        return git_collector.collect_by_date(settings.since_date, branch)

    return git_collector.collect(release_range)


def process(
    settings: Settings,
    items: list[ChangeItem],
    on_progress: ProgressCallback = noop_progress,
) -> list[ChangeItem]:
    """Stages 2-4: Classify → Filter → Deduplicate."""
    on_progress(STAGE_CLASSIFYING, f"{len(items)} items")
    items = classify(items)
    on_progress(STAGE_FILTERING, f"{len(items)} items")
    items = filter_changes(items, settings.filter)
    on_progress(STAGE_DEDUPLICATING, f"{len(items)} items")
    items = deduplicate(items)
    return items


class PipelineStats:
    """Tracks item counts through each pipeline stage for transparency."""

    __slots__ = (
        "raw", "after_filter", "after_dedup", "final",
        "category_counts", "contributor_count", "scopes",
        "first_commit_date", "last_commit_date",
        "effective_branch", "effective_date_range",
    )

    def __init__(self) -> None:
        self.raw = 0
        self.after_filter = 0
        self.after_dedup = 0
        self.final = 0
        self.category_counts: dict[str, int] = {}
        self.contributor_count: int = 0
        self.scopes: tuple[str, ...] = ()
        self.first_commit_date: str = ""
        self.last_commit_date: str = ""
        self.effective_branch: str = ""
        self.effective_date_range: str = ""

    @property
    def filtered_out(self) -> int:
        return self.raw - self.after_filter

    @property
    def dedup_removed(self) -> int:
        return self.after_filter - self.after_dedup

    def summary(self) -> str:
        return (
            f"{self.raw} collected → {self.filtered_out} filtered → "
            f"{self.dedup_removed} deduplicated → {self.final} final"
        )

    def detailed_summary(self) -> str:
        """Return a multi-line summary with category breakdown."""
        lines = [self.summary()]
        if self.effective_branch:
            lines.append(f"Branch: {self.effective_branch}")
        if self.effective_date_range:
            lines.append(f"Date range: {self.effective_date_range}")
        if self.first_commit_date:
            lines.append(f"First commit: {self.first_commit_date}")
        if self.last_commit_date:
            lines.append(f"Last commit: {self.last_commit_date}")
        if self.contributor_count:
            lines.append(f"Contributors: {self.contributor_count}")
        if self.scopes:
            lines.append(f"Components: {', '.join(self.scopes[:10])}")
        if self.category_counts:
            parts = [f"{cat}: {n}" for cat, n in sorted(self.category_counts.items()) if n > 0]
            if parts:
                lines.append(f"Categories: {', '.join(parts)}")
        return "\n".join(lines)


def process_with_stats(
    settings: Settings,
    items: list[ChangeItem],
    on_progress: ProgressCallback = noop_progress,
) -> tuple[list[ChangeItem], PipelineStats]:
    """Like process() but also returns transparency stats."""
    stats = PipelineStats()
    stats.raw = len(items)

    on_progress(STAGE_CLASSIFYING, f"{len(items)} items")
    items = classify(items)
    on_progress(STAGE_FILTERING, f"{len(items)} items")
    items = filter_changes(items, settings.filter)
    stats.after_filter = len(items)

    on_progress(STAGE_DEDUPLICATING, f"{len(items)} items")
    items = deduplicate(items)
    stats.after_dedup = len(items)
    stats.final = len(items)

    # Enrich stats with category breakdown, contributors, scopes
    cat_counts: dict[str, int] = {}
    authors: set[str] = set()
    scope_set: set[str] = set()
    earliest: str = ""
    latest: str = ""
    for item in items:
        cat_counts[item.category.value] = cat_counts.get(item.category.value, 0) + 1
        for a in item.authors:
            authors.add(a)
        if item.scope:
            scope_set.add(item.scope)
        # Track first/last commit dates from timestamp.
        # ISO 8601 YYYY-MM-DD strings compare lexicographically correctly,
        # so string comparison is safe here.
        if item.timestamp:
            item_date = item.timestamp.strftime("%Y-%m-%d")
            if not earliest or item_date < earliest:
                earliest = item_date
            if not latest or item_date > latest:
                latest = item_date
    stats.category_counts = cat_counts
    stats.contributor_count = len(authors)
    stats.scopes = tuple(sorted(scope_set))
    stats.first_commit_date = earliest
    stats.last_commit_date = latest

    return items, stats


def compose(
    settings: Settings,
    items: list[ChangeItem],
    release_range: ReleaseRange,
    stats: PipelineStats | None = None,
    on_progress: ProgressCallback = noop_progress,
) -> ReleaseNotes:
    """Stage 5: Compose grouped release notes from processed items."""
    on_progress(STAGE_GROUPING, f"{len(items)} items")
    groups = group_changes(items)
    highlights = extract_highlights(items)
    breaking = extract_breaking_changes(items)

    metadata: dict[str, str] = {}
    if stats:
        metadata["raw_count"] = str(stats.raw)
        metadata["filtered_out"] = str(stats.filtered_out)
        metadata["dedup_removed"] = str(stats.dedup_removed)
        metadata["pipeline_summary"] = stats.summary()
        if stats.contributor_count:
            metadata["contributors"] = str(stats.contributor_count)
        if stats.scopes:
            metadata["components"] = ", ".join(stats.scopes[:10])
        if stats.category_counts:
            metadata["category_breakdown"] = "; ".join(
                f"{c}: {n}" for c, n in sorted(stats.category_counts.items()) if n > 0
            )
        if stats.first_commit_date:
            metadata["first_commit_date"] = stats.first_commit_date
        if stats.last_commit_date:
            metadata["last_commit_date"] = stats.last_commit_date
        if stats.effective_branch:
            metadata["effective_branch"] = stats.effective_branch
        if stats.effective_date_range:
            metadata["effective_date_range"] = stats.effective_date_range

    on_progress(STAGE_COMPOSING)
    notes = ReleaseNotes(
        release_range=release_range,
        groups=tuple(groups),
        highlights=tuple(highlights),
        breaking_changes=tuple(breaking),
        total_changes=len(items),
        metadata=metadata,
    )

    return apply_audience(notes, settings.audience)


def render(
    settings: Settings,
    notes: ReleaseNotes,
    on_progress: ProgressCallback = noop_progress,
) -> str:
    """Stage 6: Render release notes to the configured output format.

    Dispatches to audience-specific renderers for executive/narrative audiences
    when combined with PDF/DOCX/Markdown/Plaintext output formats. For the
    standard audiences the base renderers are used.

    Returns a string for text formats (markdown, plaintext, json) and a
    base64-encoded string for binary formats (pdf, docx) so the return type
    stays consistent across the pipeline.
    """
    on_progress(STAGE_RENDERING)
    from releasepilot.domain.enums import Audience, OutputFormat

    fmt = settings.output_format
    audience = settings.audience
    lang = settings.render.language or settings.language or "en"
    accent = settings.render.accent_color

    # --- Executive audience ---------------------------------------------------
    if audience == Audience.EXECUTIVE:
        from releasepilot.audience.executive import compose_executive_brief

        brief = compose_executive_brief(notes)
        if fmt == OutputFormat.PDF:
            from releasepilot.rendering.executive_pdf import ExecutivePdfRenderer

            raw = ExecutivePdfRenderer().render_bytes(brief, lang=lang, accent_color=accent)
            import base64
            return base64.b64encode(raw).decode("ascii")
        if fmt == OutputFormat.DOCX:
            from releasepilot.rendering.executive_docx import ExecutiveDocxRenderer

            raw = ExecutiveDocxRenderer().render_bytes(brief, lang=lang, accent_color=accent)
            import base64
            return base64.b64encode(raw).decode("ascii")
        # Markdown / plaintext / json → executive markdown renderer
        from releasepilot.rendering.executive_md import ExecutiveMarkdownRenderer

        return ExecutiveMarkdownRenderer().render(brief, lang=lang)

    # --- Narrative / Customer-narrative audience ------------------------------
    if audience in (Audience.NARRATIVE, Audience.CUSTOMER_NARRATIVE):
        from releasepilot.audience.narrative import compose_narrative

        customer_facing = audience == Audience.CUSTOMER_NARRATIVE
        brief = compose_narrative(notes, customer_facing=customer_facing)
        if fmt == OutputFormat.PDF:
            from releasepilot.rendering.narrative_pdf import NarrativePdfRenderer

            raw = NarrativePdfRenderer().render_bytes(brief, lang=lang, accent_color=accent)
            import base64
            return base64.b64encode(raw).decode("ascii")
        if fmt == OutputFormat.DOCX:
            from releasepilot.rendering.narrative_docx import NarrativeDocxRenderer

            raw = NarrativeDocxRenderer().render_bytes(brief, lang=lang, accent_color=accent)
            import base64
            return base64.b64encode(raw).decode("ascii")
        if fmt == OutputFormat.PLAINTEXT:
            from releasepilot.rendering.narrative_plain import NarrativePlaintextRenderer

            return NarrativePlaintextRenderer().render(brief)
        # Markdown / json → narrative markdown renderer
        from releasepilot.rendering.narrative_md import NarrativeMarkdownRenderer

        return NarrativeMarkdownRenderer().render(brief, lang=lang)

    # --- Standard audiences (all other) with PDF/DOCX -----------------------
    if fmt == OutputFormat.PDF:
        from releasepilot.rendering.pdf import PdfRenderer

        raw = PdfRenderer().render_bytes(notes, settings.render)
        import base64
        return base64.b64encode(raw).decode("ascii")

    if fmt == OutputFormat.DOCX:
        from releasepilot.rendering.docx_renderer import DocxRenderer

        raw = DocxRenderer().render_bytes(notes, settings.render)
        import base64
        return base64.b64encode(raw).decode("ascii")

    # --- Standard text formats -----------------------------------------------
    renderers = {
        OutputFormat.MARKDOWN: MarkdownRenderer(),
        OutputFormat.PLAINTEXT: PlaintextRenderer(),
        OutputFormat.JSON: JsonRenderer(),
    }

    renderer = renderers[settings.output_format]
    return renderer.render(notes, settings.render)


def generate(settings: Settings, on_progress: ProgressCallback = noop_progress) -> str:
    """Run the full pipeline end-to-end and return the rendered output.

    Returns a string for text formats, base64 for binary formats (PDF, DOCX).
    """
    release_range = build_release_range(settings, on_progress)
    items = collect(settings, release_range, on_progress)
    items, stats = process_with_stats(settings, items, on_progress)
    notes = compose(settings, items, release_range, stats, on_progress)
    return render(settings, notes, on_progress)
