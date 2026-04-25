"""Embedding service: Voyage AI / OpenAI embedding wrapper with batching and backoff.

Voyage AI is called via direct HTTP (requests) rather than the voyageai SDK to avoid
a known import failure in gunicorn's threaded environment.  voyageai's __init__.py
patches sys.modules["pkg_resources"] as a gunicorn workaround, which can cause
ModuleNotFoundError inside ThreadPoolExecutor workers when setuptools is absent.

NOTE (billing): No per-call billing cap gate here — embed_texts/embed_single accept
raw text with no tenant context.  Callers (enrichment_service, rag_service, routes/ai)
enforce the cap gate before reaching this layer.  Billing cost recording for embedding
calls flows through llm_provider.complete() → billing_service.record_usage() for LLM
calls, and embedding costs are tracked when the calling service records via
billing_service.record_usage() directly.
"""

import logging
import time

import requests  # always available — in requirements.txt

logger = logging.getLogger(__name__)

from config import Config

BATCH_SIZE = 128  # Voyage supports up to 128 texts per batch
MAX_RETRIES = 4
BASE_DELAY = 65.0  # Start at 65s — Voyage rate limit window is 60s; first retry must cross boundary
MAX_DELAY = 65.0   # Keep flat; exponential growth unhelpful here


def _resolve_byok_keys(tenant_id: int | None) -> dict | None:
    """Look up BYOK embedding keys for a tenant. Returns dict or None."""
    if not tenant_id:
        return None
    try:
        from services.billing_service import get_byok_keys
        byok = get_byok_keys(tenant_id)
        if byok and (byok.get("voyage") or byok.get("openai")):
            return byok
    except Exception:
        pass
    return None


def _check_demo_byok(tenant_id: int | None, byok_keys: dict | None) -> None:
    """Raise ValueError if the tenant is a demo tenant without a BYOK key for the active provider.

    Called by the public embed entry points immediately after _resolve_byok_keys().
    Demo tenants must supply their own key for whichever provider is currently active —
    having a BYOK key for a different provider does not satisfy the requirement.
    """
    if not tenant_id:
        return

    # Even if byok_keys is not None, verify the active provider's key is present.
    # A demo tenant might have e.g. an OpenAI BYOK key but not a Voyage key; if the
    # active provider is "voyage" the call would fall through to Config.VOYAGE_API_KEY.
    provider = Config.EMBEDDING_PROVIDER
    if byok_keys is not None:
        active_key = byok_keys.get("voyage") if provider == "voyage" else byok_keys.get("openai")
        if active_key:
            return  # Tenant has a BYOK key for the active provider — OK

    # No BYOK key for the active provider — check if this is a demo tenant or if
    # DEMO_MODE is globally enabled (in which case all tenants must use their own keys).
    try:
        from services.billing_service import is_demo_tenant
        if is_demo_tenant(tenant_id) or Config.DEMO_MODE:
            raise ValueError("must configure BYOK API keys to use embedding features")
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Demo tenant check failed for tenant %s: %s", tenant_id, exc)


def embed_texts(texts: list[str], *, tenant_id: int | None = None) -> list[list[float]]:
    """Batch embed a list of texts.

    Splits into batches and retries with exponential backoff on rate limits.
    Returns list of embedding vectors in same order as input.

    When tenant_id is provided, checks for BYOK Voyage/OpenAI key.
    """
    if not texts:
        return []

    byok_keys = _resolve_byok_keys(tenant_id)
    _check_demo_byok(tenant_id, byok_keys)
    all_embeddings = []

    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        embeddings = _embed_batch(batch, byok_keys=byok_keys)
        all_embeddings.extend(embeddings)

    return all_embeddings


def embed_single(text: str, *, tenant_id: int | None = None) -> list[float]:
    """Embed a single text (for query-time embedding)."""
    byok_keys = _resolve_byok_keys(tenant_id)
    _check_demo_byok(tenant_id, byok_keys)
    return _embed_batch([text], byok_keys=byok_keys)[0]


def embed_single_with_usage(text: str, *, tenant_id: int | None = None) -> tuple[list[float], int]:
    """Embed a single text and return (embedding, total_tokens).

    Uses the same batch path but captures Voyage/OpenAI usage metadata.
    When tenant_id is provided, checks for BYOK Voyage/OpenAI key.
    """
    byok_keys = _resolve_byok_keys(tenant_id)
    _check_demo_byok(tenant_id, byok_keys)
    return _embed_batch_with_usage([text], byok_keys=byok_keys)


def _embed_batch(texts: list[str], byok_keys: dict | None = None) -> list[list[float]]:
    """Embed a single batch with retry logic. Routes to Voyage or OpenAI based on config."""
    provider = Config.EMBEDDING_PROVIDER

    for attempt in range(MAX_RETRIES):
        try:
            if provider == "voyage":
                return _voyage_embed(texts, byok_keys=byok_keys)
            else:
                return _openai_embed(texts, byok_keys=byok_keys)
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = any(kw in error_str for kw in ["429", "rate limit", "rate_limit", "reduced rate", "too many requests"])
            if is_rate_limit:
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                logger.warning("Rate limited [%s], retrying in %.1fs (attempt %d/%d)", error_str[:120], delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
            else:
                logger.error("Embedding API error: %s", e)
                raise

    raise RuntimeError(f"Embedding batch failed after {MAX_RETRIES} retries")


def _embed_batch_with_usage(texts: list[str], byok_keys: dict | None = None) -> tuple[list[float], int]:
    """Embed a single batch and return (first_embedding, total_tokens).

    Same retry logic as _embed_batch but captures usage metadata from the API response.
    """
    provider = Config.EMBEDDING_PROVIDER

    for attempt in range(MAX_RETRIES):
        try:
            if provider == "voyage":
                return _voyage_embed_with_usage(texts, byok_keys=byok_keys)
            else:
                return _openai_embed_with_usage(texts, byok_keys=byok_keys)
        except Exception as e:
            error_str = str(e).lower()
            is_rate_limit = any(kw in error_str for kw in ["429", "rate limit", "rate_limit", "reduced rate", "too many requests"])
            if is_rate_limit:
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                logger.warning("Rate limited [%s], retrying in %.1fs (attempt %d/%d)", error_str[:120], delay, attempt + 1, MAX_RETRIES)
                time.sleep(delay)
            else:
                logger.error("Embedding API error: %s", e)
                raise

    raise RuntimeError(f"Embedding batch failed after {MAX_RETRIES} retries")


def _voyage_embed(texts: list[str], byok_keys: dict | None = None) -> list[list[float]]:
    """Embed via Voyage AI REST API using requests (no voyageai SDK needed)."""
    api_key = (byok_keys or {}).get("voyage") or Config.VOYAGE_API_KEY
    resp = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"input": texts, "model": Config.EMBEDDING_MODEL},
        timeout=60,
    )
    resp.raise_for_status()
    items = sorted(resp.json()["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in items]


def _voyage_embed_with_usage(texts: list[str], byok_keys: dict | None = None) -> tuple[list[float], int]:
    """Embed via Voyage AI and return (first_embedding, total_tokens)."""
    api_key = (byok_keys or {}).get("voyage") or Config.VOYAGE_API_KEY
    resp = requests.post(
        "https://api.voyageai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"input": texts, "model": Config.EMBEDDING_MODEL},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    items = sorted(body["data"], key=lambda x: x["index"])
    total_tokens = body.get("usage", {}).get("total_tokens", 0)
    return items[0]["embedding"], total_tokens


def _openai_embed(texts: list[str], byok_keys: dict | None = None) -> list[list[float]]:
    """Embed via OpenAI API."""
    try:
        import openai
    except ImportError:
        raise ImportError("openai package is not installed")
    api_key = (byok_keys or {}).get("openai") or Config.OPENAI_API_KEY
    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(
        model=Config.EMBEDDING_MODEL,
        input=texts,
        dimensions=Config.EMBEDDING_DIMENSIONS,
    )
    return [item.embedding for item in response.data]


def _openai_embed_with_usage(texts: list[str], byok_keys: dict | None = None) -> tuple[list[float], int]:
    """Embed via OpenAI API and return (first_embedding, total_tokens)."""
    try:
        import openai
    except ImportError:
        raise ImportError("openai package is not installed")
    api_key = (byok_keys or {}).get("openai") or Config.OPENAI_API_KEY
    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(
        model=Config.EMBEDDING_MODEL,
        input=texts,
        dimensions=Config.EMBEDDING_DIMENSIONS,
    )
    total_tokens = response.usage.total_tokens if response.usage else 0
    return response.data[0].embedding, total_tokens
