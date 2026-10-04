"""Per-process record of published advisories and internal validation results.

The database has no advisory table yet, so published versions live here: bounded,
idempotent per case, and the only source for the validator's chat FACTS. The validator
publishes ``pending_review`` versions only; it never approves, ignores or executes one.
Expiry and supersession here are display state, not controller decisions.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agents.validation.lifecycle import PreviousVersion
from shared.contracts import AdvisoryPriority, AdvisoryStatus, ValidatedAdvisory
from shared.orchestration import ValidationVerdict

MAX_ADVISORIES = 256
MAX_RESULTS = 256
FACTS_RESULTS = 5
_PRIORITY_ORDER = {
    AdvisoryPriority.TIME_SENSITIVE: 0,
    AdvisoryPriority.ATTENTION: 1,
    AdvisoryPriority.INFORMATION: 2,
}


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    advisory: ValidatedAdvisory
    status: AdvisoryStatus  # pending_review, superseded or expired (display state only)


@dataclass(frozen=True, slots=True)
class InternalResult:
    case_id: str
    verdict: ValidationVerdict
    reason: str
    finding_codes: tuple[str, ...]
    recorded_at_utc: datetime


class AdvisoryLedger:
    def __init__(self) -> None:
        self._latest: OrderedDict[str, LedgerEntry] = OrderedDict()  # by advisory_id
        self._cases: set[str] = set()  # case ids already published or recorded
        self._results: OrderedDict[str, InternalResult] = OrderedDict()  # by case_id

    def previous(self, advisory_id: str, now: datetime) -> PreviousVersion | None:
        entry = self._current(advisory_id, now)
        if entry is None:
            return None
        return PreviousVersion(
            advisory_id=advisory_id,
            version=entry.advisory.version,
            material_fingerprint=entry.advisory.material_fingerprint,
            status=entry.status,
            expires_at_utc=entry.advisory.expires_at_utc,
        )

    def publish(self, advisory: ValidatedAdvisory) -> bool:
        """Record a new pending version; False if this case or version was already seen."""
        current = self._latest.get(advisory.advisory_id)
        if advisory.case_id in self._cases or (
            current is not None and current.advisory.version >= advisory.version
        ):
            return False
        self._cases.add(advisory.case_id)
        self._latest[advisory.advisory_id] = LedgerEntry(
            advisory, AdvisoryStatus.PENDING_REVIEW
        )  # replacing the entry supersedes the earlier version
        self._latest.move_to_end(advisory.advisory_id)
        while len(self._latest) > MAX_ADVISORIES:
            self._latest.popitem(last=False)
        return True

    def record_result(
        self,
        case_id: str,
        verdict: ValidationVerdict,
        reason: str,
        finding_codes: tuple[str, ...],
        now: datetime,
    ) -> bool:
        if case_id in self._cases:
            return False
        self._cases.add(case_id)
        self._results[case_id] = InternalResult(case_id, verdict, reason, finding_codes, now)
        while len(self._results) > MAX_RESULTS:
            self._results.popitem(last=False)
        if len(self._cases) > 4 * (MAX_ADVISORIES + MAX_RESULTS):
            self._cases = {e.advisory.case_id for e in self._latest.values()} | set(self._results)
        return True

    def pending(self, now: datetime) -> tuple[ValidatedAdvisory, ...]:
        current = (self._current(i, now) for i in tuple(self._latest))
        pending = [
            e.advisory
            for e in current
            if e is not None and e.status is AdvisoryStatus.PENDING_REVIEW
        ]
        return tuple(
            sorted(pending, key=lambda a: (_PRIORITY_ORDER[a.priority], a.window_start_utc))
        )

    def recent_results(self, limit: int = FACTS_RESULTS) -> tuple[InternalResult, ...]:
        return tuple(reversed(tuple(self._results.values())))[:limit]

    def facts(self, now: datetime) -> dict[str, Any]:
        """FACTS for the chat model: validated values only, no raw source records."""
        return {
            "generated_at_utc": now.isoformat(timespec="seconds"),
            "airport": "KDTW",
            "note": (
                "Advisories are suggestions awaiting human controller review in the "
                "WatchTower dashboard. The validator never approves or executes them."
            ),
            "pending_advisories": [_advisory_facts(a) for a in self.pending(now)],
            "recent_unpublished_results": [
                {
                    "case_id": r.case_id,
                    "verdict": r.verdict.value,
                    "reason": r.reason,
                    "finding_codes": list(r.finding_codes),
                    "at_utc": r.recorded_at_utc.isoformat(timespec="seconds"),
                }
                for r in self.recent_results()
            ],
        }

    def _current(self, advisory_id: str, now: datetime) -> LedgerEntry | None:
        entry = self._latest.get(advisory_id)
        if (
            entry is not None
            and entry.status is AdvisoryStatus.PENDING_REVIEW
            and entry.advisory.expires_at_utc <= now
        ):
            entry = LedgerEntry(entry.advisory, AdvisoryStatus.EXPIRED)
            self._latest[advisory_id] = entry
        return entry


def _advisory_facts(advisory: ValidatedAdvisory) -> dict[str, Any]:
    return {
        "advisory_id": advisory.advisory_id,
        "version": advisory.version,
        "type": advisory.advisory_type.value,
        "title": advisory.title,
        "suggested_response": advisory.suggested_response,
        "reason": advisory.reason,
        "priority": advisory.priority.value,
        "owner_role": advisory.owner_role,
        "affected_resource": advisory.affected_resource,
        "window_utc": [
            advisory.window_start_utc.isoformat(timespec="minutes"),
            advisory.window_end_utc.isoformat(timespec="minutes"),
        ],
        "expires_at_utc": advisory.expires_at_utc.isoformat(timespec="seconds"),
        "evidence_quality": advisory.quality.value,
        "evidence_references": len(advisory.evidence_ids),
        "projection_id": advisory.projection_id,
        "action_parameters": [
            {"name": p.name, "value": p.value, "unit": p.unit} for p in advisory.action_parameters
        ],
        "tradeoffs": list(advisory.tradeoffs),
        "limitations": list(advisory.limitations),
        "assumptions": list(advisory.assumptions),
    }
