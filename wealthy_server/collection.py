"""Persist normalized read-only provider results in PostgreSQL."""

from __future__ import annotations

from asset_assistant.market_data import CollectionResult, CollectionStatus
from asset_assistant.providers import BlsProvider, FederalReserveNewsProvider

from .repositories import MarketRecordInput, MarketRepository


DEFAULT_BLS_SERIES = ("CUUR0000SA0", "LNS14000000")


def persist_collection_result(
    repository: MarketRepository,
    result: CollectionResult,
    *,
    initiated_by: str | None,
    connection_mode: str = "FIXTURE",
) -> int:
    """Persist one already-normalized result without interpreting external text."""

    run = repository.start_run(
        provider=result.provider,
        started_at=result.started_at,
        initiated_by=initiated_by,
        connection_mode=connection_mode,
    )
    if result.records:
        repository.append_records(
            run.id,
            tuple(
                MarketRecordInput(
                    kind=record.kind.value,
                    provider_record_id=record.record_id,
                    instrument_id=record.instrument_id,
                    market=record.market,
                    currency=record.currency,
                    effective_at=record.effective_at,
                    published_at=record.published_at,
                    collected_at=record.collected_at,
                    unit=record.unit,
                    value=record.value,
                    revised=record.revised,
                    source_url=record.source_url,
                    source_metadata=record.metadata,
                )
                for record in result.records
            ),
        )
    status = {
        CollectionStatus.OK: "SUCCEEDED",
        CollectionStatus.PARTIAL: "PARTIAL",
        CollectionStatus.ERROR: "FAILED",
    }[result.status]
    failure_code = result.issues[0].code if result.issues else None
    repository.finish_run(
        run.id,
        status=status,
        finished_at=result.finished_at,
        failure_code=failure_code,
    )
    return run.id


def collect_public_official_sources(
    repository: MarketRepository,
    *,
    initiated_by: str | None,
    bls: BlsProvider | None = None,
    fed: FederalReserveNewsProvider | None = None,
) -> tuple[CollectionResult, ...]:
    """Collect the two official no-account providers currently connected.

    Network access occurs only when this function is explicitly called by the
    worker or an operator.  Imports, API page loads, and tests never call it.
    """

    results = (
        (bls or BlsProvider()).collect(DEFAULT_BLS_SERIES),
        (fed or FederalReserveNewsProvider()).collect(),
    )
    for result in results:
        persist_collection_result(
            repository,
            result,
            initiated_by=initiated_by,
            connection_mode="LIVE",
        )
    return results
