"""샘플·자체모의·수동 실제자료를 섞지 않는 메모리 내 자산 원장."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from .domain import (
    Account,
    AggregateValue,
    DataRealm,
    DeclaredAssetPlan,
    DomainValidationError,
    PositionRecord,
    Purpose,
    ReconciliationResult,
    ValueStatus,
)


class PortfolioLedger:
    """단일 데이터 영역의 평가 스냅샷 원장입니다."""

    def __init__(
        self,
        *,
        realm: DataRealm,
        accounts: Iterable[Account],
        positions: Iterable[PositionRecord],
    ) -> None:
        self.realm = realm
        self._accounts: dict[str, Account] = {}
        self._positions: list[PositionRecord] = []

        for account in accounts:
            if account.realm is not realm:
                raise DomainValidationError(
                    "서로 다른 자료 영역의 계좌를 한 원장에 넣을 수 없습니다."
                )
            if account.account_id in self._accounts:
                raise DomainValidationError(
                    "중복 account_id가 있습니다. 원본 ID는 오류에 표시하지 않습니다."
                )
            self._accounts[account.account_id] = account

        seen_positions: set[str] = set()
        for position in positions:
            if position.position_id in seen_positions:
                raise DomainValidationError(
                    "중복 position_id가 있습니다. 원본 ID는 오류에 표시하지 않습니다."
                )
            if position.account_id not in self._accounts:
                raise DomainValidationError(
                    "포지션이 참조한 계좌가 원장에 없습니다. 원본 ID는 오류에 표시하지 않습니다."
                )
            seen_positions.add(position.position_id)
            self._positions.append(position)

    @property
    def accounts(self) -> tuple[Account, ...]:
        return tuple(self._accounts.values())

    @property
    def positions(self) -> tuple[PositionRecord, ...]:
        return tuple(self._positions)

    def positions_for_purpose(self, purpose: Purpose) -> tuple[PositionRecord, ...]:
        return tuple(
            position
            for position in self._positions
            if self._accounts[position.account_id].purpose is purpose
        )

    def aggregate(self, *, purpose: Purpose | None = None) -> AggregateValue:
        selected = list(
            self._positions
            if purpose is None
            else self.positions_for_purpose(purpose)
        )
        if not selected:
            return AggregateValue(Decimal("0"), False, ())
        total = Decimal("0")
        missing: list[str] = []
        for position in selected:
            if position.value_status is ValueStatus.KNOWN:
                total += position.market_value_krw or Decimal("0")
            else:
                missing.append(position.position_id)
        return AggregateValue(total, not missing, tuple(missing))

    def investment_value(self) -> AggregateValue:
        return self.aggregate(purpose=Purpose.INVESTMENT)

    def reconcile(self, plan: DeclaredAssetPlan) -> ReconciliationResult:
        detail = self.aggregate()
        difference = plan.amount_krw - detail.amount_krw
        return ReconciliationResult(
            declared_krw=plan.amount_krw,
            detailed_krw=detail.amount_krw,
            difference_krw=difference,
            matches=detail.complete and difference == 0,
            complete=detail.complete,
        )
