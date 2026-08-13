"""Hosted semantic embeddings for the curated TripSync activity catalog.

Embeddings are optional enrichment: catalog publishing and trip planning remain
usable without them, falling back to deterministic text retrieval when the API
or Supabase is unavailable.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from typing import Any

import numpy as np
from dotenv import load_dotenv
from openai import APIConnectionError, AuthenticationError, OpenAIError, RateLimitError

from src.models import Activity
from src.supabase_store import is_configured, select, upsert_many


SUPABASE_EMBEDDINGS_TABLE = "catalog_activity_embeddings"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"

load_dotenv()


class EmbeddingUnavailable(RuntimeError):
    """Raised when optional hosted semantic retrieval cannot be used."""


def activity_embedding_text(activity: Activity) -> str:
    """Return the stable catalog text whose meaning should be searched."""

    return "\n".join(
        [
            f"Name: {activity.name}",
            f"Category: {activity.category.replace('_', ' ')}",
            f"Tags: {', '.join(activity.category_tags)}",
            f"Interests: {', '.join(activity.interests)}",
            f"Description: {activity.description}",
        ]
    )


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _model_name() -> str:
    return os.getenv("TRIPSYNC_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL).strip()


def _configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip()) and is_configured()


def _embed_texts(texts: Sequence[str]) -> list[list[float]]:
    if not texts:
        return []
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise EmbeddingUnavailable("OPENAI_API_KEY is not configured")
    try:
        response = OpenAI().embeddings.create(model=_model_name(), input=list(texts))
    except (AuthenticationError, RateLimitError, APIConnectionError, OpenAIError) as error:
        raise EmbeddingUnavailable(type(error).__name__) from error
    return [list(item.embedding) for item in response.data]


def _stored_rows() -> dict[str, dict[str, Any]]:
    if not _configured():
        raise EmbeddingUnavailable("hosted embedding storage is not configured")
    try:
        return {
            str(row["activity_id"]): row
            for row in select(SUPABASE_EMBEDDINGS_TABLE, order="updated_at")
        }
    except Exception as error:  # noqa: BLE001 - optional service boundary
        raise EmbeddingUnavailable("embedding storage is unavailable") from error


def ensure_activity_embeddings(activities: Sequence[Activity]) -> dict[str, list[float]]:
    """Return fresh embeddings, creating/updating only changed catalog records."""

    stored = _stored_rows()
    model = _model_name()
    documents = {activity.id: activity_embedding_text(activity) for activity in activities}
    missing_ids = [
        activity_id
        for activity_id, document in documents.items()
        if stored.get(activity_id, {}).get("content_hash") != _content_hash(document)
        or stored.get(activity_id, {}).get("embedding_model") != model
    ]
    if missing_ids:
        vectors = _embed_texts([documents[activity_id] for activity_id in missing_ids])
        rows = [
            {
                "activity_id": activity_id,
                "embedding_model": model,
                "content_hash": _content_hash(documents[activity_id]),
                "embedding": vector,
            }
            for activity_id, vector in zip(missing_ids, vectors, strict=True)
        ]
        try:
            upsert_many(SUPABASE_EMBEDDINGS_TABLE, rows, conflict="activity_id")
        except Exception as error:  # noqa: BLE001 - optional service boundary
            raise EmbeddingUnavailable("could not save catalog embeddings") from error
        stored.update({row["activity_id"]: row for row in rows})

    result: dict[str, list[float]] = {}
    for activity_id in documents:
        embedding = stored.get(activity_id, {}).get("embedding")
        if not isinstance(embedding, list) or not embedding:
            raise EmbeddingUnavailable("catalog embedding is missing")
        result[activity_id] = [float(value) for value in embedding]
    return result


def semantic_similarity_scores(
    activities: Sequence[Activity], query: str
) -> dict[str, float]:
    """Return cosine similarities between one trip query and catalog activities."""

    if not query.strip():
        return {}
    activity_vectors = ensure_activity_embeddings(activities)
    query_vector = np.asarray(_embed_texts([query])[0], dtype=float)
    query_norm = np.linalg.norm(query_vector)
    if not query_norm:
        raise EmbeddingUnavailable("query embedding was empty")

    scores: dict[str, float] = {}
    for activity_id, vector in activity_vectors.items():
        activity_vector = np.asarray(vector, dtype=float)
        denominator = query_norm * np.linalg.norm(activity_vector)
        if denominator:
            scores[activity_id] = float(np.dot(query_vector, activity_vector) / denominator)
    return scores
