"""Read-only OFAC publication-stop diagnostics, bound to the audited bytes."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .integrity_audit import blocking_findings

PATH = Path('data/dashboard/ofac_integrity.csv')
HEADER = ['監査終了日時（JST）', '証跡状態', '停止件数', '停止理由', '監査報告SHA256', 'GitHub実行URL']


def capture(root: Path, report_path: str, expected_sha256: str) -> dict:
    if not report_path or not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('今回の監査報告パス・SHA256がありません')
    path = (root / report_path).resolve()
    if (not path.is_relative_to((root / 'data/integrity_audit').resolve())
            or path.name.startswith('triage-')):
        raise ValueError('監査報告の保存先が不正です')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('今回の監査報告SHA256と一致しません')
    report = json.loads(gzip.decompress(body))
    if (not isinstance(report, dict) or report.get('schema_version') != 1
            or not isinstance(report.get('scope'), list) or 'ofac' not in report['scope']
            or report.get('status') != 'BLOCKED' or report.get('publication_allowed') is not False):
        raise ValueError('OFACの反映停止を示す監査報告ではありません')
    inputs = report.get('inputs', {})
    required = {'data/master/master.csv', 'data/state.json', 'data/master/ofac_alias_history.csv'}
    if not isinstance(inputs, dict) or not required.issubset(inputs):
        raise ValueError('監査対象の入力SHA256が不足しています')
    for name, digest in inputs.items():
        candidate = (root / name).resolve()
        if (not candidate.is_relative_to(root.resolve())
                or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest):
            raise ValueError('監査対象の入力SHA256と一致しません: ' + name)
    findings = report.get('findings')
    if not isinstance(findings, list) or any(not isinstance(f, dict) or not f.get('kind') for f in findings):
        raise ValueError('監査指摘の構造が不正です')
    blockers = blocking_findings(findings)
    if not blockers:
        raise ValueError('監査停止の指摘がありません')
    details = []
    for finding in blockers[:10]:
        master = finding.get('master') or {}
        official = finding.get('official') or {}
        if not isinstance(official, dict):
            official = {}
        details.append(dict(kind=finding['kind'],
            name=str(master.get('display_name', '') if isinstance(master, dict) else '')[:200]
                 or str(official.get('name', ''))[:200],
            detail=str(finding.get('detail', ''))[:500]))
    return dict(status='BLOCKED', report=path.relative_to(root.resolve()).as_posix(),
        report_sha256=expected_sha256, inputs=inputs,
        blocking_counts=dict(sorted(Counter(f['kind'] for f in blockers).items())), blockers=details)


def write_summary(root: Path, record: dict) -> None:
    integrity = record.get('integrity') or {}
    counts = integrity.get('blocking_counts', {})
    if integrity.get('status') == 'BLOCKED' and counts:
        count = sum(counts.values())
        summary = '監査停止' + str(count) + '件：' + '、'.join(f'{k} {v}件' for k, v in counts.items())
        names = [b['name'] for b in integrity.get('blockers', []) if b.get('name')]
        if names:
            summary += '／対象（最大10件）：' + '、'.join(names)
    else:
        count = ''
        summary = '監査停止理由を確認できません：' + integrity.get('reason', '今回の監査報告がありません')
    stamp = datetime.strptime(record['finished_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    row = [stamp.astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S'),
           integrity.get('status', 'UNAVAILABLE'), count, ' '.join(summary.split()),
           integrity.get('report_sha256', ''), record['run_url']]
    path = root / PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as stream:
        csv.writer(stream, lineterminator='\n').writerows([HEADER, row])
