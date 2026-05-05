"""OpenAI-compatible chat completions endpoint (ADR-0004).

POST /v1/chat/completions — compatible with Open WebUI.
Pipeline: parse commands → rule scorer → (LLM router) → retrieval → three-zone gen → SSE stream.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.logging import get_logger

router = APIRouter(prefix="/v1", tags=["chat"])
log = get_logger(__name__)

_MODEL_NAME = "knowledge-base"
_RULE_SCORE_THRESHOLD = 0.8  # skip LLM router when rule confidence exceeds this

# OpenWebUI sends these system tasks through the chat API — bypass our RAG pipeline
_OPENWEBUI_TASK_PREFIXES = (
    "### Task:\nSuggest",
    "### Task:\nGenerate",
    "### Task:\nCreate",
    "### Task:\nAnalyze",
    "### Task:\nSearch",
)


# ── Request / Response schemas ─────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = _MODEL_NAME
    messages: list[ChatMessage]
    stream: bool = True
    temperature: float = 0.3
    max_tokens: int = 2048


# Pydantic v2 + from __future__ import annotations: force annotation evaluation
ChatMessage.model_rebuild()
ChatCompletionRequest.model_rebuild()


# ── SSE helpers ────────────────────────────────────────────────────────────────

def _sse_chunk(content: str, completion_id: str, finish: bool = False) -> str:
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": _MODEL_NAME,
        "choices": [{
            "index": 0,
            "delta": {} if finish else {"content": content},
            "finish_reason": "stop" if finish else None,
        }],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


def _sse_role_chunk(completion_id: str) -> str:
    chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": _MODEL_NAME,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"


async def _stream_text(text: str, completion_id: str, chunk_size: int = 80) -> AsyncIterator[str]:
    """Yield SSE chunks by splitting text into ~80-char pieces."""
    yield _sse_role_chunk(completion_id)
    for i in range(0, len(text), chunk_size):
        yield _sse_chunk(text[i: i + chunk_size], completion_id)
    yield _sse_chunk("", completion_id, finish=True)
    yield "data: [DONE]\n\n"


# ── Pipeline ───────────────────────────────────────────────────────────────────

async def _run_pipeline(
    query: str,
    req: ChatCompletionRequest,
    request: Request,
) -> str:
    """Full query pipeline → rendered Markdown string."""
    from app.services.commands import apply_commands, parse_commands
    from app.services.generator import generate, make_refusal_envelope
    from app.services.retrieval.pipeline import RetrievalPipeline
    from app.services.retrieval.schemas import EmptyRetrieval
    from app.services.router import (
        RetrievalContext,
        RouterDecision,
        best_intent,
        route_with_llm,
    )

    container = request.app.state.container
    cfg = container.cfg

    # 1. Parse slash commands → clean query + RetrievalContext
    clean_query, commands = parse_commands(query)
    ctx = RetrievalContext(query=clean_query)
    apply_commands(ctx, commands)

    # Handle /save-web as a standalone command (no retrieval needed)
    if ctx.save_web_url:
        return await _handle_save_web_command(ctx.save_web_url, container)

    effective_query = clean_query
    if not effective_query.strip():
        from app.services.render.markdown import MarkdownRenderer
        return MarkdownRenderer().render(make_refusal_envelope(), query="")

    # 2. Rule-based intent scoring
    intent, confidence = best_intent(effective_query)
    log.info("router_rule", query=effective_query[:60], intent=intent, confidence=confidence)

    # 3. Build RouterDecision: skip LLM router when rule confidence is high enough
    if confidence >= _RULE_SCORE_THRESHOLD:
        decision = RouterDecision.from_intent(intent)  # type: ignore[attr-defined]
    else:
        llm_decision = await route_with_llm(
            effective_query,
            ollama_host=cfg.ollama.host,
            model=cfg.ollama.chat_model,
        )
        decision = llm_decision or RouterDecision.from_intent(intent)  # type: ignore[attr-defined]

    ctx.router_decision = decision

    # 4. Refusal: router says no
    if decision.refusal:
        log.info("router_refusal", query=effective_query[:60])
        from app.services.render.markdown import MarkdownRenderer
        envelope = make_refusal_envelope(hint=effective_query)
        return MarkdownRenderer().render(envelope, query=effective_query)

    # 5. Apply channel weights from command overrides
    channel_weights = dict(decision.channel_weights)
    if ctx.force_web:
        channel_weights["web"] = max(channel_weights.get("web", 0.0), 1.0)
    if not ctx.force_web and decision.channel_weights.get("web", 0.0) == 0.0:
        channel_weights.pop("web", None)

    # /sources:notes,books — zero out channels not in the allow-list, boost listed ones
    if ctx.sources_filter:
        allowed = set(ctx.sources_filter)
        for ch in list(channel_weights.keys()):
            if ch not in allowed:
                channel_weights[ch] = 0.0
            else:
                channel_weights[ch] = max(channel_weights.get(ch, 0.0), 1.0)

    top_k = 12 if ctx.deep_mode else 8

    # 6. Retrieval pipeline
    sensitive_entities = _collect_sensitive_entities(cfg)
    pipeline = RetrievalPipeline(
        qdrant=container.qdrant,
        oxigraph=container.oxigraph,
        dense_embedder=container.dense_embedder,
        sparse_embedder=container.sparse_embedder,
        reranker=container.reranker,
        graph_walk_cfg=cfg.graph_walk,
        web_channel=container.web_channel,
    )
    result = await pipeline.retrieve(
        effective_query,
        vault_scope=ctx.vault_scope,
        seed_entities=decision.entities,
        channel_weights=channel_weights,
        top_k=top_k,
        intent=decision.intent,
        force_web=ctx.force_web,
        no_web=ctx.no_web,
        sensitive_entities=sensitive_entities,
    )

    # 7. Empty retrieval → refusal (also when only graph/wikidata with no parents)
    if isinstance(result, EmptyRetrieval) or not result.parents:
        log.info("retrieval_empty_refusal", query=effective_query[:60])
        from app.services.render.markdown import MarkdownRenderer
        envelope = make_refusal_envelope(hint=effective_query)
        return MarkdownRenderer().render(envelope, query=effective_query)

    # 8. Sources-only mode: skip generation
    if ctx.sources_only:
        lines = [f"**Источники ({len(result.parents)}):**\n"]
        for p in result.parents:
            lines.append(f"- {p.parent_id} (score={p.score:.3f})")
        return "\n".join(lines)

    # 9. Three-zone generation
    envelope = await generate(
        effective_query,
        result,
        ollama_host=cfg.ollama.host,
        model=cfg.ollama.chat_model,
        strict_mode=ctx.strict_mode,
    )
    from app.services.render.markdown import MarkdownRenderer
    vault_map = {v.id: v.obsidian_uri_name for v in cfg.sources.vaults}
    renderer = MarkdownRenderer()
    return renderer.render(envelope, vault_map=vault_map, base_url="http://localhost:8000", query=effective_query)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _proxy_to_ollama(body: "ChatCompletionRequest", request: Request):
    """Forward OpenWebUI internal tasks directly to Ollama, bypassing RAG pipeline."""
    import httpx
    cfg = request.app.state.container.cfg
    payload = {
        "model": cfg.ollama.chat_model,
        "messages": [{"role": m.role, "content": m.content} for m in body.messages],
        "stream": body.stream,
        "temperature": body.temperature,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{cfg.ollama.host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        content = data.get("message", {}).get("content", "")
    except Exception as exc:
        log.warning("ollama_proxy_error", error=str(exc))
        content = ""

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if body.stream:
        return StreamingResponse(
            _stream_text(content, completion_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _MODEL_NAME,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _collect_sensitive_entities(cfg) -> list[str]:
    """Gather sensitive entity names from all vault configs."""
    entities: list[str] = []
    for vault in cfg.sources.vaults:
        if vault.sensitive_entities_file and vault.sensitive_entities_file.exists():
            for line in vault.sensitive_entities_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    entities.append(line)
    return entities


async def _handle_save_web_command(url: str, container) -> str:
    """Execute /save-web <url>: fetch, save, enqueue index job."""
    import importlib.util, sys
    from pathlib import Path as _Path
    p = _Path(__file__).parent.parent / "services" / "web" / "save-web-handler.py"
    name = "_save_web_handler"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    handler = sys.modules[name]

    from app.config import REPO_ROOT
    data_dir = REPO_ROOT / "data"
    try:
        result = await handler.save_web_page(
            url=url,
            data_dir=data_dir,
            sqlite=container.sqlite,
            dispatcher=container.dispatcher,
            gpu_mutex=container.gpu_mutex,
        )
        if result.is_duplicate:
            return f"Already saved (no changes): `{url}`"
        action = "Updated" if result.is_update else "Saved"
        preview = f"http://localhost:8000/preview/web/{result.content_hash}"
        return f"{action}: `{url}`\n[Читать]({preview})"
    except Exception as exc:
        log.error("save_web_command_failed", url=url, error=str(exc))
        return f"Failed to save `{url}`: {exc}"


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    """OpenAI-compatible model list so Open WebUI discovers 'knowledge-base'."""
    return {
        "object": "list",
        "data": [{
            "id": _MODEL_NAME,
            "object": "model",
            "created": 0,
            "owned_by": "local",
        }],
    }


@router.post("/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    # Extract last user message
    user_msgs = [m for m in body.messages if m.role == "user"]
    if not user_msgs:
        return JSONResponse(
            status_code=400,
            content={"error": "No user message in request"},
        )
    query = user_msgs[-1].content.strip()
    if not query:
        return JSONResponse(
            status_code=400,
            content={"error": "Empty user message"},
        )

    # Pass OpenWebUI internal tasks directly to Ollama, bypass RAG pipeline
    if any(query.startswith(p) for p in _OPENWEBUI_TASK_PREFIXES):
        return await _proxy_to_ollama(body, request)

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    log.info("chat_completions_request", query=query[:80], stream=body.stream)
    _t0 = time.time()

    try:
        text = await _run_pipeline(query, body, request)
    except Exception as e:
        log.error("pipeline_error", error_type=type(e).__name__, error=str(e), query=query[:80], exc_info=True)
        text = "Произошла ошибка при обработке запроса. Попробуйте ещё раз."

    log.info(
        "request_done",
        query=query[:80],
        response_chars=len(text),
        latency_ms=int((time.time() - _t0) * 1000),
    )

    if body.stream:
        return StreamingResponse(
            _stream_text(text, completion_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming response
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": _MODEL_NAME,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
