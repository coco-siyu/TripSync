"""Tests for optional hosted catalog embeddings without live network calls."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.embeddings import _embed_texts, activity_embedding_text, ensure_activity_embeddings
from src.models import Activity


def make_activity() -> Activity:
    return Activity(
        id="rome_gallery", name="Rome Gallery", city="Rome", country="Italy",
        category="museum", category_tags=["renaissance"], interests=["art"],
        walking_level="low", budget_level="moderate", duration_hours=2,
        indoor=True, family_friendly=True, accessibility_notes="Step-free entry.",
        reservation_required=False, description="A painting collection.",
        source_url="https://example.com/gallery",
    )


class HostedEmbeddingTests(unittest.TestCase):
    def test_activity_document_contains_grounded_search_fields(self) -> None:
        document = activity_embedding_text(make_activity())
        self.assertIn("Rome Gallery", document)
        self.assertIn("renaissance", document)
        self.assertIn("painting collection", document)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False)
    @patch("src.embeddings.OpenAI")
    def test_embeds_texts_with_hosted_client(self, openai_client) -> None:
        openai_client.return_value.embeddings.create.return_value.data = [
            type("Embedding", (), {"embedding": [0.1, 0.2]})()
        ]

        self.assertEqual(_embed_texts(["quiet cultural afternoon"]), [[0.1, 0.2]])
        openai_client.return_value.embeddings.create.assert_called_once_with(
            model="text-embedding-3-small",
            input=["quiet cultural afternoon"],
        )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test"}, clear=False)
    @patch("src.embeddings.is_configured", return_value=True)
    @patch("src.embeddings.select")
    @patch("src.embeddings._embed_texts")
    @patch("src.embeddings.upsert_many")
    def test_creates_only_missing_activity_embedding(
        self, upsert_many, embed_texts, select, _configured
    ) -> None:
        select.return_value = []
        embed_texts.return_value = [[0.1, 0.2]]

        result = ensure_activity_embeddings([make_activity()])

        self.assertEqual(result, {"rome_gallery": [0.1, 0.2]})
        self.assertEqual(embed_texts.call_count, 1)
        upsert_many.assert_called_once()
