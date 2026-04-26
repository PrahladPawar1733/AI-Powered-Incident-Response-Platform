# shared/pg_client.py
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from shared.logger import get_logger
from shared.models.runbook import PastIncident, Runbook

log = get_logger("pg-client")


class PostgresClient:
    """
    Async SQLAlchemy client used by all services.

    We use raw SQL for pgvector queries because SQLAlchemy's ORM
    doesn't speak the pgvector <-> operator natively yet.
    For everything else, SQLAlchemy core expressions are fine.
    """

    def __init__(self, database_url: str, pool_size: int = 10):
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=5,
            pool_pre_ping=True,      # check connection health before using
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Context manager — handles commit/rollback automatically."""
        async with self._session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    # ── Incident persistence ─────────────────────────────────────────

    async def save_incident(self, incident: dict[str, Any]) -> None:
        """Upsert — safe to call multiple times as incident is enriched."""
        async with self.session() as sess:
            await sess.execute(
                text("""
                    INSERT INTO incidents (
                        incident_id, status, alert_name, service,
                        severity, root_cause, resolution_summary,
                        mttr_seconds, trace_id, raw_context, created_at
                    ) VALUES (
                        :incident_id, :status, :alert_name, :service,
                        :severity, :root_cause, :resolution_summary,
                        :mttr_seconds, :trace_id, :raw_context::jsonb, :created_at
                    )
                    ON CONFLICT (incident_id) DO UPDATE SET
                        status             = EXCLUDED.status,
                        root_cause         = EXCLUDED.root_cause,
                        resolution_summary = EXCLUDED.resolution_summary,
                        mttr_seconds       = EXCLUDED.mttr_seconds,
                        raw_context        = EXCLUDED.raw_context,
                        updated_at         = NOW()
                """),
                incident,
            )

    # ── pgvector runbook retrieval ───────────────────────────────────

    async def find_similar_runbooks(
        self,
        embedding: list[float],
        service: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Find the most relevant runbooks using cosine similarity.

        The <=> operator is pgvector's cosine distance.
        1 - distance = similarity score (0 = no match, 1 = identical).

        Why cosine similarity over L2 distance?
        Cosine measures the angle between vectors — it's scale-invariant.
        A short alert description and a long runbook can still match well
        if they talk about the same concepts.
        """
        async with self.session() as sess:
            result = await sess.execute(
                text("""
                    SELECT
                        runbook_id,
                        title,
                        description,
                        steps,
                        tags,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM runbooks
                    WHERE service = ANY(CAST(:services AS text[]))
                       OR :service = ANY(services)
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                """),
                {
                    "embedding": str(embedding),
                    "services":  "{" + service + "}",
                    "service":   service,
                    "limit":     limit,
                },
            )
            return [dict(row._mapping) for row in result]

    async def find_similar_incidents(
        self,
        embedding: list[float],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Retrieve past resolved incidents similar to the current alert.
        The triage agent uses these as few-shot context:
        'Last time: root cause was X, fixed in Y minutes.'
        """
        async with self.session() as sess:
            result = await sess.execute(
                text("""
                    SELECT
                        incident_id,
                        alert_name,
                        service,
                        root_cause,
                        resolution_summary,
                        mttr_seconds,
                        severity,
                        resolved_at,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity_score
                    FROM incidents
                    WHERE status = 'resolved'
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                """),
                {"embedding": str(embedding), "limit": limit},
            )
            return [dict(row._mapping) for row in result]

    async def save_embedding(
        self,
        table: str,
        id_column: str,
        id_value: str,
        embedding: list[float],
    ) -> None:
        """Store an embedding vector after incident resolution or runbook creation."""
        async with self.session() as sess:
            await sess.execute(
                text(f"""
                    UPDATE {table}
                    SET embedding = CAST(:embedding AS vector)
                    WHERE {id_column} = :id_value
                """),
                {"embedding": str(embedding), "id_value": id_value},
            )

    async def dispose(self) -> None:
        await self._engine.dispose()