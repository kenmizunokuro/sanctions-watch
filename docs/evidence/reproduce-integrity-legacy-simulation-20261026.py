"""Offline real-source policy simulation, in a disposable data copy only.

Reparses hash-pinned official bodies through the independent audit readers.
No network, production master writes, active ledger creation, or approval.
The fixed UTC simulation clock allows replay after the proposed term expires.
"""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

CODE_ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(CODE_ROOT))
from src import integrity_audit as A, integrity_decisions as D, integrity_legacy as L, integrity_triage as T
from src import dashboard

REVIEWED='2026-10-26T00:00:00Z'
EXPIRES='2026-11-25T00:00:00Z'
NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)
REPAIR='docs/evidence/source-integrity-key-repair-proposal.json'


def sha(body):return hashlib.sha256(body).hexdigest()


def read_rows(root):
    with (root/'data/master/master.csv').open(encoding='utf-8-sig',newline='') as stream:
        return list(csv.DictReader(stream))


def write_rows(root,rows):
    with (root/'data/master/master.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    dashboard.write_list(root,rows);dashboard.write_screening_gzip(root,rows)


def summary(report):
    return dict(status=report['status'],publication_allowed_in_simulation=report['publication_allowed'],
        findings=len(report['findings']),blocking=sum(f.get('blocking',True) for f in report['findings']),
        legacy_accepted=sum(bool(f.get('legacy_accepted')) for f in report['findings']),
        ordinary_retention_approved=sum(bool(f.get('retention_approved')) for f in report['findings']),
        kinds=report['counts'])


def compressed_write(path,value):
    body=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()
    with path.open('xb') as stream:stream.write(gzip.compress(body,mtime=0))
    return dict(file=path.name,body_sha256=sha(body),file_sha256=sha(path.read_bytes()))


def simulate(root,out):
    root=root.resolve();out=out.resolve()
    if out.exists() or out==root or out.is_relative_to(root/'data') or out.is_relative_to(root/'dist'):
        raise ValueError('output must be a new directory outside business data')
    if (root/L.LEDGER_PATH).exists() or (root/'data/review/integrity_retention_decisions.json').exists():
        raise ValueError('baseline must have no active decisions')
    scope=L.load_scope(root)
    baseline=A.audit(root,['ofac','mof'])
    before={rel:sha((root/rel).read_bytes()) for rel in baseline['inputs']}
    if baseline['status']!='BLOCKED' or sum(f.get('blocking',True) for f in baseline['findings'])!=9906:
        raise ValueError('fixed baseline changed; re-review is required')
    rows=read_rows(root);candidate,draft=L.prepare(rows,scope,REVIEWED,EXPIRES)
    accepted=copy.deepcopy(draft)
    accepted.update(state='ACTIVE',reviewed_by='SIMULATION ONLY - NOT A HUMAN APPROVAL',
                    reason='Hypothetical validation; never use this transient object as an activation ledger')
    # No ACTIVE ledger is written, even in the temporary tree.
    scenarios={'current_unapproved':summary(baseline)}
    out.mkdir(parents=True,exist_ok=False)
    candidate_artifact=compressed_write(out/'source-integrity-legacy-candidate-20261026.json.gz',dict(
        schema_version=1,state='DRAFT',production_authorized=False,master_input_sha256=before['data/master/master.csv'],
        proposed_reviewed_at=REVIEWED,proposed_expires_at=EXPIRES,
        annotated_rows=[r for r in candidate if L.MARKER in r.get('review_flag','')],draft_ledger=draft))
    with tempfile.TemporaryDirectory(prefix='integrity-legacy-') as directory:
        temp=Path(directory)
        shutil.copytree(root/'data',temp/'data',ignore=shutil.ignore_patterns('integrity_audit','dashboard','diff'))
        for name in ['integrity-archives','integrity-notices']:
            source=root/'docs/evidence'/name
            if source.exists():shutil.copytree(source,temp/'docs/evidence'/name)
        for rel in (L.POLICY_PATH,L.SCOPE_PATH):
            path=temp/rel;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/rel,path)
        write_rows(temp,candidate)
        candidate_report=A.audit(temp,['ofac','mof']);candidate_triage=T.triage(temp,candidate_report)
        applied=L.apply(temp,candidate_report,candidate_triage,accepted,scope,now=NOW)
        scenarios['hypothetical_legacy_only']=summary(applied)
        if scenarios['hypothetical_legacy_only']['blocking']!=7477 or scenarios['hypothetical_legacy_only']['legacy_accepted']!=2429:
            raise ValueError('legacy-only scope regression')
        expiry=L.apply(temp,candidate_report,candidate_triage,accepted,scope,now=datetime(2026,11,25,tzinfo=timezone.utc))
        scenarios['expired_legacy']=summary(expiry)
        if expiry['publication_allowed'] or 'LEGACY_ACCEPTANCE_INVALID' not in expiry['counts']:
            raise ValueError('expired acceptance did not veto')
        path=temp/'data/dashboard/list.csv';saved=path.read_bytes()
        with path.open('a',newline='') as stream:
            row=next(iter(scope.values()))['finding']['master']
            csv.writer(stream).writerow([row['display_name'],row['risk_type'],row['status'],row['risk_level']])
        stale=L.apply(temp,candidate_report,candidate_triage,accepted,scope,now=NOW)
        scenarios['extra_unmarked_search_copy']=summary(stale);path.write_bytes(saved)
        if stale['publication_allowed'] or 'LEGACY_DISPLAY_MISMATCH' not in stale['counts']:
            raise ValueError('unmarked display copy did not veto')
        # Key merge and 7475 ordinary approvals remain separate UNAPPROVED hypotheses.
        repair_body=(root/REPAIR).read_bytes()
        if sha(repair_body)!='95b5b56ef00b683002efc0feaa5cedad500bedb4fce7acf6dbdbcfbcacf9cb92':
            raise ValueError('fixed key repair proposal changed')
        repair=json.loads(repair_body)
        merged=[copy.deepcopy(repair['proposed_merged_fields']) if r['match_key']==repair['target_key'] else r
                for r in candidate if r['match_key']!=repair['source_key']]
        write_rows(temp,merged)
        report=A.audit(temp,['ofac','mof']);triage=T.triage(temp,report)
        entries=[dict(review_id=i['review_id'],decision='APPROVED_RETAIN',reviewed_by='SIMULATION ONLY',
            reviewed_at=REVIEWED,expires_at=EXPIRES,reason='UNAPPROVED hypothesis',reviewed_evidence_sha256=i['evidence_sha256'])
            for i in triage['items'] if i['retention_eligible']]
        if len(entries)!=7475:raise ValueError('ordinary proposal scope changed')
        ordinary=D.apply(report,triage,dict(schema_version=1,entries=entries),now=NOW)
        # Recompute triage so each finding's flags match the exact preceding report.
        all_applied=L.apply(temp,ordinary,T.triage(temp,ordinary),accepted,scope,now=NOW)
        scenarios['hypothetical_all_separate_approvals_and_key_merge']=summary(all_applied)
        if (all_applied['status']!='REVIEW' or not all_applied['publication_allowed']
                or scenarios['hypothetical_all_separate_approvals_and_key_merge']['legacy_accepted']!=2429):
            raise ValueError('combined hypothetical gates did not reconcile')
        # A corrupt official original still stops publication even in the full hypothesis.
        raw=temp/report['documents'][0]['raw_path'];raw.write_bytes(b'broken official body')
        failed=A.audit(temp,['ofac','mof'])
        scenarios['corrupt_official_original']=summary(failed)
        if failed['publication_allowed'] or 'UNDETERMINED' not in failed['counts']:
            raise ValueError('official corruption did not veto')
    after={rel:sha((root/rel).read_bytes()) for rel in before}
    if before!=after:raise ValueError('business data changed')
    result=dict(schema_version=1,simulation_only=True,implementation_authorized=True,production_authorized=False,
        actual_master_changes=0,actual_approvals=0,actual_active_ledgers_created=0,business_inputs_unchanged=True,
        fixed_clock=NOW.isoformat(),proposed_reviewed_at=REVIEWED,proposed_expires_at=EXPIRES,
        source_scope='Saved official bodies; independently reparsed offline; no current remote freshness claim',
        source_documents=baseline['documents'],business_input_hashes=before,
        code_hashes={p:sha((CODE_ROOT/p).read_bytes()) for p in ['src/integrity_legacy.py','src/integrity_audit.py',
            'src/integrity_triage.py','src/integrity_decisions.py','src/export_excel.py','src/export_diff_excel.py']},
        candidate_artifact=candidate_artifact,scenarios=scenarios,
        limitations=['All approvals in scenarios are hypothetical. The saved candidate ledger is DRAFT.',
            '7475 ordinary retentions, one duplicate merge, production annotations and ledger activation remain unapproved.',
            'Original import intent, identity and current official listing of legacy strings remain unresolved.',
            'Activation requires fresh source checks and a freshly approved term of at most30days.'])
    with (out/'source-integrity-legacy-simulation-20261026.json').open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    print(json.dumps(dict(scenarios=scenarios,business_inputs_unchanged=True),ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=CODE_ROOT)
    parser.add_argument('--out-dir',type=Path,required=True);args=parser.parse_args()
    simulate(args.root,args.out_dir)


if __name__=='__main__':main()
