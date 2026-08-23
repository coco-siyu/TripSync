"""Validate real attraction websites and discover useful visitor links.

Wikidata can suggest a URL, but it is never treated as visitor information.
TripSync opens the destination organization's actual website, validates the
redirected domain and page identity, and saves only links found on that site.
Failures are deliberately non-fatal so scheduled ingestion can retry later.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


MAX_HTML_BYTES = 2_000_000
TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
GENERIC_NAME_WORDS = {
    "and", "archaeological", "art", "basilica", "castle", "cathedral",
    "center", "centre", "church", "city", "gallery", "garden", "gardens",
    "historic", "museum", "museums", "national", "of", "palace", "park",
    "regional", "saint", "st", "the", "tower",
}
VISIT_TERMS = (
    "plan your visit", "visitor information", "visit", "visita", "visiting",
    "prepare your visit", "practical information", "informazioni",
)
HOURS_TERMS = (
    "opening hours", "opening times", "hours", "orari", "horaires",
    "horarios", "days and hours", "days & hours",
)
TICKET_TERMS = (
    "official tickets", "buy tickets", "book tickets", "tickets", "ticketing",
    "biglietti", "billets", "entradas", "booking", "reservation",
)
VISIT_LINK_EXCLUSIONS = (
    "event", "events", "news", "notizie", "capitolo", "parrocchia",
)
HOURS_LINK_EXCLUSIONS = (
    "24 hours", "48 hours", "72 hours", "itinerary", "itineraries",
    "celebration", "celebrations", "celebrazione", "celebrazioni",
    "confession", "confessions", "confessione", "confessioni",
    "liturgy", "liturgical", "liturgia", "liturgiche", "mass", "messe",
    "event", "events", "news", "notizie",
)


@dataclass(frozen=True)
class OfficialSiteDetails:
    """Verified links found on an attraction's own website."""

    official_url: str
    visit_url: str | None = None
    hours_url: str | None = None
    tickets_url: str | None = None


@dataclass(frozen=True)
class OfficialSiteCandidate:
    """One activity and the external URL proposed as its official site."""

    activity_id: str
    activity_name: str
    candidate_url: str


class _OfficialSiteParser(HTMLParser):
    """Collect page identity and same-site navigation without dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta_parts: list[str] = []
        self.canonical_url: str | None = None
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._text_length = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name.casefold(): value or "" for name, value in attrs}
        if tag.casefold() == "title":
            self._in_title = True
        elif tag.casefold() == "meta":
            key = (
                attributes.get("name")
                or attributes.get("property")
                or ""
            ).casefold()
            if key in {"description", "og:title", "og:description", "og:site_name"}:
                self.meta_parts.append(attributes.get("content", ""))
        elif tag.casefold() == "link":
            rel = attributes.get("rel", "").casefold().split()
            if "canonical" in rel and attributes.get("href"):
                self.canonical_url = attributes["href"]
        elif tag.casefold() == "a" and attributes.get("href"):
            self._anchor_href = attributes["href"]
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "title":
            self._in_title = False
        elif tag.casefold() == "a" and self._anchor_href:
            self.links.append(
                (self._anchor_href, " ".join(self._anchor_text).strip())
            )
            self._anchor_href = None
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        if self._in_title:
            self.title_parts.append(normalized)
        if self._anchor_href:
            self._anchor_text.append(normalized)
        if self._text_length < 250_000:
            self.text_parts.append(normalized)
            self._text_length += len(normalized)


def _normalized_words(value: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return re.findall(r"[a-z0-9]+", ascii_value.casefold())


def _safe_public_url(value: str) -> bool:
    """Reject non-web, local, credentialed, and literal private-network URLs."""

    parsed = urlparse(value)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    if parsed.username or parsed.password or hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        return ipaddress.ip_address(hostname).is_global
    except ValueError:
        return True


def _organization_host(value: str) -> str:
    hostname = (urlparse(value).hostname or "").casefold().rstrip(".")
    return hostname.removeprefix("www.")


def _same_organization_domain(first: str, second: str) -> bool:
    first_host = _organization_host(first)
    second_host = _organization_host(second)
    return bool(
        first_host
        and second_host
        and (
            first_host == second_host
            or first_host.endswith(f".{second_host}")
            or second_host.endswith(f".{first_host}")
        )
    )


def _page_matches_activity(activity_name: str, parser: _OfficialSiteParser) -> bool:
    identity_text = " ".join(
        [*parser.title_parts, *parser.meta_parts, *parser.text_parts]
    )
    page_words = set(_normalized_words(identity_text))
    name_words = _normalized_words(activity_name)
    distinctive = [
        word
        for word in name_words
        if len(word) >= 4 and word not in GENERIC_NAME_WORDS
    ]
    if distinctive:
        return any(word in page_words for word in distinctive)
    return all(word in page_words for word in name_words if len(word) >= 3)


def _link_score(text: str, href: str, terms: tuple[str, ...]) -> int:
    normalized_text = " ".join(_normalized_words(text))
    normalized_href = " ".join(_normalized_words(href))
    score = 0
    for term in terms:
        normalized_term = " ".join(_normalized_words(term))
        if not normalized_term:
            continue
        term_weight = len(normalized_term.split())
        if normalized_text == normalized_term:
            score = max(score, 60 + term_weight)
        elif normalized_term in normalized_text:
            score = max(score, 40 + term_weight)
        if normalized_term in normalized_href:
            score = max(score, 20 + term_weight)
    return score


def _link_is_excluded(
    text: str, href: str, exclusions: tuple[str, ...]
) -> bool:
    haystack = " ".join(
        [" ".join(_normalized_words(text)), " ".join(_normalized_words(href))]
    )
    return any(
        " ".join(_normalized_words(term)) in haystack
        for term in exclusions
    )


def _best_link(
    links: Iterable[tuple[str, str]],
    base_url: str,
    terms: tuple[str, ...],
    *,
    exclusions: tuple[str, ...] = (),
    conflicting_href_terms: tuple[str, ...] = (),
) -> str | None:
    candidates: list[tuple[int, int, str]] = []
    normalized_base = base_url.rstrip("/#")
    for raw_href, text in links:
        resolved = urljoin(base_url, raw_href)
        if not _safe_public_url(resolved):
            continue
        if not _same_organization_domain(base_url, resolved):
            continue
        if resolved.rstrip("/#") == normalized_base:
            continue
        if exclusions and _link_is_excluded(text, resolved, exclusions):
            continue
        normalized_href = " ".join(_normalized_words(resolved))
        href_has_target_term = any(
            " ".join(_normalized_words(term)) in normalized_href
            for term in terms
        )
        href_has_conflicting_term = any(
            " ".join(_normalized_words(term)) in normalized_href
            for term in conflicting_href_terms
        )
        if href_has_conflicting_term and not href_has_target_term:
            continue
        score = _link_score(text, resolved, terms)
        if score:
            candidates.append((-score, len(resolved), resolved))
    return min(candidates)[2] if candidates else None


def _fetch_html(url: str, *, timeout: int = 12) -> tuple[str, str]:
    if not _safe_public_url(url):
        raise ValueError("official-site candidate must be a public HTTP(S) URL")
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "TripSync official visitor-link verifier (educational project)",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL is validated above
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read(MAX_HTML_BYTES + 1)
    except HTTPError as error:
        if error.code in TRANSIENT_HTTP_STATUSES:
            raise RuntimeError(f"official site returned temporary HTTP {error.code}") from error
        raise ValueError(f"official site returned HTTP {error.code}") from error
    except (TimeoutError, URLError) as error:
        raise RuntimeError("official site could not be reached") from error
    if not _safe_public_url(final_url) or not _same_organization_domain(url, final_url):
        raise ValueError("official-site candidate redirected to a different organization")
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise ValueError("official-site candidate did not return HTML")
    if len(body) > MAX_HTML_BYTES:
        raise ValueError("official-site page is too large to inspect safely")
    return final_url, body.decode(charset, errors="replace")


SiteFetcher = Callable[[str], tuple[str, str]]


def verify_official_site(
    activity_name: str,
    candidate_url: str,
    *,
    fetcher: SiteFetcher = _fetch_html,
) -> OfficialSiteDetails | None:
    """Open and validate the real site, returning only same-domain links."""

    try:
        final_url, html = fetcher(candidate_url)
    except (RuntimeError, ValueError):
        return None
    if not _safe_public_url(final_url):
        return None
    if not _same_organization_domain(candidate_url, final_url):
        return None
    parser = _OfficialSiteParser()
    try:
        parser.feed(html)
    except (TypeError, ValueError):
        return None
    if not _page_matches_activity(activity_name, parser):
        return None

    canonical_url = (
        urljoin(final_url, parser.canonical_url)
        if parser.canonical_url
        else final_url
    )
    if not _safe_public_url(canonical_url) or not _same_organization_domain(
        final_url, canonical_url
    ):
        canonical_url = final_url
    return OfficialSiteDetails(
        official_url=canonical_url,
        visit_url=_best_link(
            parser.links,
            canonical_url,
            VISIT_TERMS,
            exclusions=VISIT_LINK_EXCLUSIONS,
        ),
        hours_url=_best_link(
            parser.links,
            canonical_url,
            HOURS_TERMS,
            exclusions=HOURS_LINK_EXCLUSIONS,
            conflicting_href_terms=TICKET_TERMS,
        ),
        tickets_url=_best_link(parser.links, canonical_url, TICKET_TERMS),
    )


def verify_official_sites(
    candidates: Iterable[OfficialSiteCandidate],
    *,
    verifier: Callable[[str, str], OfficialSiteDetails | None] = verify_official_site,
    max_workers: int = 6,
) -> dict[str, OfficialSiteDetails]:
    """Validate independent websites concurrently with a small worker bound."""

    rows = [candidate for candidate in candidates if candidate.candidate_url]
    if not rows:
        return {}
    results: dict[str, OfficialSiteDetails] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 12))) as executor:
        futures = {
            executor.submit(
                verifier, candidate.activity_name, candidate.candidate_url
            ): candidate.activity_id
            for candidate in rows
        }
        for future in as_completed(futures):
            try:
                details = future.result()
            except Exception:  # noqa: BLE001 - one site must not stop a catalog batch
                details = None
            if details is not None:
                results[futures[future]] = details
    return results


def enrich_candidate_official_sites(
    candidates: Iterable[dict[str, object]],
    *,
    batch_verifier: Callable[
        [Iterable[OfficialSiteCandidate]], dict[str, OfficialSiteDetails]
    ] = verify_official_sites,
) -> list[dict[str, object]]:
    """Validate candidate URLs and attach only real-site visitor links."""

    rows = [dict(candidate) for candidate in candidates]
    site_candidates: list[OfficialSiteCandidate] = []
    key_by_position: dict[int, str] = {}
    for position, candidate in enumerate(rows):
        name = str(candidate.get("name", "")).strip()
        candidate_url = str(candidate.get("official_url", "")).strip()
        if not name or not candidate_url:
            continue
        candidate_key = str(
            candidate.get("wikidata_id") or f"candidate-{position}"
        )
        key_by_position[position] = candidate_key
        site_candidates.append(
            OfficialSiteCandidate(candidate_key, name, candidate_url)
        )
    verified = batch_verifier(site_candidates)
    checked_at = datetime.now(UTC).isoformat()
    for position, candidate in enumerate(rows):
        details = verified.get(key_by_position.get(position, ""))
        if details is None:
            candidate["official_site_verified"] = False
            continue
        candidate.update(
            {
                "official_url": details.official_url,
                "official_site_verified": True,
                "official_visit_url": details.visit_url,
                "official_hours_url": details.hours_url,
                "official_tickets_url": details.tickets_url,
                "official_site_checked_at": checked_at,
            }
        )
    return rows
