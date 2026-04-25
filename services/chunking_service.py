"""Chunking service: heading-aware document splitting with token counting."""

import hashlib
import re

import tiktoken

# cl100k_base matches text-embedding-3-small tokenizer
_enc = tiktoken.get_encoding("cl100k_base")

SMALL_DOC_THRESHOLD = 4000   # tokens — below this, 1 chunk = whole article
MAX_SECTION_TOKENS = 6000    # fallback split threshold for oversized sections
TARGET_CHUNK_TOKENS = 4000   # target size for paragraph-level splits


def chunk_document(
    doc_id: int,
    title: str,
    content: str,
    source_url: str | None,
    module_slug: str,
    tags: list[str] | None = None,
) -> list[dict]:
    """Split a document into chunks suitable for embedding.

    Small docs (< 4000 tokens): single chunk with full content.
    Large docs: split at ### / #### heading boundaries, prepending article title.
    Oversized sections: fallback split at paragraph boundaries.

    Args:
        tags: Document-level tags to propagate to each chunk.

    Returns list of chunk dicts with:
        chunk_index, content, token_count, tags, metadata (module, title, section, source_url, content_hash, tags)
    """
    total_tokens = len(_enc.encode(content))

    if total_tokens < SMALL_DOC_THRESHOLD:
        chunk_text = f"Article: {title}\n\n{content}"
        return [_make_chunk(chunk_text, 0, module_slug, title, None, source_url, tags)]

    # Split at heading boundaries
    sections = _split_at_headings(content)
    chunks = []
    idx = 0

    for section_heading, section_body in sections:
        if section_heading:
            section_text = f"Article: {title}\n\nSection: {section_heading}\n\n{section_body}"
        else:
            section_text = f"Article: {title}\n\n{section_body}"

        section_tokens = len(_enc.encode(section_text))

        if section_tokens <= MAX_SECTION_TOKENS:
            chunks.append(_make_chunk(section_text, idx, module_slug, title, section_heading, source_url, tags))
            idx += 1
        else:
            # Fallback: split at paragraph boundaries
            sub_chunks = _split_at_paragraphs(section_body, title, section_heading)
            for sub_text in sub_chunks:
                chunks.append(_make_chunk(sub_text, idx, module_slug, title, section_heading, source_url, tags))
                idx += 1

    return chunks


def _split_at_headings(content: str) -> list[tuple[str | None, str]]:
    """Split content at ### or #### heading markers.

    Returns list of (heading_text, body_text) tuples.
    First section may have heading=None (preamble before first heading).
    """
    pattern = re.compile(r'^(#{3,4})\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(content))

    if not matches:
        return [(None, content)]

    sections = []

    # Preamble before first heading
    if matches[0].start() > 0:
        preamble = content[:matches[0].start()].strip()
        if preamble:
            sections.append((None, preamble))

    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()
        if body:
            sections.append((heading, body))

    return sections


def _split_at_paragraphs(text: str, title: str, section_heading: str | None) -> list[str]:
    """Split text at paragraph boundaries to stay under TARGET_CHUNK_TOKENS."""
    paragraphs = re.split(r'\n\n+', text)
    chunks = []
    current_parts = []
    current_tokens = 0

    prefix = f"Article: {title}"
    if section_heading:
        prefix += f"\n\nSection: {section_heading}"
    prefix += "\n\n"
    prefix_tokens = len(_enc.encode(prefix))

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_tokens = len(_enc.encode(para))

        if current_tokens + para_tokens + prefix_tokens > TARGET_CHUNK_TOKENS and current_parts:
            chunks.append(prefix + "\n\n".join(current_parts))
            current_parts = []
            current_tokens = 0

        current_parts.append(para)
        current_tokens += para_tokens

    if current_parts:
        chunks.append(prefix + "\n\n".join(current_parts))

    return chunks


def _make_chunk(
    content: str,
    chunk_index: int,
    module_slug: str,
    title: str,
    section: str | None,
    source_url: str | None,
    tags: list[str] | None = None,
) -> dict:
    """Create a chunk dict with metadata, content hash, and propagated tags.

    Content hash is computed from content only (not tags) — tag-only changes
    do not trigger re-embedding.
    """
    token_count = len(_enc.encode(content))
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    chunk_tags = tags or []

    return {
        "chunk_index": chunk_index,
        "content": content,
        "token_count": token_count,
        "tags": chunk_tags,
        "metadata": {
            "module": module_slug,
            "title": title,
            "section": section,
            "source_url": source_url,
            "content_hash": content_hash,
            "tags": chunk_tags,
        },
    }
