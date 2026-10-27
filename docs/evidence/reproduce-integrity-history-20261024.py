"""Offline research companion. No approval, source-state, or master writes.

Requires complete Git ancestry of BASE and the exact previous triage artifact.
Run from any directory: python this_file.py --output-dir /tmp/history-report
The generated report supplements, and never replaces, the publication audit.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import integrity_readers as R
from src.normalize import match_key

BASE = 'ea4ac7f0319ea3ea285d8d26c77be1fd17e92cda'
TRIAGE = 'docs/evidence/source-integrity-20261023-triage.json.gz'
TRIAGE_HASH = '624bb52d78479d4401e4552593e3ab78bcaad73db80c4bc28d5c98ba58d6338b'
MASTER = 'data/master/master.csv'
FOLDERS = ['data/raw/' + folder for folder in (
    'ofac_sdn', 'ofac_cons', 'ofac_sdn_advanced', 'ofac_cons_advanced')]
URLS = {
    ('SDN', 'primary'): 'https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV',
    ('SDN', 'alias'): 'https://sanctionslistservice.ofac.treas.gov/api/download/ALT.CSV',
    ('SDN', 'advanced'): 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML',
    ('Consolidated', 'primary'): 'https://sanctionslistservice.ofac.treas.gov/api/download/CONS_PRIM.CSV',
    ('Consolidated', 'alias'): 'https://sanctionslistservice.ofac.treas.gov/api/download/CONS_ALT.CSV',
    ('Consolidated', 'advanced'): 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/CONS_ADVANCED.XML',
}


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def sha(body):
    return hashlib.sha256(body).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def blob_proof(commit, path, blob, body):
    return dict(commit=commit, path=path, git_blob=blob, sha256=sha(body),
                repository_url=f'https://github.com/kenmizunokuro/sanctions-watch/blob/{commit}/{path}')


def trace_master(items):
    """First-parent observations by exact stored key and normalized display.

    Normalized names are candidate associations, not independent identity proof.
    Every row present at the first matching snapshot is retained, including
    duplicate rows. Embedded first_seen_ms is not treated as a Git observation.
    """
    targets = {match_key(x['display_name']) for x in items}
    stored_targets = {x['master_key'] for x in items}
    first_name, first_ofac, first_key, initial = {}, {}, {}, {}
    manifest, cache = [], {}
    commits = git('log', '--first-parent', '--reverse', '--format=%H', BASE, '--', MASTER).decode().splitlines()
    for commit in commits:
        blob = git('rev-parse', f'{commit}:{MASTER}').decode().strip()
        body = git('cat-file', 'blob', blob)
        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig')), strict=True))
        proof = blob_proof(commit, MASTER, blob, body)
        proof.update(row_count=len(rows), committed_at=git('show', '-s', '--format=%cI', commit).decode().strip())
        manifest.append(proof)
        names, ofac, keys = defaultdict(list), defaultdict(list), defaultdict(list)
        for line, row in enumerate(rows, 2):
            display = row['display_name']
            if display not in cache:
                cache[display] = match_key(display)
            normal = cache[display]
            if normal in targets:
                record = dict(csv_record_number=line, row=row)
                names[normal].append(record)
                if 'OFAC' in row['sources'].split(';'):
                    ofac[normal].append(record)
            if row['match_key'] in stored_targets:
                keys[row['match_key']].append(dict(csv_record_number=line, row=row))
        for source, dest in ((names, first_name), (ofac, first_ofac), (keys, first_key)):
            for key, records in source.items():
                if key not in dest:
                    dest[key] = dict(snapshot=proof, records=records)
        if len(manifest) == 1:
            initial = dict(names)
    return dict(manifest=manifest, initial=initial, first_name=first_name,
                first_ofac=first_ofac, first_key=first_key)


def archived_names(targets):
    """Parse every distinct historical raw body, retaining all Party relations.

    Include changed as well as added blobs. Identical bodies at different paths
    remain visible in occurrences; each unique body is parsed once. This records
    locally saved bodies with their original download URLs, not fresh retrieval.
    """
    log = git('log', '--full-history', '-m', '--root', '--reverse', '--topo-order', '--no-renames', '--raw', '--no-abbrev',
              '--format=COMMIT:%H', BASE, '--', *FOLDERS).decode()
    documents, witnesses, commit = {}, defaultdict(dict), None
    for line in log.splitlines():
        if line.startswith('COMMIT:'):
            commit = line[7:]
        if not line.startswith(':'):
            continue
        metadata, path = line.split('\t', 1)
        _, _, _, blob, status = metadata.split()
        if status == 'D':
            continue
        raw = git('cat-file', 'blob', blob)
        body = gzip.decompress(raw) if path.endswith('.gz') else raw
        label = 'Consolidated' if 'ofac_cons' in path else 'SDN'
        role = 'advanced' if 'advanced' in path else 'alias' if 'alt.csv' in path else 'primary'
        ident = (sha(body), label, role)
        occurrence = blob_proof(commit, path, blob, body)
        occurrence['stored_sha256'] = sha(raw)
        if ident in documents:
            documents[ident]['occurrences'].append(occurrence)
            continue
        document = dict(source_hash=sha(body), category=label, role=role,
                        source_url=URLS[label, role], occurrences=[occurrence], status='unreadable')
        documents[ident] = document
        try:
            records = R.read_ofac_xml(body, label) if role == 'advanced' else R.read_classic(body, label, alias=role == 'alias')
        except ValueError as exc:
            document['error'] = str(exc)
            continue
        document.update(status='parsed', name_count=len(records))
        for record in records:
            key = match_key(record['name'])
            if key not in targets:
                continue
            semantic = tuple(record.get(k) for k in ('category', 'source_id', 'name', 'primary', 'weak'))
            if semantic not in witnesses[key]:
                witnesses[key][semantic] = dict(record=record, original=occurrence,
                                                source_url=URLS[label, role])
    return list(documents.values()), {k: list(v.values()) for k, v in witnesses.items()}


def build_report():
    if git('rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('Complete Git history required; fetch ancestry before reproducing.')
    raw = (ROOT / TRIAGE).read_bytes()
    if sha(raw) != TRIAGE_HASH:
        raise ValueError('Pinned triage artifact hash differs; do not compare another generation silently.')
    triage = json.loads(gzip.decompress(raw))
    # This research must describe the same master generation as the source audit.
    if sha(git('show', f'{BASE}:{MASTER}')) != triage['source_audit_inputs'][MASTER]:
        raise ValueError('Pinned base master differs from source audit input.')
    items = triage['items']
    history = trace_master(items)
    docs, names = archived_names({match_key(x['display_name']) for x in items})
    local_hashes = {x.get('source_hash') for x in triage['archive_manifest']}
    records = []
    for item in items:
        normal = match_key(item['display_name'])
        links = {(x['list'], x['party_id']) for x in item['index_links']}
        proofs = names.get(normal, [])
        matched = [p for p in proofs if (p['record']['category'], p['record']['source_id']) in links]
        records.append(dict(
            review_id=item['review_id'], classification=item['classification'],
            display_name=item['display_name'], master_key=item['master_key'],
            normalized_display_key=normal, archive_lookup_key=normal,
            initial_normalized_name_matches=history['initial'].get(normal, []),
            first_normalized_name_observation=history['first_name'].get(normal),
            first_ofac_source_claim_observation=history['first_ofac'].get(normal),
            first_exact_stored_key_observation=history['first_key'].get(item['master_key']),
            archived_name_witnesses=proofs,
            archived_name_and_index_party_witnesses=matched,
            witnesses_from_bodies_absent_in_previous_triage=[p for p in proofs if p['original']['sha256'] not in local_hashes],
            grants_approval=False,
        ))
    counts = {}
    for category in sorted(triage['counts']):
        group = [r for r in records if r['classification'] == category]
        counts[category] = dict(
            findings=len(group), initial_name_candidates=sum(bool(r['initial_normalized_name_matches']) for r in group),
            initial_name_with_ofac_claim=sum(any('OFAC' in y['row']['sources'].split(';') for y in r['initial_normalized_name_matches']) for r in group),
            archived_name_candidates=sum(bool(r['archived_name_witnesses']) for r in group),
            archived_name_and_index_party=sum(bool(r['archived_name_and_index_party_witnesses']) for r in group),
            additional_archived_body_candidates=sum(bool(r['witnesses_from_bodies_absent_in_previous_triage']) for r in group))
    manifest = dict(base_commit=BASE, triage_path=TRIAGE, triage_sha256=sha(raw),
                    source_audit_inputs=triage['source_audit_inputs'], master_snapshots=history['manifest'],
                    archived_documents=docs)
    summary = dict(counts=counts, finding_count=len(records), master_snapshot_count=len(history['manifest']),
                   raw_occurrence_count=sum(len(d['occurrences']) for d in docs),
                   distinct_body_count=len(docs), parse_errors=sum(d['status'] != 'parsed' for d in docs),
                   retention_approvals_created=0, business_data_modified=False,
                   publication_audit_status='BLOCKED', publication_audit_status_basis='Pinned previous audit, not a new live audit')
    limitations = [
        'An initial-master source label is a historical claim, not primary-source verification.',
        'Name associations share match_key; homonyms are not proven to be the same party.',
        'A matching Party ID is reported separately; a name-only match never proves identity.',
        'Git commit timestamps show repository observations, not designation or original import dates.',
        'The first-parent master trajectory is inspected, not every abandoned branch.',
        'Raw bodies come from all ancestors of the fixed base; this is not fresh remote retrieval.',
        'No archive match does not prove that the name was never officially listed.',
        'Additional-body counts use the selected earliest witness per semantic name/Party relation, not every matching document.',
        'No results are fed into audit exemptions or the retention approval ledger.',
    ]
    return dict(schema_version=1, manifest=manifest, summary=summary, records=records, limitations=limitations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    # Only new output files are allowed; never overwrite a prior evidence file.
    paths = [output / 'source-integrity-20261024-history.json.gz', output / 'source-integrity-20261024-history-summary.json']
    if any(p.exists() for p in paths):
        raise ValueError('Output evidence already exists; choose a new output directory.')
    report = build_report()
    output.mkdir(parents=True, exist_ok=True)
    with paths[0].open('xb') as stream:
        stream.write(gzip.compress(json_bytes(report), mtime=0))
    with paths[1].open('xb') as stream:
        stream.write(json_bytes(dict(manifest=report['manifest'], summary=report['summary'], limitations=report['limitations'])))
    print(json.dumps(report['summary'], ensure_ascii=False, sort_keys=True, indent=2))
    if report['summary']['parse_errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
