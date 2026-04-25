"""LLM Provider Abstraction — Anthropic primary, OpenAI automatic failover.

Usage:
    from services.llm_provider import complete
    result = complete(
        model=Config.AI_MODEL_ROUTER,
        max_tokens=500,
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "Hello"}],
        tenant_id=1,
        ticket_id=42,
        caller="atlas.engage",
    )
    print(result.text)  # extracted text
"""

import logging
import threading
import time
from dataclasses import dataclass, field

from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cost table — single source of truth in billing_service.COSTS
# OpenAI fallback rates kept here (not in billing_service — only used for failover)
# ---------------------------------------------------------------------------
_OPENAI_COSTS: dict[str, tuple[float, float]] = {
    "gpt-4o-mini":  (0.15,  0.60),
    "gpt-4o":       (5.00, 15.00),
}


def _get_rates(model: str) -> tuple[float, float]:
    """Return (input_rate, output_rate) per 1M tokens for the model."""
    from services.billing_service import COSTS
    billing_entry = COSTS.get(model)
    if billing_entry:
        return (billing_entry["input"] * 1_000_000, billing_entry["output"] * 1_000_000)
    oai = _OPENAI_COSTS.get(model)
    if oai:
        return oai
    logger.warning("No cost rate for model %r — cost will be $0.00", model)
    return (0.0, 0.0)


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated cost in USD."""
    rate_in, rate_out = _get_rates(model)
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def _record_usage(
    tenant_id: int | None,
    ticket_id: int | None,
    provider: str,
    model: str,
    caller: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Fire-and-forget insert into tenant_token_usage. Never raises."""
    if tenant_id is None:
        logger.warning("_record_usage called with tenant_id=None (caller=%s) — skipping", caller)
        return

    def _insert():
        try:
            from models.db import execute as db_execute
            rate_in, rate_out = _get_rates(model)
            cost = (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000
            db_execute(
                """INSERT INTO tenant_token_usage
                   (tenant_id, ticket_id, provider, model, caller,
                    input_tokens, output_tokens, cost_usd,
                    rate_input, rate_output)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [tenant_id, ticket_id, provider, model, caller,
                 input_tokens, output_tokens, cost, rate_in, rate_out],
            )
        except Exception as exc:
            logger.debug("token_usage insert failed: %s", exc)

    threading.Thread(target=_insert, daemon=True).start()


# ---------------------------------------------------------------------------
# Model mapping: Anthropic → OpenAI equivalents (same cost tier)
# ---------------------------------------------------------------------------
_OPENAI_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "gpt-4o-mini",
    "claude-sonnet-4-20250514": "gpt-4o",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class LLMResult:
    text: str = ""
    content: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    stop_reason: str = ""
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------
def log_call(
    caller: str,
    tenant_id: int | None,
    ticket_id: int | None,
    provider: str,
    model: str,
    latency_ms: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    success: bool = True,
    error: str = "",
):
    """Structured log line for any LLM call."""
    model_short = model.split("-202")[0] if "-202" in model else model
    extra = {
        "tenant_id": tenant_id,
        "ticket_id": ticket_id,
        "duration_ms": int(latency_ms),
        "llm_provider": provider,
        "llm_model": model_short,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_caller": caller,
    }
    if success:
        logger.info("LLM %s %s %d/%d tokens %dms", caller, model_short, tokens_in, tokens_out, latency_ms, extra=extra)
    else:
        extra["llm_error"] = error
        logger.error("LLM %s %s FAILED: %s", caller, model_short, error, extra=extra)


# ---------------------------------------------------------------------------
# BYOK-aware client factory (used by rag_service and other direct callers)
# ---------------------------------------------------------------------------
def get_anthropic_client(tenant_id: int | None = None):
    """Return an Anthropic client, using BYOK key if the tenant has one.

    Lightweight — Anthropic client creation just stores the key.
    Safe to call per-request.
    """
    import anthropic

    api_key = Config.ANTHROPIC_API_KEY
    has_byok = False
    if tenant_id:
        try:
            from services.billing_service import get_byok_keys
            byok = get_byok_keys(tenant_id)
            if byok and byok.get("anthropic"):
                api_key = byok["anthropic"]
                has_byok = True
                logger.debug("BYOK Anthropic client for tenant %s", tenant_id)
        except Exception as exc:
            logger.warning("BYOK lookup failed for tenant %s: %s — using platform key", tenant_id, exc)

        # Demo tenants (or any tenant when DEMO_MODE is on) must supply their own
        # API key — no platform key fallback.
        if not has_byok:
            try:
                from services.billing_service import is_demo_tenant
                if is_demo_tenant(tenant_id) or Config.DEMO_MODE:
                    raise ValueError("must configure BYOK API keys to use AI features")
            except ValueError:
                raise
            except Exception as exc:
                logger.warning("Demo tenant check failed for tenant %s: %s", tenant_id, exc)

    return anthropic.Anthropic(api_key=api_key)


# ---------------------------------------------------------------------------
# Anthropic call
# ---------------------------------------------------------------------------
def _call_anthropic(
    model: str,
    max_tokens: int,
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    api_key_override: str | None = None,
) -> LLMResult:
    """Call Anthropic API. Returns LLMResult."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key_override or Config.ANTHROPIC_API_KEY)

    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    start = time.time()
    response = client.messages.create(**kwargs)
    latency = (time.time() - start) * 1000

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    return LLMResult(
        text=text.strip(),
        content=response.content,
        usage={
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        stop_reason=response.stop_reason or "",
        provider="anthropic",
        model=model,
        latency_ms=latency,
    )


# ---------------------------------------------------------------------------
# OpenAI call (fallback)
# ---------------------------------------------------------------------------
def _translate_tools_for_openai(tools: list[dict]) -> list[dict]:
    """Translate Anthropic tool schemas to OpenAI function-calling format.

    Anthropic:  {name, description, input_schema: {type, properties, required}}
    OpenAI:     {type: "function", function: {name, description, parameters: {type, properties, required}}}
    """
    oai_tools = []
    for tool in tools:
        oai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        })
    return oai_tools


def _call_openai(
    anthropic_model: str,
    max_tokens: int,
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    api_key_override: str | None = None,
) -> LLMResult:
    """Call OpenAI API as fallback. Translates Anthropic params → OpenAI format."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key_override or Config.OPENAI_API_KEY)
    openai_model = _OPENAI_MODEL_MAP.get(anthropic_model, "gpt-4o-mini")

    # Build OpenAI messages: system first, then conversation
    oai_messages = []
    if system:
        oai_messages.append({"role": "system", "content": system})
    for m in messages:
        oai_messages.append({"role": m["role"], "content": m["content"]})

    kwargs = {
        "model": openai_model,
        "max_tokens": max_tokens,
        "messages": oai_messages,
    }
    if tools:
        kwargs["tools"] = _translate_tools_for_openai(tools)

    start = time.time()
    response = client.chat.completions.create(**kwargs)
    latency = (time.time() - start) * 1000

    choice = response.choices[0]
    text = choice.message.content or ""

    return LLMResult(
        text=text.strip(),
        content=[],  # no Anthropic-format content blocks
        usage={
            "input_tokens": response.usage.prompt_tokens if response.usage else 0,
            "output_tokens": response.usage.completion_tokens if response.usage else 0,
        },
        stop_reason=choice.finish_reason or "",
        provider="openai",
        model=openai_model,
        latency_ms=latency,
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def complete(
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    system: str = "",
    tools: list[dict] | None = None,
    tenant_id: int | None = None,
    ticket_id: int | None = None,
    caller: str = "",
) -> LLMResult:
    """Call LLM with automatic failover to OpenAI.

    Tool schemas are automatically translated between providers.
    When OPENAI_API_KEY is not set, failover is disabled.

    BYOK: When tenant_id is provided, checks for enterprise BYOK keys
    and uses the tenant's own API key instead of the platform key.
    """
    # --- Resolve BYOK keys for enterprise tenants ---
    byok_anthropic_key = None
    byok_openai_key = None
    if tenant_id:
        try:
            from services.billing_service import get_byok_keys
            byok = get_byok_keys(tenant_id)
            if byok:
                byok_anthropic_key = byok.get("anthropic")
                byok_openai_key = byok.get("openai")
                if byok_anthropic_key:
                    logger.debug("Using BYOK Anthropic key for tenant %s", tenant_id)
                if byok_openai_key:
                    logger.debug("Using BYOK OpenAI key for tenant %s", tenant_id)
        except Exception as exc:
            logger.warning("BYOK key lookup failed for tenant %s: %s — using platform key", tenant_id, exc)

    # Demo tenants (or any tenant when DEMO_MODE is on) must supply their own API key —
    # no platform key fallback.
    if tenant_id and byok_anthropic_key is None:
        try:
            from services.billing_service import is_demo_tenant
            if is_demo_tenant(tenant_id) or Config.DEMO_MODE:
                raise ValueError("must configure BYOK API keys to use AI features")
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Demo tenant check failed for tenant %s: %s", tenant_id, exc)

    # --- Try Anthropic first ---
    anthropic_error = None
    try:
        result = _call_anthropic(model, max_tokens, messages, system, tools,
                                 api_key_override=byok_anthropic_key)
        tok_in  = result.usage.get("input_tokens", 0)
        tok_out = result.usage.get("output_tokens", 0)
        log_call(caller, tenant_id, ticket_id, "anthropic", model, result.latency_ms, tok_in, tok_out)
        _record_usage(tenant_id, ticket_id, "anthropic", model, caller, tok_in, tok_out)
        try:
            from services import billing_service
            billing_service.record_usage(tenant_id, model, caller, tok_in, tok_out)
        except Exception:
            pass  # Never let billing errors break LLM calls
        return result
    except Exception as e:
        anthropic_error = e
        log_call(caller, tenant_id, ticket_id, "anthropic", model, 0, success=False, error=str(e)[:200])

    # --- Failover to OpenAI ---
    can_failover = (byok_openai_key or Config.OPENAI_API_KEY) and model in _OPENAI_MODEL_MAP

    if not can_failover:
        raise anthropic_error

    # Demo tenant gate for OpenAI failover: if the tenant has no BYOK OpenAI key the
    # failover would consume the platform key.  Block it and re-raise the original error.
    # This applies to individual demo-tier tenants and to all tenants when DEMO_MODE is on.
    if tenant_id and byok_openai_key is None:
        try:
            from services.billing_service import is_demo_tenant
            if is_demo_tenant(tenant_id) or Config.DEMO_MODE:
                raise anthropic_error  # No platform OpenAI fallback
        except ValueError:
            raise
        except Exception:
            pass  # Non-ValueError means the demo check itself failed; allow failover

    logger.warning("Anthropic failed — attempting OpenAI failover for [%s]", caller)

    try:
        result = _call_openai(model, max_tokens, messages, system, tools,
                             api_key_override=byok_openai_key)
        tok_in  = result.usage.get("input_tokens", 0)
        tok_out = result.usage.get("output_tokens", 0)
        log_call(caller, tenant_id, ticket_id, "openai", result.model, result.latency_ms, tok_in, tok_out)
        _record_usage(tenant_id, ticket_id, "openai", result.model, caller, tok_in, tok_out)
        try:
            from services import billing_service
            billing_service.record_usage(tenant_id, result.model, caller, tok_in, tok_out)
        except Exception:
            pass  # Never let billing errors break LLM calls
        return result
    except Exception as openai_error:
        log_call(
            caller, tenant_id, ticket_id, "openai", _OPENAI_MODEL_MAP.get(model, "?"),
            0, success=False, error=str(openai_error)[:200],
        )
        # Raise original Anthropic error — it's more relevant
        raise anthropic_error from openai_error
