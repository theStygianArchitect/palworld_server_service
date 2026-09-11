"""Changelog parser and semantic release notes engine.

Provides in-memory cached parsing of Keep a Changelog formatted markdown files,
supporting structured categories, version resolution, and graceful fallback
degradation in compliance with 3 AM standards and 10.00/10 Pylint score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__ as APP_VERSION
from app.api.schemas import (
    ChangelogCategoryItem,
    ChangelogRelease,
    ChangelogResponse,
)
from app.core.logger import log

RE_UNRELEASED_HEADER: re.Pattern[str] = re.compile(r"^##\s+\[?[Uu]nreleased\]?\s*$", re.IGNORECASE)
RE_RELEASE_HEADER: re.Pattern[str] = re.compile(
    r"^##\s+\[?(?:v|V)?(?P<version>[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:-[a-zA-Z0-9_.-]+)?)\]?"
    r"(?:\s*-\s*(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}))?\s*$"
)
RE_CATEGORY_HEADER: re.Pattern[str] = re.compile(r"^###\s+(?P<category>[A-Za-z0-9_\- ]+)\s*$")
RE_BULLET_ITEM: re.Pattern[str] = re.compile(r"^\s*[*-]\s+(?P<item>.+)$")


def _build_fallback_response(version: str) -> ChangelogResponse:
    """Constructs a graceful degradation fallback response.

    Args:
        version: Target semantic version string.

    Returns:
        ChangelogResponse: Fallback release object with informational notice.
    """
    fallback_release = ChangelogRelease(
        version=version,
        date=None,
        categories=[
            ChangelogCategoryItem(
                category="Information",
                items=["Changelog details are currently unavailable on this host."],
            )
        ],
        raw_body="Changelog details are currently unavailable on this host.",
    )
    return ChangelogResponse(
        current_version=version,
        releases=[fallback_release],
        unreleased=[],
    )


@dataclass
class _ReleaseState:
    """In-flight builder state tracking a parsed release section.

    Attributes:
        version: Semantic version string.
        date: Optional release date string (YYYY-MM-DD).
        categories: Map of category name to bulleted change item strings.
        raw_lines: Accumulator of raw markdown lines in the release section.
    """

    version: str = ""
    date: str | None = None
    categories: dict[str, list[str]] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)

    def build_release(self) -> ChangelogRelease | None:
        """Serializes current state into a ChangelogRelease instance if version is present.

        Returns:
            ChangelogRelease | None: Populated release object or None if empty.
        """
        if not self.version:
            return None
        cat_items = [ChangelogCategoryItem(category=c_name, items=items) for c_name, items in self.categories.items()]
        return ChangelogRelease(
            version=self.version,
            date=self.date,
            categories=cat_items,
            raw_body="\n".join(self.raw_lines).strip(),
        )

    def reset(self, version: str = "", date: str | None = None) -> None:
        """Resets builder state for a new release section.

        Args:
            version: New semantic version string.
            date: Optional release date string.
        """
        self.version = version
        self.date = date
        self.categories = {}
        self.raw_lines = []


class ChangelogParser:
    """Parser and in-memory cache for Keep a Changelog formatted markdown files.

    Attributes:
        custom_path (Path | None): Explicit filesystem path override if provided.
    """

    def __init__(self, custom_path: Path | None = None) -> None:
        """Initializes the ChangelogParser.

        Args:
            custom_path: Optional explicit filesystem path to CHANGELOG.md.
        """
        self.custom_path = custom_path
        self._cached_path: Path | None = None
        self._cached_mtime: float | None = None
        self._cached_response: ChangelogResponse | None = None

    def resolve_path(self, repo_root: Path | None = None) -> Path:
        """Resolves the candidate path to CHANGELOG.md across expected locations.

        Args:
            repo_root: Optional explicit repository root directory path.

        Returns:
            Path: Best candidate path to CHANGELOG.md.
        """
        if self.custom_path is not None:
            return self.custom_path

        if repo_root is not None:
            return Path(repo_root) / "CHANGELOG.md"

        candidates = [
            Path(__file__).resolve().parent.parent.parent / "CHANGELOG.md",
            Path(__file__).resolve().parent.parent / "CHANGELOG.md",
            Path("/opt/palworld-web-manager/CHANGELOG.md"),
            Path("/var/lib/palmanager/repo/CHANGELOG.md"),
            Path.cwd() / "CHANGELOG.md",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def _flush_active_release(
        self,
        releases: list[ChangelogRelease],
        state: _ReleaseState,
    ) -> None:
        """Flushes an in-flight release object into the releases accumulator if present.

        Args:
            releases: Target accumulator list of ChangelogRelease objects.
            state: Mutable release builder state.
        """
        rel = state.build_release()
        if rel is not None:
            releases.append(rel)

    def _parse_lines(
        self,
        lines: list[str],
    ) -> tuple[list[ChangelogRelease], list[ChangelogCategoryItem]]:
        """Parses markdown lines into release list and unreleased category items.

        Args:
            lines: List of text lines from the changelog markdown file.

        Returns:
            tuple: Pair containing (releases, unreleased_categories).
        """
        releases: list[ChangelogRelease] = []
        unreleased_cats: dict[str, list[str]] = {}
        active_section = "preamble"
        current_cat: str | None = None
        state = _ReleaseState()

        for line in lines:
            line_str = line.rstrip("\r\n")

            if RE_UNRELEASED_HEADER.match(line_str):
                self._flush_active_release(releases, state)
                active_section = "unreleased"
                current_cat = None
                continue

            rel_match = RE_RELEASE_HEADER.match(line_str)
            if rel_match:
                self._flush_active_release(releases, state)
                active_section = "release"
                state.reset(version=rel_match.group("version"), date=rel_match.group("date"))
                current_cat = None
                continue

            cat_match = RE_CATEGORY_HEADER.match(line_str)
            if cat_match:
                current_cat = cat_match.group("category").strip()
                if active_section == "unreleased":
                    unreleased_cats.setdefault(current_cat, [])
                elif active_section == "release":
                    state.categories.setdefault(current_cat, [])
                    state.raw_lines.append(line_str)
                continue

            bullet_match = RE_BULLET_ITEM.match(line_str)
            if bullet_match:
                item_text = bullet_match.group("item").strip()
                target_cat = current_cat or ("Planned" if active_section == "unreleased" else "Other")
                if active_section == "unreleased":
                    unreleased_cats.setdefault(target_cat, []).append(item_text)
                elif active_section == "release":
                    state.categories.setdefault(target_cat, []).append(item_text)
                    state.raw_lines.append(line_str)
                continue

            if active_section == "release" and line_str.strip():
                state.raw_lines.append(line_str)

        self._flush_active_release(releases, state)

        unreleased_items = [
            ChangelogCategoryItem(category=cat_name, items=items) for cat_name, items in unreleased_cats.items()
        ]
        return releases, unreleased_items

    def parse(self, repo_root: Path | None = None) -> ChangelogResponse:
        """Parses CHANGELOG.md, returning cached response if modification timestamp is unchanged.

        Args:
            repo_root: Optional repository root path containing CHANGELOG.md.

        Returns:
            ChangelogResponse: Parsed changelog or fallback degradation object.
        """
        target_path = self.resolve_path(repo_root)
        if not target_path.is_file():
            log.warning("Changelog file not found at '%s'. Returning fallback response.", target_path)
            return _build_fallback_response(APP_VERSION)

        try:
            current_mtime = target_path.stat().st_mtime
        except OSError as err:
            log.warning("Filesystem error stating changelog at '%s': %s", target_path, err)
            return _build_fallback_response(APP_VERSION)

        if (
            self._cached_path == target_path
            and self._cached_mtime == current_mtime
            and self._cached_response is not None
        ):
            return self._cached_response

        try:
            raw_text = target_path.read_text(encoding="utf-8")
            releases, unreleased = self._parse_lines(raw_text.splitlines())
        except OSError as err:
            log.warning("Filesystem error reading changelog from '%s': %s", target_path, err)
            return _build_fallback_response(APP_VERSION)
        except UnicodeDecodeError as err:
            log.warning("Encoding error reading changelog from '%s': %s", target_path, err)
            return _build_fallback_response(APP_VERSION)
        except ValueError as err:
            log.warning("Value error parsing changelog from '%s': %s", target_path, err)
            return _build_fallback_response(APP_VERSION)

        if not releases:
            log.warning("No release entries discovered in changelog at '%s'. Returning fallback.", target_path)
            return _build_fallback_response(APP_VERSION)

        response = ChangelogResponse(
            current_version=APP_VERSION,
            releases=releases,
            unreleased=unreleased,
        )
        self._cached_path = target_path
        self._cached_mtime = current_mtime
        self._cached_response = response
        return response


_DEFAULT_PARSER: ChangelogParser = ChangelogParser()


def get_changelog(repo_root: Path | None = None) -> ChangelogResponse:
    """Retrieves and returns the parsed changelog response.

    Uses an in-memory cached parser keyed by file modification time (st_mtime).
    Degrades gracefully with fallback telemetry if the changelog file is missing
    or cannot be parsed.

    Args:
        repo_root: Optional explicit repository root directory path.

    Returns:
        ChangelogResponse: Parsed changelog metadata and releases.
    """
    return _DEFAULT_PARSER.parse(repo_root=repo_root)
