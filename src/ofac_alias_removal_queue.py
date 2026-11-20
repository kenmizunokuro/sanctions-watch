"""Persistent human-review queue and approval ledger for OFAC alias/variant removals.

When an active OFAC Party removes, renames, or downgrades an alias (or removes
the structured name parts that previously supported a retained person-order
variant), screening keeps the existing active master row intact and queues an
exact snapshot-bound review event instead of blocking new OFAC designations.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableSequence, Sequence, Set, Tuple

from . import master as M
from . import ofac_removal as OR
from . import ofac_removal_queue as ORQ
from .normalize import canonical_display_name, match_key


QUEUE_REL = Path("data/review/ofac_alias_removal_queue.csv")
APPROVALS_REL = Path("data/review/ofac_alias_removal_approvals.csv")
AUDIT_REL = Path("data/review/ofac_alias_removal_audit.csv")

FIELDS = [
    "event_id",
    "list",
    "snapshot_sha256",
    "party_id",
    "match_key",
    "alias_name",
    "relation_kind",
    "status",
    "detected_at_ms",
    "last_seen_at_ms",
    "resolved_at_ms",
    "resolution",
]

APPROVAL_FIELDS = [
    "list",
    "snapshot_sha256",
    "party_id",
    "match_key",
    "alias_name",
    "decision",
    "approved_by",
    "approved_at",
    "official_url",
    "notes",
]

AUDIT_FIELDS = [
    "applied_at",
    "list",
    "snapshot_sha256",
    "party_id",
    "match_key",
    "alias_name",
    "decision",
    "approved_by",
    "approved_at",
    "official_url",
    "notes",
    "action",
    "before_json",
    "after_json",
    "master_before_sha256",
    "master_after_sha256",
    "index_before_sha256",
    "index_after_sha256",
]

PENDING_REVIEW = "PENDING_REVIEW"
APPLIED = "APPLIED"
CANCELLED_REAPPEARED = "CANCELLED_REAPPEARED"

STATUSES = {
    PENDING_REVIEW,
    APPLIED,
    CANCELLED_REAPPEARED,
}

DECISION_DELIST = "DELIST"
DECISION_RETAIN = "RETAIN"
DECISIONS = {DECISION_DELIST, DECISION_RETAIN}

RESOLUTION_DELIST = "APPROVED_DELIST"
RESOLUTION_RETAIN = "APPROVED_RETAIN"
RESOLUTION_REAPPEARED = "REAPPEARED"

RELATION_ALIAS_REMOVED = "alias_removed"
RELATION_ALIAS_DOWNGRADED = "alias_downgraded_weak"
RELATION_PERSON_FORM_REMOVED = "person_form_removed"
RELATION_KINDS = {
    RELATION_ALIAS_REMOVED,
    RELATION_ALIAS_DOWNGRADED,
    RELATION_PERSON_FORM_REMOVED,
}
_RELATION_PRIORITY = {
    RELATION_ALIAS_REMOVED: 0,
    RELATION_ALIAS_DOWNGRADED: 1,
    RELATION_PERSON_FORM_REMOVED: 2,
}

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EXEMPT_MARKERS = (
    "LEGACY_UNVERIFIED_V1:",
    "OFAC_ALIAS_HISTORY_V1:",
    "OFAC_VESSEL_HISTORY_V1:",
)

ItemKey = Tuple[str, str, str]  # (list, party_id, match_key)
ApprovalKey = Tuple[str, str, str, str]  # (list, snapshot_sha256, party_id, match_key)


class QueueError(RuntimeError):
    """The OFAC alias removal queue is malformed or unsafe to transition."""


class ApprovalError(RuntimeError):
    """The OFAC alias removal approval ledger is malformed or mismatched."""


class AuditError(RuntimeError):
    """The OFAC alias removal audit ledger cannot be written safely."""


@dataclass(frozen=True)
class Candidate:
    list_name: str
    party_id: str
    match_key: str
    alias_name: str
    relation_kind: str


@dataclass(frozen=True)
class AliasQueueDiff:
    created_events: Set[str] = field(default_factory=set)
    cancelled_items: Set[ItemKey] = field(default_factory=set)


@dataclass(frozen=True)
class Approval:
    list_name: str
    snapshot_sha256: str
    party_id: str
    match_key: str
    alias_name: str
    decision: str
    approved_by: str
    approved_at: str
    official_url: str
    notes: str


def _split(value: str) -> list[str]:
    return [item for item in str(value or "").split(";") if item]


def _join(values: Iterable[str]) -> str:
    return ";".join(sorted({str(value) for value in values if str(value)}))


def _timestamp(value: object, field_name: str) -> str:
    rendered = str(value)
    if not rendered.isdigit() or int(rendered) < 0:
        raise QueueError(
            f"OFAC alias removal queue {field_name} must be a non-negative integer: {value!r}"
        )
    return rendered


def _event_id(
    list_name: str,
    snapshot_sha256: str,
    party_id: str,
    key: str,
    *,
    detected_at_ms: str = "",
) -> str:
    body = {
        "list": list_name,
        "match_key": key,
        "party_id": str(party_id),
        "snapshot_sha256": snapshot_sha256,
    }
    if detected_at_ms:
        body["detected_at_ms"] = str(detected_at_ms)
    payload = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_row(row: Mapping[str, object], row_number: int) -> dict:
    normalized = {
        field_name: str(row.get(field_name, "") or "").strip()
        for field_name in FIELDS
    }
    required = [
        "event_id",
        "list",
        "snapshot_sha256",
        "party_id",
        "match_key",
        "alias_name",
        "relation_kind",
        "status",
        "detected_at_ms",
        "last_seen_at_ms",
    ]
    missing = [name for name in required if not normalized[name]]
    if missing:
        raise QueueError(
            f"OFAC alias removal queue row {row_number} has empty fields: {missing}"
        )
    if not _SHA256_RE.fullmatch(normalized["event_id"]):
        raise QueueError(
            f"OFAC alias removal queue row {row_number} event_id is invalid"
        )
    if not _SHA256_RE.fullmatch(normalized["snapshot_sha256"]):
        raise QueueError(
            f"OFAC alias removal queue row {row_number} snapshot SHA256 is invalid"
        )
    if normalized["list"] not in {"SDN", "Consolidated"}:
        raise QueueError(
            f"OFAC alias removal queue row {row_number} list is invalid: {normalized['list']!r}"
        )
    if normalized["relation_kind"] not in RELATION_KINDS:
        raise QueueError(
            f"OFAC alias removal queue row {row_number} relation_kind is invalid: "
            f"{normalized['relation_kind']!r}"
        )
    if normalized["status"] not in STATUSES:
        raise QueueError(
            f"OFAC alias removal queue row {row_number} status is invalid: "
            f"{normalized['status']!r}"
        )

    normalized["alias_name"] = canonical_display_name(normalized["alias_name"])
    if match_key(normalized["alias_name"]) != normalized["match_key"]:
        raise QueueError(
            f"OFAC alias removal queue row {row_number} alias_name/match_key mismatch: "
            f"{normalized['alias_name']!r} != {normalized['match_key']!r}"
        )
    normalized["detected_at_ms"] = _timestamp(
        normalized["detected_at_ms"], "detected_at_ms"
    )
    normalized["last_seen_at_ms"] = _timestamp(
        normalized["last_seen_at_ms"], "last_seen_at_ms"
    )
    if int(normalized["last_seen_at_ms"]) < int(normalized["detected_at_ms"]):
        raise QueueError(
            f"OFAC alias removal queue row {row_number} last_seen precedes detection"
        )

    if normalized["status"] == PENDING_REVIEW:
        if normalized["resolved_at_ms"] or normalized["resolution"]:
            raise QueueError(
                f"OFAC alias removal queue row {row_number} pending row is resolved"
            )
    else:
        normalized["resolved_at_ms"] = _timestamp(
            normalized["resolved_at_ms"], "resolved_at_ms"
        )
        if int(normalized["resolved_at_ms"]) < int(normalized["detected_at_ms"]):
            raise QueueError(
                f"OFAC alias removal queue row {row_number} resolution precedes detection"
            )
        valid_resolutions = (
            {RESOLUTION_DELIST, RESOLUTION_RETAIN}
            if normalized["status"] == APPLIED
            else {RESOLUTION_REAPPEARED}
        )
        if normalized["resolution"] not in valid_resolutions:
            raise QueueError(
                f"OFAC alias removal queue row {row_number} resolution is invalid: "
                f"{normalized['resolution']!r}"
            )
    return normalized


def _validated(rows: Iterable[Mapping[str, object]]) -> List[dict]:
    normalized = [
        _normalized_row(row, row_number)
        for row_number, row in enumerate(rows, start=2)
    ]
    seen_events: Set[str] = set()
    open_items: Set[ItemKey] = set()
    by_event: Dict[str, dict] = {}

    for row in normalized:
        event_id = row["event_id"]
        if event_id in seen_events:
            raise QueueError(
                f"OFAC alias removal queue has duplicate event_id: {event_id}"
            )
        seen_events.add(event_id)
        by_event[event_id] = row

        if row["status"] == PENDING_REVIEW:
            item = (row["list"], row["party_id"], row["match_key"])
            if item in open_items:
                raise QueueError(
                    "OFAC alias removal queue has multiple open events for one item: "
                    f"{item}"
                )
            open_items.add(item)

    for row in normalized:
        base_id = _event_id(
            row["list"],
            row["snapshot_sha256"],
            row["party_id"],
            row["match_key"],
        )
        salted_id = _event_id(
            row["list"],
            row["snapshot_sha256"],
            row["party_id"],
            row["match_key"],
            detected_at_ms=row["detected_at_ms"],
        )
        if row["event_id"] == base_id:
            continue
        prior = by_event.get(base_id)
        if (
            row["event_id"] == salted_id
            and prior is not None
            and prior["status"] != PENDING_REVIEW
        ):
            continue
        raise QueueError(
            "OFAC alias removal queue event_id does not match its item binding: "
            f"event_id={row['event_id']} expected={base_id}"
        )

    normalized.sort(
        key=lambda row: (
            int(row["detected_at_ms"]),
            row["event_id"],
        )
    )
    return normalized


def load(path: Path) -> List[dict]:
    """Load and strictly validate the alias queue; missing file -> empty list."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDS:
            raise QueueError(
                "OFAC alias removal queue schema mismatch: "
                f"expected={FIELDS} actual={reader.fieldnames}"
            )
        return _validated(reader)


def save(rows: Iterable[Mapping[str, object]], path: Path) -> None:
    """Write validated alias removal queue in deterministic order."""
    normalized = _validated(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(normalized)


def pending_rows(rows: Iterable[Mapping[str, object]]) -> List[dict]:
    """Return validated PENDING_REVIEW rows."""
    return [row for row in _validated(rows) if row["status"] == PENDING_REVIEW]


def pending_lists(rows: Iterable[Mapping[str, object]]) -> Set[str]:
    """Return OFAC list labels ('SDN', 'Consolidated') that have open alias reviews."""
    return {row["list"] for row in pending_rows(rows)}


def detect_candidates(
    master_rows: Mapping[str, Mapping[str, object]],
    previous_history: Mapping[Tuple[str, str, str], Mapping[str, object]],
    current_history: Mapping[Tuple[str, str, str], Mapping[str, object]],
    *,
    previous_person_forms: Sequence[Mapping[str, object]] = (),
    current_person_forms: Sequence[Mapping[str, object]] = (),
    party_queue_parties: Set[Tuple[str, str]] | None = None,
) -> Set[Candidate]:
    """Detect active OFAC master rows whose current OFAC alias/variant disappeared."""
    party_queued = {
        (str(list_name), str(party_id))
        for list_name, party_id in (party_queue_parties or set())
    }
    current_strong_keys = {
        str(item.get("match_key") or "").strip()
        for item in current_history.values()
        if (
            item.get("party_current") == "1"
            and item.get("alias_current") == "1"
            and item.get("low_quality") != "1"
            and str(item.get("match_key") or "").strip()
        )
    }
    party_queued_strong_keys = {
        str(item.get("match_key") or "").strip()
        for item in current_history.values()
        if (
            (str(item.get("list") or ""), str(item.get("party_id") or ""))
            in party_queued
            and item.get("low_quality") != "1"
            and str(item.get("match_key") or "").strip()
        )
    }
    current_form_keys = {
        match_key(str(form.get("name") or ""))
        for form in current_person_forms
        if match_key(str(form.get("name") or ""))
    }

    curr_by_key: Dict[str, List[Tuple[Tuple[str, str, str], Mapping[str, object]]]] = {}
    for hist_key, item in current_history.items():
        k = str(item.get("match_key") or "").strip()
        if k:
            curr_by_key.setdefault(k, []).append((hist_key, item))

    prev_forms_by_key: Dict[str, List[Mapping[str, object]]] = {}
    for form in previous_person_forms:
        k = match_key(str(form.get("name") or ""))
        if k:
            prev_forms_by_key.setdefault(k, []).append(form)

    chosen: Dict[ItemKey, Candidate] = {}

    def record_candidate(cand: Candidate) -> None:
        item_key = (cand.list_name, cand.party_id, cand.match_key)
        existing = chosen.get(item_key)
        if existing is None or (
            _RELATION_PRIORITY[cand.relation_kind]
            < _RELATION_PRIORITY[existing.relation_kind]
        ):
            chosen[item_key] = cand

    for key, mrow in master_rows.items():
        if str(mrow.get("status") or "").strip() != M.STATUS_ACTIVE:
            continue
        claimed_sources = _split(str(mrow.get("sources") or ""))
        claimed_cats = _split(str(mrow.get("categories") or ""))
        if "OFAC" not in claimed_sources and not any(
            cat.startswith("OFAC:") for cat in claimed_cats
        ):
            continue
        flags = str(mrow.get("review_flag") or "")
        if any(marker in flags for marker in _EXEMPT_MARKERS):
            continue
        if key in current_strong_keys or key in party_queued_strong_keys:
            continue

        for hist_key, item in curr_by_key.get(key, ()):
            prev_item = previous_history.get(hist_key)
            if prev_item is None or prev_item.get("alias_current") != "1":
                continue
            list_name = str(item.get("list") or "")
            party_id = str(item.get("party_id") or "")
            alias_surface = canonical_display_name(
                str(item.get("name") or mrow.get("display_name") or "")
            )
            if match_key(alias_surface) != key:
                alias_surface = canonical_display_name(
                    str(mrow.get("display_name") or "")
                )
            if item.get("party_current") == "1" and item.get("alias_current") == "0":
                record_candidate(
                    Candidate(
                        list_name=list_name,
                        party_id=party_id,
                        match_key=key,
                        alias_name=alias_surface,
                        relation_kind=RELATION_ALIAS_REMOVED,
                    )
                )
            elif (
                item.get("party_current") == "1"
                and item.get("alias_current") == "1"
                and item.get("low_quality") == "1"
                and prev_item.get("low_quality") != "1"
            ):
                record_candidate(
                    Candidate(
                        list_name=list_name,
                        party_id=party_id,
                        match_key=key,
                        alias_name=alias_surface,
                        relation_kind=RELATION_ALIAS_DOWNGRADED,
                    )
                )

        if key not in current_form_keys:
            for form in prev_forms_by_key.get(key, ()):
                list_name = str(form.get("category") or "")
                party_id = str(form.get("source_id") or "")
                if not list_name or not party_id:
                    continue
                alias_surface = canonical_display_name(
                    str(mrow.get("display_name") or form.get("name") or "")
                )
                record_candidate(
                    Candidate(
                        list_name=list_name,
                        party_id=party_id,
                        match_key=key,
                        alias_name=alias_surface,
                        relation_kind=RELATION_PERSON_FORM_REMOVED,
                    )
                )

    return set(chosen.values())


def reconcile(
    rows: MutableSequence[dict],
    candidates: Iterable[Candidate],
    *,
    current_strong_keys: Set[str],
    current_person_form_keys: Set[str] | None = None,
    snapshot_hashes: Mapping[str, str],
    ts: int,
) -> AliasQueueDiff:
    """Reconcile open alias-removal events against one complete OFAC snapshot."""
    timestamp = _timestamp(ts, "transition timestamp")
    staged = copy.deepcopy(_validated(rows))
    strong = {str(k).strip() for k in current_strong_keys if str(k).strip()}
    person_forms = {
        str(k).strip() for k in (current_person_form_keys or set()) if str(k).strip()
    }
    normalized_hashes = {
        str(list_name): str(snapshot).strip()
        for list_name, snapshot in snapshot_hashes.items()
    }

    created_events: Set[str] = set()
    cancelled_items: Set[ItemKey] = set()

    for row in staged:
        if row["status"] != PENDING_REVIEW:
            continue
        key = row["match_key"]
        reappeared = key in strong or (
            row["relation_kind"] == RELATION_PERSON_FORM_REMOVED
            and key in person_forms
        )
        if reappeared:
            row["status"] = CANCELLED_REAPPEARED
            row["resolved_at_ms"] = timestamp
            row["resolution"] = RESOLUTION_REAPPEARED
            cancelled_items.add((row["list"], row["party_id"], key))
        elif row["list"] in normalized_hashes:
            row["last_seen_at_ms"] = timestamp

    open_items = {
        (row["list"], row["party_id"], row["match_key"])
        for row in staged
        if row["status"] == PENDING_REVIEW
    }
    existing_event_ids = {row["event_id"] for row in staged}

    for cand in sorted(
        candidates,
        key=lambda c: (c.list_name, c.party_id, c.match_key, c.relation_kind),
    ):
        item_key = (cand.list_name, cand.party_id, cand.match_key)
        if item_key in open_items:
            continue
        if cand.match_key in strong or (
            cand.relation_kind == RELATION_PERSON_FORM_REMOVED
            and cand.match_key in person_forms
        ):
            continue
        snapshot = normalized_hashes.get(cand.list_name, "")
        if not _SHA256_RE.fullmatch(snapshot):
            raise QueueError(
                f"{cand.list_name} alias removal queue snapshot SHA256 is invalid or missing"
            )
        event_id = _event_id(
            cand.list_name,
            snapshot,
            cand.party_id,
            cand.match_key,
        )
        if event_id in existing_event_ids:
            event_id = _event_id(
                cand.list_name,
                snapshot,
                cand.party_id,
                cand.match_key,
                detected_at_ms=timestamp,
            )
        if event_id in existing_event_ids:
            raise QueueError(
                f"OFAC alias removal queue would reuse an existing event_id: {event_id}"
            )
        staged.append({
            "event_id": event_id,
            "list": cand.list_name,
            "snapshot_sha256": snapshot,
            "party_id": cand.party_id,
            "match_key": cand.match_key,
            "alias_name": canonical_display_name(cand.alias_name),
            "relation_kind": cand.relation_kind,
            "status": PENDING_REVIEW,
            "detected_at_ms": timestamp,
            "last_seen_at_ms": timestamp,
            "resolved_at_ms": "",
            "resolution": "",
        })
        existing_event_ids.add(event_id)
        open_items.add(item_key)
        created_events.add(event_id)

    validated = _validated(staged)
    rows[:] = validated
    return AliasQueueDiff(
        created_events=created_events,
        cancelled_items=cancelled_items,
    )


def _load_approvals(path: Path) -> List[Approval]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != APPROVAL_FIELDS:
            raise ApprovalError(
                "OFAC別名削除承認台帳の列構造が不一致: "
                f"expected={APPROVAL_FIELDS} actual={reader.fieldnames}"
            )
        approvals: List[Approval] = []
        seen: Set[ApprovalKey] = set()
        for row_number, row in enumerate(reader, start=2):
            list_name = str(row.get("list") or "").strip()
            snapshot_sha256 = str(row.get("snapshot_sha256") or "").strip()
            party_id = str(row.get("party_id") or "").strip()
            key = str(row.get("match_key") or "").strip()
            alias_name = canonical_display_name(row.get("alias_name") or "")
            decision = str(row.get("decision") or "").strip()
            approved_by = str(row.get("approved_by") or "").strip()
            approved_at = str(row.get("approved_at") or "").strip()
            official_url = str(row.get("official_url") or "").strip()
            notes = str(row.get("notes") or "").strip()

            required = {
                "list": list_name,
                "party_id": party_id,
                "match_key": key,
                "alias_name": alias_name,
                "decision": decision,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "official_url": official_url,
            }
            missing = [k for k, v in required.items() if not v]
            if missing:
                raise ApprovalError(
                    f"別名承認台帳 {row_number}行目の必須項目が空: {missing}"
                )
            if list_name not in {"SDN", "Consolidated"}:
                raise ApprovalError(
                    f"別名承認台帳 {row_number}行目 list が不正: {list_name!r}"
                )
            if not _SHA256_RE.fullmatch(snapshot_sha256):
                raise ApprovalError(
                    f"別名承認台帳 {row_number}行目 snapshot_sha256 が不正"
                )
            if match_key(alias_name) != key:
                raise ApprovalError(
                    f"別名承認台帳 {row_number}行目 alias_name と match_key が不一致"
                )
            if decision not in DECISIONS:
                raise ApprovalError(
                    f"別名承認台帳 {row_number}行目 decision は DELIST または RETAIN 必須: "
                    f"{decision!r}"
                )
            try:
                OR._validate_timestamp(approved_at, row_number)
                OR._validate_official_url(official_url, row_number)
            except OR.ApprovalError as exc:
                raise ApprovalError(str(exc)) from exc

            approval_key = (list_name, snapshot_sha256, party_id, key)
            if approval_key in seen:
                raise ApprovalError(
                    f"OFAC別名削除承認が重複: {approval_key}"
                )
            seen.add(approval_key)
            approvals.append(
                Approval(
                    list_name=list_name,
                    snapshot_sha256=snapshot_sha256,
                    party_id=party_id,
                    match_key=key,
                    alias_name=alias_name,
                    decision=decision,
                    approved_by=approved_by,
                    approved_at=approved_at,
                    official_url=official_url,
                    notes=notes,
                )
            )
    return approvals


def load_available_approvals(
    path: Path,
    queue_rows: Iterable[Mapping[str, object]],
) -> List[Approval]:
    """Return validated approvals matching currently open PENDING_REVIEW rows."""
    open_rows = {
        (row["list"], row["snapshot_sha256"], row["party_id"], row["match_key"]): row
        for row in pending_rows(queue_rows)
    }
    if not open_rows or not path.is_file():
        return []
    approvals = _load_approvals(path)
    matched: List[Approval] = []
    for item in approvals:
        qkey = (
            item.list_name,
            item.snapshot_sha256,
            item.party_id,
            item.match_key,
        )
        qrow = open_rows.get(qkey)
        if qrow is None:
            continue
        if qrow["alias_name"] != item.alias_name:
            raise ApprovalError(
                "OFAC別名削除承認のalias_nameがキューと不一致: "
                f"expected={qrow['alias_name']!r} actual={item.alias_name!r}"
            )
        matched.append(item)
    matched.sort(
        key=lambda a: (a.list_name, a.snapshot_sha256, a.party_id, a.match_key)
    )
    return matched


def apply_to_master(
    rows: Dict[str, dict],
    history: Mapping[Tuple[str, str, str], Mapping[str, object]],
    approvals: Sequence[Approval],
    *,
    protected_keys: Set[str] | None = None,
    ts: int,
) -> Tuple[M.Diff, List[dict]]:
    """Apply approved DELIST / RETAIN decisions to master rows."""
    protected = set(protected_keys or set())
    current_strong_keys = {
        str(item.get("match_key") or "").strip()
        for item in history.values()
        if (
            item.get("party_current") == "1"
            and item.get("alias_current") == "1"
            and item.get("low_quality") != "1"
            and str(item.get("match_key") or "").strip()
        )
    }

    diff = M.Diff(source="OFAC")
    effects: List[dict] = []

    for approval in approvals:
        key = approval.match_key
        row = rows.get(key)
        if row is None:
            effects.append({
                "approval": approval,
                "action": "master_row_absent",
                "before": {},
                "after": {},
            })
            continue

        before = {field_name: str(row.get(field_name, "") or "") for field_name in M.DIFF_AUDIT_FIELDS}
        display_name = str(row.get("display_name") or "")

        if approval.decision == DECISION_RETAIN:
            effects.append({
                "approval": approval,
                "action": "retained_active",
                "before": before,
                "after": before,
            })
            continue

        if key in current_strong_keys:
            effects.append({
                "approval": approval,
                "action": "kept_current_ofac_support",
                "before": before,
                "after": before,
            })
            continue

        if key in protected:
            effects.append({
                "approval": approval,
                "action": "kept_other_pending_review",
                "before": before,
                "after": before,
            })
            continue

        sources = _split(row.get("sources", ""))
        if "OFAC" not in sources:
            effects.append({
                "approval": approval,
                "action": "ofac_source_absent",
                "before": before,
                "after": before,
            })
            continue

        was_active = row.get("status") == M.STATUS_ACTIVE
        remaining_sources = [s for s in sources if s != "OFAC"]
        remaining_categories = [
            c for c in _split(row.get("categories", "")) if not c.startswith("OFAC:")
        ]
        row["sources"] = _join(remaining_sources)
        row["categories"] = _join(remaining_categories)
        if not remaining_sources:
            row["status"] = M.STATUS_INACTIVE
            row["invalid_reason"] = M.DELISTED
            row["review_flag"] = _join(
                f for f in _split(row.get("review_flag", ""))
                if f not in M.OFAC_WEAK_ALIAS_FLAGS
            )
            action = "deactivated"
        else:
            action = "removed_ofac_source"

        M._recompute(row)
        row["last_updated_ms"] = str(ts)
        after = {field_name: str(row.get(field_name, "") or "") for field_name in M.DIFF_AUDIT_FIELDS}

        if was_active:
            diff.removed.append({
                "key": key,
                "name": display_name,
                "before": before,
                "after": after,
                "delisted": True,
            })
        elif before != after:
            diff.changed.append({
                "key": key,
                "name": display_name,
                "before": before,
                "after": after,
            })

        effects.append({
            "approval": approval,
            "action": action,
            "before": before,
            "after": after,
        })

    return diff, effects


def mark_applied(
    rows: MutableSequence[dict],
    approvals: Sequence[Approval],
    ts: int,
) -> None:
    """Mark exact approved alias-removal queue rows APPLIED."""
    timestamp = _timestamp(ts, "transition timestamp")
    staged = copy.deepcopy(_validated(rows))
    available = {
        (row["list"], row["snapshot_sha256"], row["party_id"], row["match_key"]): row
        for row in staged
        if row["status"] == PENDING_REVIEW
    }
    for approval in approvals:
        key = (
            approval.list_name,
            approval.snapshot_sha256,
            approval.party_id,
            approval.match_key,
        )
        row = available.get(key)
        if row is None:
            raise QueueError(
                f"alias approval does not identify a pending queue row: {key}"
            )
        row["status"] = APPLIED
        row["resolved_at_ms"] = timestamp
        row["resolution"] = (
            RESOLUTION_DELIST
            if approval.decision == DECISION_DELIST
            else RESOLUTION_RETAIN
        )
    rows[:] = _validated(staged)


def build_audit_rows(
    effects: Sequence[dict],
    *,
    master_before_sha256: str,
    master_after_sha256: str,
    index_before_sha256: str,
    index_after_sha256: str,
) -> List[dict]:
    hashes = {
        "master_before_sha256": master_before_sha256,
        "master_after_sha256": master_after_sha256,
        "index_before_sha256": index_before_sha256,
        "index_after_sha256": index_after_sha256,
    }
    invalid = [k for k, v in hashes.items() if not _SHA256_RE.fullmatch(str(v or ""))]
    if invalid:
        raise AuditError(f"OFAC別名削除適用監査の状態hashが不正: {invalid}")

    out: List[dict] = []
    for effect in effects:
        approval: Approval = effect["approval"]
        row = {f: "" for f in AUDIT_FIELDS}
        row.update({
            "list": approval.list_name,
            "snapshot_sha256": approval.snapshot_sha256,
            "party_id": approval.party_id,
            "match_key": approval.match_key,
            "alias_name": approval.alias_name,
            "decision": approval.decision,
            "approved_by": approval.approved_by,
            "approved_at": approval.approved_at,
            "official_url": approval.official_url,
            "notes": approval.notes,
            "action": str(effect.get("action") or ""),
            "before_json": json.dumps(
                effect.get("before") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "after_json": json.dumps(
                effect.get("after") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            **hashes,
        })
        out.append(row)
    return out


def append_audit(path: Path, rows: Sequence[dict], *, applied_at: datetime) -> None:
    if not rows:
        return
    if applied_at.tzinfo is None:
        raise AuditError("OFAC別名削除適用日時にtimezoneが無い")
    path.parent.mkdir(parents=True, exist_ok=True)
    has_content = path.exists() and path.stat().st_size > 0
    if has_content:
        with path.open("r", encoding="utf-8", newline="") as handle:
            actual = next(csv.reader(handle), [])
        if actual != AUDIT_FIELDS:
            raise AuditError(
                "OFAC別名削除適用監査の列構造が不一致: "
                f"expected={AUDIT_FIELDS} actual={actual}"
            )
    timestamp = applied_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=AUDIT_FIELDS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        if not has_content:
            writer.writeheader()
        for source_row in rows:
            row = {f: "" for f in AUDIT_FIELDS}
            row.update(source_row)
            row["applied_at"] = timestamp
            writer.writerow(row)


def apply_audit_relief(
    root: Path,
    report: dict,
    *,
    expected_records: Sequence[Mapping[str, object]],
    index_rows: Sequence[Mapping[str, object]],
) -> dict:
    """Release blocking status for verified OFAC pending/retained removal queue items.

    Findings remain visible in ``report['findings']`` (so audit status is ``REVIEW``),
    while ``blocking=False`` prevents unapproved OFAC party/alias removals from
    halting publication of new OFAC designations.
    """
    from .integrity_audit import _finish, sha

    result = copy.deepcopy(report)
    party_queue_path = root / "data/review/ofac_party_removal_queue.csv"
    alias_queue_path = root / QUEUE_REL
    alias_approvals_path = root / APPROVALS_REL

    if (
        not party_queue_path.exists()
        and not alias_queue_path.exists()
    ):
        return _finish(result)

    official_parties: Set[Tuple[str, str]] = set()
    strong_keys: Set[str] = set()
    for rec in expected_records:
        if rec.get("source") != "OFAC":
            continue
        official_parties.add((str(rec.get("category") or ""), str(rec.get("source_id") or "")))
        if not rec.get("weak"):
            k = match_key(str(rec.get("name") or ""))
            if k:
                strong_keys.add(k)

    party_queue_rows: List[dict] = []
    if party_queue_path.exists():
        body = party_queue_path.read_bytes()
        result.setdefault("inputs", {})[
            "data/review/ofac_party_removal_queue.csv"
        ] = sha(body)
        party_queue_rows = ORQ.load(party_queue_path)

    alias_queue_rows: List[dict] = []
    if alias_queue_path.exists():
        body = alias_queue_path.read_bytes()
        result.setdefault("inputs", {})[QUEUE_REL.as_posix()] = sha(body)
        alias_queue_rows = load(alias_queue_path)

    approved_retains: Dict[ApprovalKey, Approval] = {}
    if alias_approvals_path.exists():
        body = alias_approvals_path.read_bytes()
        result.setdefault("inputs", {})[APPROVALS_REL.as_posix()] = sha(body)
        for item in _load_approvals(alias_approvals_path):
            if item.decision == DECISION_RETAIN:
                approved_retains[
                    (
                        item.list_name,
                        item.snapshot_sha256,
                        item.party_id,
                        item.match_key,
                    )
                ] = item

    pending_party_by_key: Dict[str, List[dict]] = {}
    open_parties = {
        (row["list"], row["party_id"]): row
        for row in party_queue_rows
        if row["status"] == ORQ.PENDING_REVIEW
    }
    if open_parties:
        for party in open_parties:
            if party in official_parties:
                raise QueueError(
                    f"OFAC party removal queue has uncancelled current party: {party}"
                )
        for idx_row in index_rows:
            party = (str(idx_row.get("list") or ""), str(idx_row.get("party_id") or ""))
            k = str(idx_row.get("match_key") or "").strip()
            if (
                party in open_parties
                and k
                and idx_row.get("party_current") == "0"
                and idx_row.get("alias_current") == "0"
            ):
                pending_party_by_key.setdefault(k, []).append(open_parties[party])

    index_by_item: Dict[ItemKey, List[Mapping[str, object]]] = {}
    for idx_row in index_rows:
        item_key = (
            str(idx_row.get("list") or ""),
            str(idx_row.get("party_id") or ""),
            str(idx_row.get("match_key") or "").strip(),
        )
        if item_key[2]:
            index_by_item.setdefault(item_key, []).append(idx_row)

    pending_alias_by_key: Dict[str, List[dict]] = {}
    retained_alias_by_key: Dict[str, List[Tuple[dict, Approval]]] = {}
    for row in alias_queue_rows:
        k = row["match_key"]
        item_key = (row["list"], row["party_id"], k)
        if row["status"] == PENDING_REVIEW:
            if k in strong_keys:
                raise QueueError(
                    f"OFAC alias removal queue has uncancelled current strong alias: {item_key}"
                )
            if row["relation_kind"] != RELATION_PERSON_FORM_REMOVED:
                matches = index_by_item.get(item_key, [])
                if not any(
                    m.get("alias_current") == "0" or m.get("low_quality") == "1"
                    for m in matches
                ):
                    raise QueueError(
                        f"OFAC alias removal queue item lacks index witness: {item_key}"
                    )
            pending_alias_by_key.setdefault(k, []).append(row)
        elif row["status"] == APPLIED and row["resolution"] == RESOLUTION_RETAIN:
            akey = (
                row["list"],
                row["snapshot_sha256"],
                row["party_id"],
                k,
            )
            approval = approved_retains.get(akey)
            if approval is None:
                raise ApprovalError(
                    f"OFAC alias queue APPROVED_RETAIN lacks approval row: {akey}"
                )
            retained_alias_by_key.setdefault(k, []).append((row, approval))

    for finding in result.get("findings", []):
        if not finding.get("blocking", True):
            continue
        kind = finding.get("kind")
        if kind not in {"EXTRA_REGISTRATION", "WEAK_ALIAS_ACTIVE"}:
            continue
        checked = finding.get("checked_source") or (
            finding.get("official") or {}
        ).get("source")
        if checked != "OFAC":
            continue
        master = finding.get("master")
        if not isinstance(master, dict) or master.get("status") != M.STATUS_ACTIVE:
            continue
        key = str(master.get("match_key") or "").strip()
        if not key or key in strong_keys:
            continue

        if key in pending_party_by_key:
            qrow = pending_party_by_key[key][0]
            finding["blocking"] = False
            finding["pending_removal_review"] = {
                "queue": "party",
                "event_id": qrow["event_id"],
                "list": qrow["list"],
                "party_id": qrow["party_id"],
                "snapshot_sha256": qrow["snapshot_sha256"],
            }
        elif key in pending_alias_by_key:
            qrow = pending_alias_by_key[key][0]
            finding["blocking"] = False
            finding["pending_removal_review"] = {
                "queue": "alias",
                "event_id": qrow["event_id"],
                "list": qrow["list"],
                "party_id": qrow["party_id"],
                "relation_kind": qrow["relation_kind"],
                "snapshot_sha256": qrow["snapshot_sha256"],
            }
        elif key in retained_alias_by_key:
            qrow, approval = retained_alias_by_key[key][0]
            finding["blocking"] = False
            finding["approved_alias_retain"] = {
                "event_id": qrow["event_id"],
                "list": qrow["list"],
                "party_id": qrow["party_id"],
                "snapshot_sha256": qrow["snapshot_sha256"],
                "approved_by": approval.approved_by,
                "approved_at": approval.approved_at,
                "official_url": approval.official_url,
            }

    return _finish(result)
