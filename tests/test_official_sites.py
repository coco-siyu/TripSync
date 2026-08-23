"""Tests for automatic validation of actual attraction websites."""

from __future__ import annotations

import unittest

from src.official_sites import (
    OfficialSiteCandidate,
    OfficialSiteDetails,
    enrich_candidate_official_sites,
    verify_official_site,
    verify_official_sites,
)


OFFICIAL_HTML = """
<!doctype html>
<html>
  <head>
    <title>Vatican Museums — official visitor information</title>
    <meta name="description" content="Official Vatican Museums website">
    <link rel="canonical" href="https://www.museivaticani.va/en.html">
  </head>
  <body>
    <a href="/en/visit.html">Plan your visit</a>
    <a href="/en/info/opening-hours.html">Opening hours</a>
    <a href="https://tickets.museivaticani.va/home">Official tickets</a>
    <a href="https://unrelated.example/hours">Opening hours elsewhere</a>
  </body>
</html>
"""


class OfficialSiteTests(unittest.TestCase):
    def test_verifies_real_site_and_keeps_only_organization_links(self) -> None:
        details = verify_official_site(
            "Vatican Museums",
            "https://www.museivaticani.va/",
            fetcher=lambda _: (
                "https://www.museivaticani.va/",
                OFFICIAL_HTML,
            ),
        )

        self.assertEqual(
            details,
            OfficialSiteDetails(
                official_url="https://www.museivaticani.va/en.html",
                visit_url="https://www.museivaticani.va/en/visit.html",
                hours_url="https://www.museivaticani.va/en/info/opening-hours.html",
                tickets_url="https://tickets.museivaticani.va/home",
            ),
        )

    def test_rejects_mismatched_page_and_cross_organization_redirect(self) -> None:
        self.assertIsNone(
            verify_official_site(
                "Vatican Museums",
                "https://www.museivaticani.va/",
                fetcher=lambda _: (
                    "https://www.museivaticani.va/",
                    "<title>Unrelated hotel booking portal</title>",
                ),
            )
        )

    def test_does_not_mistake_itineraries_or_services_for_visitor_hours(self) -> None:
        html = """
        <title>Example Cathedral official website</title>
        <a href="/itineraries/city-48-hours">City in 48 hours</a>
        <a href="/confessions">Confession hours</a>
        <a href="/news/liturgical-celebrations">Liturgical celebration hours</a>
        """

        details = verify_official_site(
            "Example Cathedral",
            "https://cathedral.example.org/",
            fetcher=lambda _: ("https://cathedral.example.org/", html),
        )

        self.assertIsNotNone(details)
        self.assertIsNone(details.hours_url)

    def test_prefers_precise_anchor_label_over_term_hidden_in_shared_url(self) -> None:
        html = """
        <title>Example Museum official website</title>
        <a href="/info-and-tickets#directions">Directions</a>
        <a href="/info-and-tickets#tickets">Tickets</a>
        """

        details = verify_official_site(
            "Example Museum",
            "https://museum.example.org/",
            fetcher=lambda _: ("https://museum.example.org/", html),
        )

        self.assertEqual(
            details.tickets_url,
            "https://museum.example.org/info-and-tickets#tickets",
        )

    def test_omits_duplicate_homepage_and_tickets_only_hours_link(self) -> None:
        html = """
        <title>Example Museum official website</title>
        <a href="/">Tickets</a>
        <a href="/info-and-tickets#tickets">Opening hours</a>
        """

        details = verify_official_site(
            "Example Museum",
            "https://museum.example.org/",
            fetcher=lambda _: ("https://museum.example.org/", html),
        )

        self.assertIsNone(details.hours_url)
        self.assertEqual(
            details.tickets_url,
            "https://museum.example.org/info-and-tickets#tickets",
        )
        self.assertIsNone(
            verify_official_site(
                "Vatican Museums",
                "https://www.museivaticani.va/",
                fetcher=lambda _: ("https://unrelated.example/", OFFICIAL_HTML),
            )
        )

    def test_one_failed_site_does_not_stop_the_batch(self) -> None:
        candidates = [
            OfficialSiteCandidate("good", "Vatican Museums", "https://good.example"),
            OfficialSiteCandidate("bad", "Example Museum", "https://bad.example"),
        ]

        def verifier(name: str, url: str) -> OfficialSiteDetails | None:
            if "bad" in url:
                raise RuntimeError("temporary outage")
            return OfficialSiteDetails(url)

        self.assertEqual(
            verify_official_sites(candidates, verifier=verifier),
            {"good": OfficialSiteDetails("https://good.example")},
        )

    def test_enriches_candidates_without_manual_approval(self) -> None:
        candidates = [
            {
                "wikidata_id": "Q1",
                "name": "Vatican Museums",
                "official_url": "https://www.museivaticani.va/",
            }
        ]
        enriched = enrich_candidate_official_sites(
            candidates,
            batch_verifier=lambda _: {
                "Q1": OfficialSiteDetails(
                    "https://www.museivaticani.va/",
                    hours_url="https://www.museivaticani.va/hours",
                )
            },
        )

        self.assertTrue(enriched[0]["official_site_verified"])
        self.assertEqual(
            enriched[0]["official_hours_url"],
            "https://www.museivaticani.va/hours",
        )


if __name__ == "__main__":
    unittest.main()
