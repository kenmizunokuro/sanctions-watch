"""Re-run the existing audit with three historical raw files in a disposable copy.

No production inputs or approvals are changed. Requires complete Git history.
Run: python this_file.py --output-dir /tmp/recovered-audit
"""
import argparse
import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src import integrity_audit as A
from src import integrity_triage as T

TRIAGE = ROOT / 'docs/evidence/source-integrity-20261023-triage.json.gz'
PINNED_TRIAGE_HASH = '624bb52d78479d4401e4552593e3ab78bcaad73db80c4bc28d5c98ba58d6338b'
RECOVER = [
    ('283b090b6822f6fffb3226728ff83003072eb443', 'data/raw/ofac_sdn/20260829T214252Z__alt.csv',
     'a4cae601ae9cd8ef977dc08da48d47a2cc59a1383cc0677fa812421b48025e39'),
    ('283b090b6822f6fffb3226728ff83003072eb443', 'data/raw/ofac_sdn/20260829T214252Z__sdn.csv',
     'a2ccea04237a13459009bc527502bb6a08d4e84fe97b46fcce1ed621e9a8301e'),
    ('fb258464ac7c5d297ac7ead4d8e164b909909d77', 'data/raw/ofac_sdn_advanced/20260904T010536Z__sdn_advanced.xml.gz',
     '657e2ecd8ed722d5640e4076058a24982c6e722aac2a631eea3da1e052861f93'),
]


def validate_baseline(sandbox, previous):
    """Pin every historical body and notice, including file membership."""
    expected = {d['raw_path']: d for d in previous['archive_manifest']}
    actual = set()
    for folder in ('ofac_sdn', 'ofac_cons', 'ofac_sdn_advanced', 'ofac_cons_advanced'):
        actual.update(p.relative_to(sandbox).as_posix() for p in (sandbox / 'data/raw' / folder).iterdir() if p.is_file())
    notice_root = sandbox / 'docs/evidence/integrity-notices'
    actual.update(p.relative_to(sandbox).as_posix() for p in notice_root.iterdir() if p.is_file() and p.name != 'manifest.json')
    if actual != set(expected):
        raise ValueError('Archive baseline file membership differs from previous triage')
    for path, document in expected.items():
        raw = (sandbox / path).read_bytes()
        body = gzip.decompress(raw) if path.endswith('.gz') else raw
        if A.sha(body) != document['source_hash']:
            raise ValueError('Archive baseline body hash differs: ' + path)
    expected_notices = [
        {k: v for k, v in d.items() if k not in {'status', 'role', 'name_count', 'current'}}
        for d in previous['archive_manifest'] if d.get('role') == 'notice_mention'
    ]
    if json.loads((notice_root / 'manifest.json').read_text()) != expected_notices:
        raise ValueError('Notice baseline manifest differs from previous triage')
    for relative, expected_hash in previous['source_audit_inputs'].items():
        if A.sha((sandbox / relative).read_bytes()) != expected_hash:
            raise ValueError('Copied input baseline hash differs: ' + relative)


def run():
    if A.sha(TRIAGE.read_bytes()) != PINNED_TRIAGE_HASH:
        raise ValueError('Previous triage artifact differs from the pinned investigation.')
    previous = json.loads(gzip.decompress(TRIAGE.read_bytes()))
    for relative, digest in previous['source_audit_inputs'].items():
        if A.sha((ROOT / relative).read_bytes()) != digest:
            raise ValueError('Current source-audit generation changed: ' + relative)
    recovered = []
    with tempfile.TemporaryDirectory(prefix='sanctions-integrity-recovery-') as temporary:
        sandbox = Path(temporary)
        for folder in ('ofac_sdn', 'ofac_cons', 'ofac_sdn_advanced', 'ofac_cons_advanced', 'mof'):
            shutil.copytree(ROOT / 'data/raw' / folder, sandbox / 'data/raw' / folder)
        for relative in previous['source_audit_inputs']:
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        shutil.copytree(ROOT / 'docs/evidence/integrity-notices', sandbox / 'docs/evidence/integrity-notices')
        validate_baseline(sandbox, previous)
        for commit, path, expected in RECOVER:
            raw = subprocess.check_output(['git', '-C', str(ROOT), 'show', f'{commit}:{path}'])
            body = gzip.decompress(raw) if path.endswith('.gz') else raw
            if A.sha(body) != expected:
                raise ValueError('Recovered body hash differs: ' + path)
            relative = path if path.endswith('.gz') else path + '.gz'
            target = sandbox / relative
            if target.exists():
                raise ValueError('Recovered file would overwrite an existing archive: ' + relative)
            target.write_bytes(raw if path.endswith('.gz') else gzip.compress(body, mtime=0))
            recovered.append(dict(commit=commit, path=path, restored_path=relative, source_hash=expected,
                                  repository_url=f'https://github.com/kenmizunokuro/sanctions-watch/blob/{commit}/{path}'))
        audit = A.audit(sandbox, ['ofac', 'mof'])
        investigation = T.triage(sandbox, audit)
        # Match findings by exact kind/source/master/official, not mutable review_id.
        identity = lambda x: T.digest({k: x['finding'].get(k) for k in ('kind', 'checked_source', 'master', 'official')})
        by_identity = {identity(x): x for x in previous['items']}
        if len(by_identity) != len(previous['items']):
            raise ValueError('Ambiguous previous finding identities')
        changes = []
        for item in investigation['items']:
            prior = by_identity.get(identity(item))
            if prior is None:
                raise ValueError('Audit findings changed beyond archive evidence')
            if item['classification'] != prior['classification']:
                changes.append(dict(previous_review_id=prior['review_id'], previous_classification=prior['classification'],
                                    updated_item=item))
        if len(investigation['items']) != len(previous['items']) or investigation['archive_errors']:
            raise ValueError('Unexpected finding count or archive parse failures')
        if audit['status'] != 'BLOCKED' or investigation['approved_count'] != 0:
            raise ValueError('Recovery must not grant approval or allow publication')
        return dict(schema_version=1, previous_triage_sha256=PINNED_TRIAGE_HASH,
                    source_audit_inputs=audit['inputs'], recovered=recovered,
                    audit_status=audit['status'], publication_allowed=audit['publication_allowed'],
                    audit_counts=audit['counts'], triage_counts=investigation['counts'],
                    retained_candidate_count=investigation['retention_candidates'], approved_count=0,
                    changed_classification_count=len(changes), changed_classifications=changes,
                    full_triage_sha256=T.digest(investigation), archive_errors=investigation['archive_errors'],
                    evidence_scope='Classification deltas plus complete recovered archive manifest; unchanged items remain in the pinned prior artifact.',
                    recovered_archive_manifest=investigation['archive_manifest'],
                    limitations=['Disposable copy only; repository raw archives and production inputs unchanged.',
                                 'This does not approve retention or alter current publication decisions.',
                                 'Review IDs can change when new historical name/Party evidence is introduced.',
                                 'The full recovered triage is regenerated by this script; this artifact retains classification deltas and archive membership, not every unchanged item.',
                                 'Historical names without index correspondence do not prove master party identity.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    output = parser.parse_args().output_dir.resolve()
    target = output / 'source-integrity-20261024-recovery.json.gz'
    summary_path = output / 'source-integrity-20261024-recovery-summary.json'
    if target.exists() or summary_path.exists():
        raise ValueError('Evidence exists; choose a new output directory.')
    report = run()
    output.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as stream:
        stream.write(gzip.compress((json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode(), mtime=0))
    summary = {k: v for k, v in report.items() if k != 'changed_classifications'}
    with summary_path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
