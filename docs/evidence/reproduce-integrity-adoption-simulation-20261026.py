"""Offline adoption what-if; never writes a master or approval ledger.

Fixed saved lineage and explicit document roles replay the comparison, not a
new independent reading or proof of current remote listing. All hypotheses
remain PENDING; only aggregate hypothetical effects are exported.
"""
import argparse
import copy
import csv
import gzip
import hashlib
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CODE_ROOT))
from src import integrity_audit as A, integrity_decisions as D, integrity_triage as T

AUDIT = 'docs/evidence/source-integrity-20261026-alias-fields-audit.json.gz'
TRIAGE = 'docs/evidence/source-integrity-20261026-alias-fields-triage.json.gz'
REPAIR = 'docs/evidence/source-integrity-key-repair-proposal.json'
PINS = {
    AUDIT: '2a8c219d58eae466436a245c29bf5698a24856373c1886768fee0472f38d53b7',
    TRIAGE: '0e696ee6f5879a9f23eb0e7b6d3365e4a3c3fa120c8ca0eb7aa6ba75756ec890',
    REPAIR: '95b5b56ef00b683002efc0feaa5cedad500bedb4fce7acf6dbdbcfbcacf9cb92',
}
NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pinned(root, name):
    body = (root / name).read_bytes()
    if name.endswith('.gz'):
        body = gzip.decompress(body)
    if sha(body) != PINS[name]:
        raise ValueError('fixed proposal input changed: ' + name)
    return json.loads(body)


def hypothetical(report, triage):
    entries = [dict(review_id=i['review_id'], decision='APPROVED_RETAIN',
                    reviewed_by='SIMULATION ONLY - NOT A HUMAN APPROVAL',
                    reviewed_at='2026-10-26T00:00:00Z', expires_at='2026-11-25T00:00:00Z',
                    reason='Hypothetical aggregate calculation; do not use as a decision ledger',
                    reviewed_evidence_sha256=i['evidence_sha256'])
               for i in triage['items'] if i['retention_eligible']]
    return A._finish(D.apply(report, triage, dict(schema_version=1, entries=entries), now=NOW))


def summary(report, triage):
    return dict(simulation_only=True, publication_allowed_in_simulation=report['publication_allowed'],
                status=report['status'], total_findings=len(report['findings']),
                blocking_findings=sum(f.get('blocking', True) for f in report['findings']),
                hypothetical_retention_applied=sum(f.get('retention_approved', False) for f in report['findings']),
                remaining_classifications=dict(sorted(Counter(i['classification'] for i, f in
                    zip(triage['items'], report['findings']) if f.get('blocking', True)).items())))


def build(root):
    root = root.resolve()
    audit, triage, repair = (pinned(root, name) for name in (AUDIT, TRIAGE, REPAIR))
    before = {}
    for rel, expected in audit['inputs'].items():
        path = (root / rel).resolve()
        if not path.is_relative_to(root / 'data'):
            raise ValueError('proposal business input outside data')
        before[rel] = sha(path.read_bytes())
        if before[rel] != expected:
            raise ValueError('business input changed: ' + rel)
    if sha((root / 'data/master/master.csv').read_bytes()) != repair['master_input_sha256']:
        raise ValueError('repair proposal is stale')
    with (root / 'data/master/master.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    with (root / 'data/master/ofac_alias_history.csv').open(encoding='utf-8-sig', newline='') as stream:
        index = list(csv.DictReader(stream))
    categories = {k: '財務省:' + v if v else None for k, v in
                  json.loads((root / 'data/kubun_map.json').read_text())['map'].items()}
    categories.update(SDN='OFAC:SDN', Consolidated='OFAC:Consolidated')
    documents = {d['raw_path']: d for d in audit['documents']}
    expected = []
    for record in audit['lineage']:
        value = {k: v for k, v in record.items() if k not in {'master_key', 'master_rows', 'id_link'}}
        if value['source'] == 'OFAC':
            role = documents[value['raw_path']]['role']
            if role not in {'primary', 'alias', 'advanced'}:
                raise ValueError('unknown fixed source role')
            value['format'] = 'advanced_xml_v3' if role == 'advanced' else 'classic_csv'
        expected.append(value)
    replay = A.compare(expected, rows, index, categories)
    if replay['counts'] != audit['counts']:
        raise ValueError('comparison semantics changed; fixed baseline cannot be replayed')
    source = [r for r in rows if r['match_key'] == repair['source_key']]
    target = [r for r in rows if r['match_key'] == repair['target_key']]
    if source != [repair['source_row']] or target != repair['target_rows'] or len(target) != 1:
        raise ValueError('key repair rows changed')
    merged_row = repair['proposed_merged_fields']
    if (merged_row['status'] != source[0]['status'] or merged_row['status'] != target[0]['status']
            or json.loads(merged_row['variants']) != sorted(set(json.loads(source[0]['variants']) + json.loads(target[0]['variants'])))
            or int(merged_row['first_seen_ms']) != min(int(source[0]['first_seen_ms']), int(target[0]['first_seen_ms']))
            or int(merged_row['last_updated_ms']) != max(int(source[0]['last_updated_ms']), int(target[0]['last_updated_ms']))):
        raise ValueError('key repair loses variants, timestamps or status')
    merged = [copy.deepcopy(merged_row) if r['match_key'] == repair['target_key'] else copy.deepcopy(r)
              for r in rows if r['match_key'] != repair['source_key']]
    comparison = A.compare(expected, merged, index, categories)
    merged_report = A._finish(dict(audit, findings=comparison['findings'], lineage=comparison['lineage'],
                                   regression=comparison['regression'], master_row_count=len(merged)))
    witnesses = {i['master_key']: i['evidence'] for i in triage['items']}
    merged_triage = T.classify(merged_report, merged, index, witnesses)
    original_ids = {i['review_id'] for i in triage['items'] if i['retention_eligible']}
    if original_ids != {i['review_id'] for i in merged_triage['items'] if i['retention_eligible']}:
        raise ValueError('key repair changes retention proposal scope')
    after = {rel: sha((root / rel).read_bytes()) for rel in before}
    if before != after:
        raise ValueError('business inputs changed during simulation')
    return dict(schema_version=1, simulation_only=True, actual_approvals=0,
                actual_master_changes=0, actual_ledger_created=False, publication_authorized=False,
                fixed_inputs=PINS, business_input_hashes=before, business_inputs_unchanged=before == after,
                code_hashes={name: sha((CODE_ROOT / name).read_bytes()) for name in
                             ('src/integrity_audit.py', 'src/integrity_triage.py', 'src/integrity_decisions.py', 'src/normalize.py')},
                scenarios={
                    'current_unapproved': summary(audit, triage),
                    'hypothetical_retention_7475': summary(hypothetical(audit, triage), triage),
                    'hypothetical_key_merge_only': summary(merged_report, merged_triage),
                    'hypothetical_key_merge_and_retention': summary(hypothetical(merged_report, merged_triage), merged_triage)},
                proposed_key_merge=dict(applied=False, source_rows=2, resulting_rows=1,
                    master_rows_before=len(rows), master_rows_after=len(merged), ofac_party_id=repair['ofac_party_id'],
                    same_display_and_active_status=source[0]['display_name'] == target[0]['display_name'] and source[0]['status'] == target[0]['status'],
                    merged_fields=merged_row, separate_original_rows_preserved_in=REPAIR),
                limitations=['Hypothetical aggregate scenarios are not approvals and never create a decision ledger.',
                             'Uses fixed saved lineage/document roles, not a fresh independent source parse or current remote listing.',
                             'Unknown history is not grandfathered; all 2429 judgment targets retain their veto.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=CODE_ROOT)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = build(args.root)
        payload = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        with args.out.open('xb') as stream:
            stream.write(payload)
    except (OSError, ValueError, KeyError, EOFError) as exc:
        print('ADOPTION SIMULATION FAILED: ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(dict(simulation_only=True, actual_approvals=0, scenarios=report['scenarios']), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
