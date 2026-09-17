from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path
from typing import Any

from voice_core.config import Settings, get_settings
from voice_core.kb.chunker import DocParseError, build_chunks, parse_document
from voice_core.packs.loader import load_pack
from voice_core.ports.embeddings import EmbeddingProvider
from voice_core.ports.knowledge import KnowledgeStore
from voice_core.ports.types import KBChunk, KBDocument

_REQUIRED_FIELDS = ("slug", "title", "domain", "language", "version", "status")


def _validate_front_matter(front_matter: dict[str, Any], path: Path) -> None:
    missing = [f for f in _REQUIRED_FIELDS if f not in front_matter]
    if missing:
        raise DocParseError(f"{path}: missing required front-matter field(s): {missing}")


async def ingest_file(
    path: Path,
    pack_id: str,
    store: KnowledgeStore,
    embeddings: EmbeddingProvider,
    *,
    dry_run: bool = False,
) -> str:
    raw = path.read_text(encoding="utf-8")
    parsed = parse_document(raw)
    _validate_front_matter(parsed.front_matter, path)
    fm = parsed.front_matter

    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    existing_hash = await store.get_document_hash(
        pack_id, fm["slug"], fm["language"], int(fm["version"])
    )
    if existing_hash == content_hash:
        return f"unchanged: {fm['slug']}@{fm['language']} v{fm['version']}"

    if dry_run:
        return (
            f"would ingest: {fm['slug']}@{fm['language']} v{fm['version']} (status={fm['status']})"
        )

    text_chunks = build_chunks(fm["title"], parsed.sections)
    if not text_chunks:
        return f"skipped (no ## sections): {fm['slug']}@{fm['language']}"

    vectors = await embeddings.embed([c.text for c in text_chunks], kind="document")

    kb_chunks = [
        KBChunk(
            chunk_index=c.chunk_index,
            heading=c.heading,
            text=c.text,
            embedding=vector,
            embedding_model=embeddings.model_id,
            token_count=c.token_count,
        )
        for c, vector in zip(text_chunks, vectors, strict=True)
    ]

    doc = KBDocument(
        pack_id=pack_id,
        slug=fm["slug"],
        title=fm["title"],
        domain=fm["domain"],
        version=int(fm["version"]),
        language=fm["language"],
        status=fm["status"],
        content_hash=content_hash,
        audience=fm.get("audience"),
        source_path=str(path),
        effective_from=str(fm["effective_from"]) if fm.get("effective_from") else None,
    )
    await store.publish_document(doc, kb_chunks)
    return f"ingested: {fm['slug']}@{fm['language']} v{fm['version']} ({len(kb_chunks)} chunks)"


def _build_store(settings: Settings) -> KnowledgeStore:
    from voice_core.adapters.supabase.store import SupabaseKnowledgeStore

    return SupabaseKnowledgeStore(
        database_url=settings.database_url,
        statement_cache_size=settings.db_statement_cache_size,
    )


def _build_embeddings(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "gemini":
        from voice_core.adapters.gemini.embeddings import GeminiEmbedding

        return GeminiEmbedding(api_key=settings.llm_api_key, dim=settings.embedding_dim)

    from voice_core.adapters.fakes.embeddings import FakeEmbedding

    return FakeEmbedding(dim=settings.embedding_dim)


async def _main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    backend_dir = Path(__file__).resolve().parents[2]
    packs_root = (backend_dir / settings.domain_packs_dir).resolve()
    pack = load_pack(args.pack, packs_root)

    knowledge_dir = pack.pack_dir / "knowledge"
    paths = sorted(knowledge_dir.glob("*/*.md"))
    if args.slug:
        paths = [p for p in paths if p.stem == args.slug]
    if not paths:
        print(f"no knowledge files found under {knowledge_dir}", file=sys.stderr)
        return 1

    store = _build_store(settings)
    embeddings = _build_embeddings(settings)

    for path in paths:
        try:
            result = await ingest_file(path, args.pack, store, embeddings, dry_run=args.dry_run)
        except DocParseError as exc:
            print(f"ERROR {path}: {exc}", file=sys.stderr)
            return 1
        print(result)

    if hasattr(store, "close"):
        await store.close()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a pack's knowledge docs")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slug", default=None, help="ingest only the file with this slug/stem")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
