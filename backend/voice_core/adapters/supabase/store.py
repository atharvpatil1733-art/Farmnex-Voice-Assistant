from __future__ import annotations

import asyncpg

from voice_core.ports.types import Chunk, KBChunk, KBDocument


def _encode_vector(value: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in value) + "]"


def _decode_vector(value: str) -> list[float]:
    inner = value.strip("[]")
    return [float(x) for x in inner.split(",")] if inner else []


class SupabaseKnowledgeStore:
    """KnowledgeStore backed by the `voice` schema in Postgres (Supabase or plain PG)."""

    def __init__(self, database_url: str, statement_cache_size: int = 0) -> None:
        self._database_url = database_url
        self._statement_cache_size = statement_cache_size
        self._pool: asyncpg.Pool | None = None

    async def _init_connection(self, conn: asyncpg.Connection) -> None:
        await conn.set_type_codec(
            "vector",
            schema="extensions",
            encoder=_encode_vector,
            decoder=_decode_vector,
            format="text",
        )

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._database_url,
                statement_cache_size=self._statement_cache_size,
                init=self._init_connection,
            )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def match(
        self,
        pack_id: str,
        embedding: list[float],
        k: int,
        min_similarity: float,
        domains: list[str] | None,
        prefer_language: str | None = None,
    ) -> list[Chunk]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            "select slug, version, heading, content, similarity "
            "from voice.match_chunks($1, $2::extensions.vector, $3, $4, $5, $6)",
            pack_id,
            embedding,
            k,
            min_similarity,
            domains,
            prefer_language,
        )
        return [
            Chunk(
                doc_slug=r["slug"],
                doc_version=r["version"],
                heading=r["heading"] or "",
                text=r["content"],
                similarity=r["similarity"],
            )
            for r in rows
        ]

    async def publish_document(self, doc: KBDocument, chunks: list[KBChunk]) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            if doc.status == "active":
                await conn.execute(
                    """
                    update voice.kb_documents
                       set status = 'retired', effective_to = now()
                     where pack_id = $1 and slug = $2 and language = $3 and status = 'active'
                       and version <> $4
                    """,
                    doc.pack_id,
                    doc.slug,
                    doc.language,
                    doc.version,
                )

            row = await conn.fetchrow(
                """
                insert into voice.kb_documents
                    (pack_id, slug, title, domain, language, version, status, audience,
                     source_path, content_hash, effective_from)
                values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, coalesce($11::timestamptz, now()))
                on conflict (pack_id, slug, language, version) do update
                    set title = excluded.title, domain = excluded.domain, status = excluded.status,
                        audience = excluded.audience, source_path = excluded.source_path,
                        content_hash = excluded.content_hash
                returning id
                """,
                doc.pack_id,
                doc.slug,
                doc.title,
                doc.domain,
                doc.language,
                doc.version,
                doc.status,
                doc.audience,
                doc.source_path,
                doc.content_hash,
                doc.effective_from,
            )
            document_id = row["id"]

            await conn.execute("delete from voice.kb_chunks where document_id = $1", document_id)
            for chunk in chunks:
                await conn.execute(
                    """
                    insert into voice.kb_chunks
                        (document_id, pack_id, chunk_index, heading, content, language,
                         token_count, embedding, embedding_model)
                    values ($1, $2, $3, $4, $5, $6, $7, $8::extensions.vector, $9)
                    """,
                    document_id,
                    doc.pack_id,
                    chunk.chunk_index,
                    chunk.heading,
                    chunk.text,
                    doc.language,
                    chunk.token_count,
                    chunk.embedding,
                    chunk.embedding_model,
                )

    async def get_document_hash(
        self, pack_id: str, slug: str, language: str, version: int
    ) -> str | None:
        pool = await self._get_pool()
        result: str | None = await pool.fetchval(
            """
            select content_hash from voice.kb_documents
             where pack_id = $1 and slug = $2 and language = $3 and version = $4
            """,
            pack_id,
            slug,
            language,
            version,
        )
        return result
