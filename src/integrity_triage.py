"""Read-only, reproducible investigation of integrity findings. Classification is
not approval, a delisting instruction, or proof of the original importer's intent.
"""
from __future__ import annotations

import argparse
import io
import re
from html.parser import HTMLParser
import csv
import gzip
import hashlib
import json
import sys
import uuid
import zlib
from collections import Counter, defaultdict
from pathlib import Path

from . import integrity_readers as R
from .normalize import match_key


class TriageError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _facts(proof):
    # Bind the party/name relationship, not unrelated changes to the whole file.
    # The full byte hashes and locations remain separately in review evidence.
    return {k:proof.get(k) for k in ('name','source_id','category','current','field','transform','weak','primary')}


def classify(report, master_rows, index_rows, witnesses):
    bykey=defaultdict(list);masters=defaultdict(list)
    for row in index_rows:bykey[row['match_key']].append(row)
    for row in master_rows:masters[row['match_key']].append(row)
    items=[]
    for finding in report['findings']:
        row=finding.get('master') or {}
        official=finding.get('official') or {}
        key=row.get('match_key','') if isinstance(row,dict) else ''
        if not key and official:key=match_key(official.get('name',''))
        links=bykey.get(key,[])
        proofs=witnesses.get(key,[])
        kind=finding['kind'];eligible=False;proposal=None
        checked_source=finding.get('checked_source') or official.get('source')
        classification='OTHER_BLOCKING_FINDING'
        if kind=='CATEGORY_LABEL_UNRESOLVED':
            classification='CATEGORY_LABEL_UNRESOLVED'
        elif kind=='MISGENERATED' and row:
            canonical=match_key(row['display_name']);peers=masters.get(canonical,[])
            classification='KEY_COLLISION_REVIEW' if peers else 'KEY_REPAIR_REVIEW'
            proposal=dict(action='REVIEW_MERGE' if peers else 'REVIEW_REKEY',source_key=key,target_key=canonical,
                          source_row=row,target_rows=peers,
                          preserve=['first_seen_ms','last_updated_ms','variants','sources','categories','status','all prior audit history'],
                          applied=False)
        elif kind=='WEAK_ALIAS_ACTIVE':
            current=[p for p in proofs if p['current'] and p['field']=='name' and p['transform']=='exact']
            if current and all(p['weak'] for p in current):
                classification='CURRENT_WEAK_ONLY';eligible=True
            else:classification='WEAK_EVIDENCE_MISSING'
        elif kind=='EXTRA_REGISTRATION' and checked_source!='OFAC':
            classification='SOURCE_CONTEXT_UNSUPPORTED'
        elif kind=='EXTRA_REGISTRATION':
            current_forms=[p for p in proofs if p['current'] and p['field']=='name' and p['transform']=='comma_removed']
            literal_current=[p for p in proofs if p['current'] and p['field']=='name' and p['transform']=='exact']
            named_past=[p for p in proofs if not p['current'] and p['field']=='name' and p['transform']=='exact']
            historical={(p['category'],p['source_id']) for p in named_past}
            corroborated=[x for x in links if (x['list'],x['party_id']) in historical]
            if match_key(row['display_name'])!=key:
                classification='KEY_COLLISION_REVIEW' if masters.get(match_key(row['display_name'])) else 'KEY_REPAIR_REVIEW'
            elif literal_current:
                # A claimed EXTRA with current literal evidence is inconsistent:
                # never turn that into a retention approval silently.
                classification='AUDIT_EVIDENCE_CONFLICT'
            elif current_forms:
                classification='CURRENT_FORMAT_VARIANT';eligible=True
            elif corroborated:
                if all(x['party_current']=='0' and x['alias_current']=='0' for x in corroborated):
                    classification='HISTORICAL_PARTY_CONFIRMED';eligible=True
                elif all(x['party_current']=='1' and x['alias_current']=='0' for x in corroborated):
                    classification='HISTORICAL_ALIAS_CONFIRMED';eligible=True
                else:classification='MIXED_HISTORICAL_RELATION'
            elif links:
                classification='HISTORY_WITHOUT_ORIGINAL'
            elif named_past:
                classification='ARCHIVED_NAME_WITHOUT_INDEX';eligible=True
            elif any(p['field'] not in {'name','notice_text'} for p in proofs):
                classification='NON_NAME_FIELD_ONLY'
            elif any(p['field']=='notice_text' for p in proofs):
                classification='NOTICE_MENTION_ONLY'
            else:classification='ORIGIN_UNRESOLVED'
        semantic_links=[{k:x.get(k) for k in ('list','party_id','name','alias_current','party_current','low_quality','primary')} for x in links]
        semantic_proofs=sorted({_key_json(_facts(p)) for p in proofs})
        identity=dict(kind=kind,checked_source=checked_source,master=row,classification=classification,
                      official={k:official.get(k) for k in ('source','category','source_id','name','weak','primary')},
                      links=sorted(semantic_links,key=_key_json),proofs=semantic_proofs)
        item=dict(review_id=digest(identity),kind=kind,checked_source=checked_source,classification=classification,
                  master_key=key,display_name=(row.get('display_name') or official.get('name','')) if isinstance(row,dict) else official.get('name',''),
                  master_sha256=digest(row),finding=finding,index_links=links,evidence=proofs,
                  evidence_sha256=digest(proofs),retention_eligible=eligible,approved=bool(finding.get('retention_approved',False)))
        if proposal:item['repair_proposal']=proposal
        items.append(item)
    return dict(schema_version=1,source_audit_inputs=report.get('inputs',{}),
                source_audit_status=report['status'],items=items,
                counts=dict(sorted(Counter(x['classification'] for x in items).items())),
                retention_candidates=sum(x['retention_eligible'] for x in items),
                approved_count=sum(x['approved'] for x in items),limitations=[
                    'Classification does not prove intent to retain and never grants approval.',
                    'Historical index flags alone are not primary-source evidence.',
                    'Name and comma-removed matches are evidence candidates; no fuzzy identity inference.',
                    'An absent archive witness does not prove a name was never officially listed.',
                ])


def _key_json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def _read_body(path):
    try:
        body=path.read_bytes()
        return gzip.decompress(body) if path.suffix=='.gz' else body
    except (OSError,EOFError,zlib.error) as exc:
        raise TriageError(f'原本を読めない: {path}: {exc}') from exc


def _csv(path):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream,strict=True);rows=list(reader)
        if not reader.fieldnames or any(None in r or None in r.values() for r in rows):
            raise TriageError(f'CSV構造不明: {path}')
        return rows


def collect_witnesses(root, report, target_keys):
    """Read fixed current bodies and locally archived bodies. Never fetch URLs.

    Retain the latest archive for each semantic party/name relationship, and all
    distinct Party IDs. Each retained witness has its own original byte hash.
    """
    current={d['raw_path']:d for d in report.get('documents',[]) if d['source'].startswith('ofac_')}
    urls={(d['source'],d['role']):d['source_url'] for d in current.values()}
    paths=set(current)
    for folder in ('ofac_sdn','ofac_cons','ofac_sdn_advanced','ofac_cons_advanced'):
        paths.update(p.relative_to(root).as_posix() for p in (root/'data/raw'/folder).glob('*') if p.is_file())
    found=defaultdict(dict);manifest=[];errors=[]
    for rel in sorted(paths):
        path=(root/rel).resolve()
        if not path.is_relative_to(root/'data/raw'):
            raise TriageError('原本パスがdata/raw外')
        source='ofac_cons' if 'ofac_cons' in rel else 'ofac_sdn'
        label='Consolidated' if source=='ofac_cons' else 'SDN'
        advanced='advanced' in rel
        role=current[rel]['role'] if rel in current else ('advanced' if advanced else ('alias' if 'alt.csv' in rel.lower() else 'primary'))
        advanced=role=='advanced'
        entry=dict(raw_path=rel,source=source,role=role,current=rel in current,
                   source_url=urls.get((source,role),''),status='unread')
        manifest.append(entry)
        try:
            body=_read_body(path);entry['source_hash']=hashlib.sha256(body).hexdigest()
            if rel in current and entry['source_hash']!=current[rel]['sha256']:
                raise TriageError(f'監査時原本から変更された: {rel}')
            records=R.read_ofac_xml(body,label) if advanced else R.read_classic(body,label,alias=role=='alias')
            entry.update(status='parsed',name_count=len(records))
            for record in records:
                raw_name=record['name']
                forms=[('exact',raw_name)]
                if ',' in raw_name and not advanced:forms.append(('comma_removed',raw_name.replace(',','')))
                for transform,name in forms:
                    key=match_key(name)
                    if key in target_keys:
                        proof={k:record.get(k) for k in ('name','source_id','category','locator','weak','primary')}
                        proof.update({k:entry[k] for k in ('raw_path','source_url','source_hash','current')},field='name',transform=transform)
                        found[key][_key_json(_facts(proof))]=proof
            if role=='primary':
                for line,values in enumerate(csv.reader(io.StringIO(R.decode(body)),strict=True),1):
                    if len(values)!=12:continue
                    for field,col in [('call_sign',5),('vessel_owner',10),('title',4)]:
                        key=match_key(values[col])
                        if key in target_keys:
                            proof=dict(name=values[col],source_id=values[0].strip(),category=label,locator=f'line:{line}/{field}',
                                       weak=False,primary=False,field=field,transform='exact',
                                       **{k:entry[k] for k in ('raw_path','source_url','source_hash','current')})
                            found[key][_key_json(_facts(proof))]=proof
        except (ValueError,OSError,EOFError,csv.Error) as exc:
            entry.update(status='unreadable',error=str(exc));errors.append(dict(raw_path=rel,error=str(exc),current=entry['current']))
            if rel in current:
                raise TriageError(f'現行原本のtriage失敗: {rel}: {exc}') from exc
    return {key:sorted(vals.values(),key=_key_json) for key,vals in found.items()},manifest,errors



class _NoticeBody(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.text=[];self.found=False

    def handle_starttag(self,tag,attrs):
        if tag=='div':
            if self.depth:self.depth+=1
            elif 'field--name-field-body' in dict(attrs).get('class','').split():
                self.depth=1;self.found=True

    def handle_endtag(self,tag):
        if tag=='div' and self.depth:self.depth-=1

    def handle_data(self,data):
        if self.depth:self.text.append(data)


def notice_mentions(body,names):
    parser=_NoticeBody();parser.feed(R.decode(body));parser.close()
    if not parser.found or parser.depth:
        raise TriageError('OFAC報道発表本文の構造不明')
    text=R.clean(' '.join(parser.text));folded=text.casefold()
    return {name for name in names if name.casefold() in folded
            and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',text,re.I)}


def add_notice_witnesses(root,masters,witnesses,manifest):
    index=root/'docs/evidence/integrity-notices/manifest.json'
    if not index.exists():return
    documents=json.loads(index.read_text(encoding='utf-8'))
    byname=defaultdict(set)
    for row in masters:byname[row['display_name']].add(row['match_key'])
    for doc in documents:
        path=(root/doc['raw_path']).resolve()
        if not path.is_relative_to(root/'docs/evidence/integrity-notices'):
            raise TriageError('補助報道発表パスが証跡領域外')
        body=_read_body(path)
        if hashlib.sha256(body).hexdigest()!=doc['source_hash']:
            raise TriageError('補助報道発表hash不一致')
        names=notice_mentions(body,byname)
        manifest.append(dict(doc,status='parsed',role='notice_mention',name_count=len(names),current=False))
        for name in sorted(names):
            proof=dict(name=name,source_id=None,category='OFAC_NOTICE',current=False,field='notice_text',
                       transform='exact',weak=False,primary=False,locator='Recent Actions Body / literal bounded mention',
                       **{k:doc[k] for k in ('raw_path','source_url','source_hash')})
            for key in sorted(byname[name]):witnesses.setdefault(key,[]).append(proof)


def triage(root,report):
    root=root.resolve()
    for rel,expected in report.get('inputs',{}).items():
        path=(root/rel).resolve()
        if not path.is_relative_to(root/'data') or not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise TriageError(f'監査入力が一致しない: {rel}')
    masters=_csv(root/'data/master/master.csv')
    index_path=root/'data/master/ofac_alias_history.csv'
    index=_csv(index_path) if index_path.exists() else []
    targets={f['master']['match_key'] for f in report['findings'] if isinstance(f.get('master'),dict)}
    targets.update(match_key(f['master']['display_name']) for f in report['findings'] if isinstance(f.get('master'),dict))
    witnesses,manifest,errors=collect_witnesses(root,report,targets)
    add_notice_witnesses(root,[r for r in masters if r['match_key'] in targets],witnesses,manifest)
    result=classify(report,masters,index,witnesses)
    result.update(archive_manifest=manifest,archive_errors=errors)
    return result


def write_outputs(directory,result,stem=None):
    directory.mkdir(parents=True,exist_ok=True)
    stem=stem or ('triage-'+uuid.uuid4().hex)
    path=directory/(stem+'.json.gz')
    with path.open('xb') as stream:stream.write(gzip.compress((_key_json(result)+'\n').encode(),mtime=0))
    csv_path=directory/(stem+'.csv')
    fields=['review_id','kind','checked_source','classification','master_key','display_name','retention_eligible','approved','master_sha256','evidence_sha256']
    with csv_path.open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(result['items'])
    return path,csv_path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--audit-report',type=Path,required=True)
    parser.add_argument('--out-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        report=json.loads(_read_body(args.audit_report));result=triage(args.root,report)
        paths=write_outputs(args.out_dir,result)
    except (OSError,ValueError,csv.Error) as exc:
        print(f'TRIAGE BLOCKED: {exc}',file=sys.stderr);return 1
    print(json.dumps(dict(counts=result['counts'],retention_candidates=result['retention_candidates'],approved_count=0,paths=[str(p) for p in paths]),ensure_ascii=False))
    return 0


if __name__=='__main__':sys.exit(main())
