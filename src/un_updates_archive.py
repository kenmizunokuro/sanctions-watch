"""Read a repository-reviewed receipt for an alternate UN history capture."""
from __future__ import annotations

import gzip
import hashlib
import json
import re
import zlib

from . import un_press_rss as P, un_sources as U, un_updates as V

RECEIPT_PATH = 'data/un/updates_archive_receipt.json'
RECEIPT_LIMIT = 8192


def load(root, now):
    path = root / RECEIPT_PATH
    if not path.exists():
        return None
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('UN update archive receipt lies outside the repository')
    with path.open('rb') as stream:
        payload = stream.read(RECEIPT_LIMIT + 1)
    if len(payload) > RECEIPT_LIMIT:
        raise ValueError('UN update archive receipt exceeds size limit')
    receipt = json.loads(payload)
    if (not isinstance(receipt, dict) or receipt.get('schema_version') != 1
            or receipt.get('capture_method') != 'alternate_https' or receipt.get('http_status') != 200
            or receipt.get('url') != V.UPDATES_URL or receipt.get('final_url') != V.UPDATES_URL
            or not all(isinstance(receipt.get(key), str) for key in
                       ('observed_at', 'source_hash', 'etag', 'last_modified'))
            or not all(type(receipt.get(key)) is int and receipt[key] >= 0 for key in
                       ('record_count', 'conflict_count'))):
        raise ValueError('UN update archive receipt schema invalid')
    observed = P.moment(receipt['observed_at'])
    if P.stamp(observed) != receipt['observed_at'] or observed > now:
        raise ValueError('UN update archive observation time invalid')
    sha = receipt['source_hash']
    body = read_original(root, sha)
    items, conflicts = V.parse_update_history(body, now=observed)
    if len(items) != receipt['record_count'] or len(conflicts) != receipt['conflict_count']:
        raise ValueError('UN update archive receipt counts do not match the original')
    return receipt, observed, items, conflicts



def read_original(root, sha):
    if not re.fullmatch('[0-9a-f]{64}', sha):
        raise ValueError('UN update archive requires an exact SHA256')
    archive = root / 'data/raw/un'
    raw = archive / f'{sha}.html.gz'
    if (not archive.resolve().is_relative_to(root.resolve())
            or not raw.resolve().is_relative_to(archive.resolve())):
        raise ValueError('UN update archive original lies outside the archive')
    try:
        with gzip.open(raw, 'rb') as stream:
            body = stream.read(U.MAX_BYTES + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise ValueError('UN update archive original is unreadable') from error
    if len(body) > U.MAX_BYTES or hashlib.sha256(body).hexdigest() != sha:
        raise ValueError('UN update archive original hash mismatch or response too large')
    return body
