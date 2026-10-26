"""Protect source context and read-only boundaries; no mocked network/parser."""
import gzip
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def html(body):
    return ('<html><nav>Jane Doe</nav><div class="field field--name-field-body">' + body + '</div></html>').encode()


class ContextTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('src.integrity_notice_context'),
                             'independent notice context reader is missing')
        return importlib.import_module('src.integrity_notice_context')

    def test_preserves_complete_record_including_linked_target_without_identity_inference(self):
        result=self.module().read_contexts(html('<h3>SDN List</h3><h4>Deletions:</h4>'
            '<p><a><font>VELES LIMITED, Cyprus [RUSSIA] (Linked To: Jane Doe).</font></a></p>'),{'Jane Doe'})
        self.assertEqual(len(result['matches']),1)
        hit=result['matches'][0]
        self.assertEqual(hit['record'],'VELES LIMITED, Cyprus [RUSSIA] (Linked To: Jane Doe).')
        self.assertEqual(hit['section'],'SDN List');self.assertEqual(hit['heading'],'Deletions:')
        self.assertEqual(hit['record'][hit['start']:hit['end']],'Jane Doe')
        self.assertEqual(result['record_count'],1)
        self.assertNotIn('party_id',hit);self.assertNotIn('primary',hit)

    def test_double_br_and_paragraph_boundaries_do_not_join_different_entities(self):
        result=self.module().read_contexts(html('<h3>SDN List</h3><h4>Changes:</h4>'
            '<p>JANE DOE, one<br>address<br><br>JANE DOE, two</p><p>JANE DOE, three</p>'),{'Jane Doe'})
        self.assertEqual([x['record'] for x in result['matches']],['JANE DOE, one address','JANE DOE, two','JANE DOE, three'])
        self.assertEqual([x['record_number'] for x in result['matches']],[1,2,3])

    def test_inline_formatting_keeps_whitespace_and_empty_body_is_not_success(self):
        M=self.module()
        result=M.read_contexts(html('<p><strong>Jane</strong> <span>Doe</span>&nbsp;[TAG]</p>'),{'Jane Doe'})
        self.assertEqual(len(result['matches']),1)
        self.assertEqual(result['matches'][0]['record'],'Jane Doe [TAG]')
        with self.assertRaises(ValueError):M.read_contexts(html(''),{'Jane Doe'})

    def test_new_list_heading_resets_previous_action_and_preserves_unknown_heading(self):
        result=self.module().read_contexts(html('<h3>SDN</h3><h4>Deletions</h4><p>Jane Doe</p>'
            '<h3>FSE</h3><p>Jane Doe</p><h4>Unknown future operation</h4><p>Jane Doe</p>'),{'Jane Doe'})
        self.assertEqual([(x['section'],x['heading']) for x in result['matches']],
                         [('SDN','Deletions'),('FSE',''),('FSE','Unknown future operation')])
        self.assertTrue(all('action' not in x for x in result['matches']))

    def test_excludes_navigation_and_word_substrings_but_keeps_all_literal_occurrences(self):
        result=self.module().read_contexts(html('<p>Jane Doer; Jane Doe and JANE DOE; Alias X.</p>'),{'Jane Doe','Ali'})
        self.assertEqual([x['name'] for x in result['matches']],['Jane Doe','Jane Doe'])
        self.assertEqual(result['matched_names'],['Jane Doe'])

    def test_table_rows_preserve_raw_uid_context_and_ignore_css(self):
        result=self.module().read_contexts(html('<style>Jane Doe { color:red }</style>'
            '<h4>Administrative update</h4><table><thead><tr><th>Name</th><th>UID</th></tr></thead>'
            '<tbody><tr><td>Jane Doe</td><td>123 retained / 456 removed</td></tr></tbody></table>'),{'Jane Doe'})
        self.assertEqual(len(result['matches']),1)
        self.assertEqual(result['matches'][0]['record'],'Jane Doe | 123 retained / 456 removed')
        self.assertEqual(result['matches'][0]['record_format'],'table_row')
        self.assertNotIn('source_id',result['matches'][0])

    def test_nested_cell_blocks_and_double_breaks_preserve_the_entire_table_row(self):
        result=self.module().read_contexts(html('<table><tr><td><p>Jane Doe</p></td>'
            '<td><div>Linked To: Other Person<br><br>ID 123</div></td></tr></table>'),{'Jane Doe'})
        self.assertEqual(len(result['matches']),1)
        self.assertEqual(result['matches'][0]['record'],'Jane Doe | Linked To: Other Person ID 123')
        self.assertEqual(result['matches'][0]['record_format'],'table_row')

    def test_invalid_balanced_parent_placement_and_void_end_tags_are_rejected(self):
        M=self.module()
        for fragment in ['<p>Jane Doe<p>Linked To: Other Person</p></p>', '<td>Jane Doe</td>',
                         '<p>Jane</br> Doe</p>', '<table>Jane Doe</table>',
                         '<table><tr><td>Jane Doe<table><tr><td>Nested</td></tr></table></td></tr></table>']:
            with self.subTest(fragment=fragment):
                with self.assertRaises(ValueError):M.read_contexts(html(fragment),{'Jane Doe'})

    def test_missing_duplicate_unclosed_and_unknown_body_structure_are_not_empty_success(self):
        M=self.module()
        cases=[b'<html>Jane Doe</html>', b'<div class="field--name-field-body">Jane Doe',
               html('Jane Doe')+html('Jane Doe'),html('<iframe>Jane Doe</iframe>'),
               html('<p><script>Jane Doe</script></p>'),html('<h4>Jane Doe')]
        for body in cases:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):M.read_contexts(body,{'Jane Doe'})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.directory=self.root/'docs/evidence/integrity-notices';self.directory.mkdir(parents=True)
        self.body=html('<h3>SDN</h3><h4>Deletions</h4><p>VELES, Cyprus (Linked To: Jane Doe).</p>')
        (self.directory/'notice.html.gz').write_bytes(gzip.compress(self.body,mtime=0))
        self.manifest=[dict(source_url='https://ofac.treasury.gov/recent-actions/20251218',
                            source_hash=hashlib.sha256(self.body).hexdigest(),
                            raw_path='docs/evidence/integrity-notices/notice.html.gz')]
        self.path=self.root/'baseline.json.gz'
        self.baseline=dict(items=[dict(review_id='1'*64,display_name='Jane Doe',classification='ORIGIN_UNRESOLVED'),
                                 dict(review_id='2'*64,display_name='No Witness',classification='NON_NAME_FIELD_ONLY'),
                                 dict(review_id='3'*64,display_name='VELES',classification='CURRENT_WEAK_ONLY')])
        self.pin()

    def pin(self):
        body=json.dumps(self.baseline).encode();self.path.write_bytes(gzip.compress(body,mtime=0))
        self.hash=hashlib.sha256(body).hexdigest()
        (self.directory/'manifest.json').write_text(json.dumps(self.manifest))

    def build(self):
        return ContextTests.module(self).build_report(self.root,self.path,baseline_sha256=self.hash)

    def test_hash_bound_report_keeps_unmatched_rows_and_never_approves_or_changes_inputs(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        r=self.build()
        self.assertEqual(r['status'],'RESEARCH_ONLY');self.assertEqual(r['target_count'],2)
        self.assertEqual(r['matched_item_count'],1)
        self.assertEqual([(x['display_name'],len(x['contexts'])) for x in r['items']],[('Jane Doe',1),('No Witness',0)])
        self.assertEqual(r['items'][0]['contexts'][0]['source_url'],self.manifest[0]['source_url'])
        self.assertFalse(r['current_listing_verified']);self.assertFalse(r['identity_verified'])
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_changed_original_hash_or_baseline_refuses_partial_report(self):
        M=ContextTests.module(self)
        with self.assertRaises(ValueError):M.build_report(self.root,self.path,baseline_sha256='0'*64)
        (self.directory/'notice.html.gz').write_bytes(gzip.compress(b'changed'))
        with self.assertRaises(ValueError):self.build()

    def test_unofficial_url_escape_and_duplicate_document_are_rejected(self):
        for change in [dict(source_url='https://unofficial.test/20251218'),dict(raw_path='../elsewhere'),
                       dict(source_url='https://ofac.treasury.gov.evil.test/recent-actions/20251218')]:
            with self.subTest(change=change):
                original=self.manifest;self.manifest=[dict(original[0],**change)];self.pin()
                with self.assertRaises(ValueError):self.build()
                self.manifest=original
        self.manifest*=2;self.pin()
        with self.assertRaises(ValueError):self.build()

    def test_cli_writes_no_partial_report_when_balanced_html_is_invalid(self):
        self.body=html('<p>Jane Doe<p>Linked To: Other Person</p></p>')
        (self.directory/'notice.html.gz').write_bytes(gzip.compress(self.body,mtime=0))
        self.manifest[0]['source_hash']=hashlib.sha256(self.body).hexdigest();self.pin()
        result=subprocess.run([sys.executable,'-m','src.integrity_notice_context','--root',str(self.root),
            '--baseline',str(self.path),'--baseline-sha256',self.hash,'--out',str(self.root/'broken.gz')],
            env=dict(os.environ,PYTHONPATH=str(ROOT)),capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'broken.gz').exists())

    def test_cli_is_reproducible_and_refuses_overwrite_or_invalid_input(self):
        ContextTests.module(self)
        env=dict(os.environ,PYTHONPATH=str(ROOT))
        for output in ['one.gz','two.gz']:
            result=subprocess.run([sys.executable,'-m','src.integrity_notice_context','--root',str(self.root),
                '--baseline',str(self.path),'--baseline-sha256',self.hash,'--out',str(self.root/output)],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(gzip.decompress((self.root/'one.gz').read_bytes()),gzip.decompress((self.root/'two.gz').read_bytes()))
        old=(self.root/'one.gz').read_bytes()
        result=subprocess.run([sys.executable,'-m','src.integrity_notice_context','--root',str(self.root),
            '--baseline',str(self.path),'--baseline-sha256',self.hash,'--out',str(self.root/'one.gz')],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertEqual(old,(self.root/'one.gz').read_bytes())
        self.path.write_bytes(gzip.compress(b'{}'))
        result=subprocess.run([sys.executable,'-m','src.integrity_notice_context','--root',str(self.root),
            '--baseline',str(self.path),'--baseline-sha256',self.hash,'--out',str(self.root/'bad.gz')],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.root/'bad.gz').exists())


if __name__=='__main__':unittest.main()
