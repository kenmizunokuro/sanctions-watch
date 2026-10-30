"""Read-only monitor for the UN Security Council's consolidated-list update log.

This is an independently fetched notification sensor, NOT a source of
screening-master additions or removals. XML is the current authoritative
snapshot, and every reported change still needs record-ID review.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

from . import un_sources as U
from .mofa_sources import Tree

UPDATES_URL = "https://main.un.org/securitycouncil/en/content/list-updates-unsc-consolidated-list"
LOOKBACK_DAYS = 120
REFERENCE = re.compile(r"\b([A-Za-z]{2})([iIeE])\.(\d{2,4})\b")
KINDS = {
    "amendment": "AMENDMENT",
    "listing": "LISTING",
    "delisting": "DELISTING",
    "relisting": "RELISTING",
    "nonrenewalofthemeasures": "NON_RENEWAL",
}


def _norm(value: str) -> str:
    return " ".join(value.split())


def _cells(row) -> list:
    return [node for node in row.children
            if not isinstance(node, str) and node.tag in {"td", "th"}]


def parse_updates(body: bytes, *, now: datetime) -> list[dict]:
    """Require real table headings, recent dated rows, explicit refs and counts.

    Old UN archive rows have inconsistent layout, so only the bounded recent
    window is interpreted. A missing or changed current row fails closed.
    """
    if now.tzinfo is None:
        raise ValueError("UN update lookup timestamp must include timezone")
    if not body or len(body) > U.MAX_BYTES:
        raise U.UNSchemaError("UN update history is empty or oversized")
    root = Tree(body).root
    table = None
    for candidate in root.walk():
        if candidate.tag != "table":
            continue
        headers = " ".join(_norm(c.text()).lower() for row in candidate.walk()
                           if row.tag == "tr" for c in _cells(row) if c.tag == "th")
        if all(label in headers for label in
               ("date", "permanent reference number", "type of update")):
            table = candidate
            break
    if table is None:
        raise U.UNSchemaError("UN updates table headings missing or changed")
    cutoff = (now.astimezone(timezone.utc).date() -
              timedelta(days=LOOKBACK_DAYS))
    seen, result, previous_date = set(), [], None
    started = False
    for row in table.walk():
        if row.tag != "tr":
            continue
        cells = _cells(row)
        if not cells or all(c.tag == "th" for c in cells):
            continue
        fields = [_norm(c.text()) for c in cells]
        if len(fields) < 5 or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", fields[0]):
            # A historical multiline legacy row is permitted only after
            # the bounded recent window is already fully traversed.
            if started:
                raise U.UNSchemaError("UN updates recent row format changed")
            continue
        try:
            day = datetime.strptime(fields[0], "%d/%m/%Y").date()
        except ValueError as exc:
            raise U.UNSchemaError("invalid UN update date") from exc
        if day < cutoff:
            break
        started = True
        if previous_date is not None and day > previous_date:
            raise U.UNSchemaError("UN updates ordering changed")
        if day > now.astimezone(timezone.utc).date() + timedelta(days=1):
            raise U.UNSchemaError("UN update future-dated row")
        previous_date = day
        regime = fields[1]
        if regime not in U.COMMITTEES.values():
            raise U.UNSchemaError("unknown UN update regime: " + regime)
        refs = [p.upper() + k.lower() + "." + n.zfill(3)
                for p, k, n in REFERENCE.findall(fields[2])]
        if len(refs) != len(set(refs)) or not refs:
            raise U.UNSchemaError("UN updates missing or duplicate references")
        kind = KINDS.get(re.sub(r"[^a-z]", "", fields[3].lower()))
        if not kind:
            raise U.UNSchemaError("unknown UN update type: " + fields[3])
        if not fields[4].isdigit() or int(fields[4]) != len(refs):
            raise U.UNSchemaError("UN updates reference/count mismatch: " + day.isoformat())
        for ref in refs:
            if U.COMMITTEES.get(ref[:2]) != regime:
                raise U.UNSchemaError("UN update regime/reference mismatch: " + ref)
        # The history page is the guaranteed evidence URL. Notification links
        # are accepted only if they appear in its original table cells.
        notice_url = ""
        for cell in cells[5:]:
            for anchor in cell.walk():
                if anchor.tag != "a" or not anchor.attrs.get("href"):
                    continue
                candidate = urljoin(UPDATES_URL, anchor.attrs["href"])
                try:
                    U.validate_un_url(candidate)
                except ValueError as exc:
                    raise U.UNSchemaError("UN notification link not official") from exc
                notice_url = candidate
                break
            if notice_url:
                break
        item = {
            "publication_date": day.isoformat(),
            "regime": regime,
            "references": sorted(refs),
            "change": kind,
            "record_count": len(refs),
            "notice_url": notice_url,
            "source_url": UPDATES_URL,
        }
        item["event_id"] = U.digest({key: item[key] for key in
                                     ("publication_date", "regime", "references", "change")})
        if item["event_id"] in seen:
            raise U.UNSchemaError("duplicate UN updates history event")
        seen.add(item["event_id"])
        result.append(item)
    if not result:
        raise U.UNSchemaError("no recent dated UN update history rows")
    return result
