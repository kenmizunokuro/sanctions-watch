import contextlib
import copy
import hashlib
import importlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import requests
from src.fetch import Fetched

XML='https://scsanctions.un.org/resources/xml/en/name/consolidated.xml'
UPDATES='https://main.un.org/securitycouncil/en/content/list-updates-unsc-consolidated-list'
PRESS='https://main.un.org/securitycouncil/en/sanctions/1267/press-releases'
RSS_URL='https://main.un.org/securitycouncil/en/feed/1.0/updates_unsc_consolidated_list'
PRESS_HOME='https://press.un.org/en'
PRESS_RSS='https://press.un.org/en/rss.xml'
RSS=b'<rss version="2.0"><channel><title>UN updates</title><item><guid>node/620152</guid><pubDate>28 Oct 2026</pubDate></item><item><guid>node/620152</guid><pubDate>26 Oct 2026</pubDate></item><item><guid>node/620152</guid><pubDate>28 Oct 2026</pubDate></item></channel></rss>'
NOW=datetime(2026,11,11,0,0,tzinfo=timezone.utc)


class UNTransportProbeTests(unittest.TestCase):
    def setUp(self):
        try:self.P=importlib.import_module('src.un_transport_probe')
        except ImportError:self.fail('UN read-only transport probe implementation is missing')
        self.items={url:Fetched(url=url,final_url=url,http_status=200,body=body,sha256='0'*64,
                               headers={'Content-Type':kind})
                    for url,body,kind in [(XML,b'<CONSOLIDATED_LIST/>','application/xml'),
                                          (UPDATES,b'<html>updates</html>','text/html'),
                                          (PRESS,b'<html>press</html>','text/html'),
                                          (RSS_URL,RSS,'application/rss+xml'),
                                          (PRESS_HOME,b'<html><a href="/en/rss.xml">RSS</a></html>','text/html'),
                                          (PRESS_RSS,RSS,'application/rss+xml')]}

    def getter(self,url,**kwargs):
        # A stale conditional response cannot be evidence for this fresh probe.
        self.assertIs(kwargs['allow_conditional'],False)
        self.assertEqual(kwargs['timeout'],15)
        self.assertIsNotNone(kwargs['url_validator'])
        self.assertGreaterEqual(kwargs['max_bytes'],len(RSS))
        value=self.items[url]
        if isinstance(value,Exception):raise value
        return copy.deepcopy(value)

    def probe(self):return self.P.probe(fetcher=self.getter,now=NOW)
    def observation(self,result,url):return next(r for r in result['observations'] if r['url']==url)

    def test_official_press_rss_is_observed_independently_of_blocked_committee_host(self):
        self.items[UPDATES].http_status=202
        self.items[UPDATES].body=b''
        self.items[UPDATES].headers={'x-amzn-waf-action':'challenge'}
        report=self.probe()
        self.assertEqual({r['url'] for r in report['observations']},set(self.items))
        self.assertEqual(self.observation(report,UPDATES)['transport_status'],'ACCESS_CHALLENGE')
        self.assertEqual(self.observation(report,PRESS_HOME)['http_status'],200)
        item=self.observation(report,PRESS_RSS)
        self.assertEqual(item['transport_status'],'HTTP200_UNVALIDATED')
        self.assertEqual(item['rss_shape']['format'],'rss')
        self.assertNotIn('source_healthy',report)

    def test_press_rss_challenge_is_not_parsed_even_with_valid_rss(self):
        self.items[PRESS_RSS].headers={'x-amzn-waf-action':'challenge'}
        item=self.observation(self.probe(),PRESS_RSS)
        self.assertEqual(item['transport_status'],'ACCESS_CHALLENGE')
        self.assertNotIn('rss_shape',item)

    def test_press_rss_http_failure_retains_original_and_new_homepage_observations(self):
        response=requests.Response();response.status_code=503;response.url=PRESS_RSS
        self.items[PRESS_RSS]=requests.HTTPError('not available',response=response)
        report=self.probe()
        self.assertTrue(report['probe_completed'])
        self.assertEqual(self.observation(report,PRESS_RSS)['transport_status'],'HTTP_ERROR')
        self.assertEqual(self.observation(report,XML)['http_status'],200)
        self.assertEqual(self.observation(report,PRESS_HOME)['http_status'],200)

    def test_explicit_access_challenge_and_empty_202_are_distinct(self):
        cases=[(202,b'',{'X-Amzn-Waf-Action':'challenge'},'ACCESS_CHALLENGE'),
               (202,b'',{},'EMPTY_202'),(200,b'',{},'EMPTY_200'),
               (304,None,{},'UNEXPECTED_304'),
               (200,b'<html>challenge</html>',{'x-amzn-waf-action':'captcha'},'ACCESS_CHALLENGE')]
        for code,body,headers,want in cases:
            with self.subTest(code=code,headers=headers):
                self.items[UPDATES]=Fetched(url=UPDATES,final_url=UPDATES,http_status=code,body=body,headers=headers)
                r=self.observation(self.probe(),UPDATES)
                self.assertEqual(r['transport_status'],want)
                self.assertEqual(r['http_status'],code)

    def test_reports_body_hash_and_duplicate_rss_shape_without_claiming_list_validation(self):
        r=self.probe();self.assertTrue(r['probe_completed']);self.assertEqual(len(r['observations']),6)
        item=self.observation(r,RSS_URL)
        self.assertEqual(r['checked_at'],'2026-11-11T00:00:00Z')
        self.assertEqual(item['transport_status'],'HTTP200_UNVALIDATED')
        self.assertEqual(item['sha256'],hashlib.sha256(RSS).hexdigest())
        self.assertEqual(item['body_bytes'],len(RSS))
        self.assertEqual(item['rss_shape'],{'format':'rss','item_count':3,'distinct_guids':1,'distinct_publication_labels':2})
        self.assertNotIn('parties',json.dumps(r))
        self.assertNotIn('names',item)

    def test_does_not_serialize_headers_or_temporary_credentials(self):
        self.items[XML].final_url='https://unsolprodfiles.blob.core.windows.net/publiclegacyxmlfiles/EN/current.xml?sv=1&sig=SAS_SECRET'
        self.items[XML].headers.update({'Set-Cookie':'COOKIE_SECRET','Authorization':'AUTH_SECRET',
                                        'ETag':'public-etag','X-Amzn-Waf-Action':'challenge'})
        r=self.probe();body=json.dumps(r)
        for secret in ['SAS_SECRET','COOKIE_SECRET','AUTH_SECRET']:self.assertNotIn(secret,body)
        item=self.observation(r,XML)
        self.assertEqual(item['final_url'],'https://unsolprodfiles.blob.core.windows.net/publiclegacyxmlfiles/EN/current.xml')
        self.assertEqual(item['etag'],'public-etag')
        self.assertEqual(item['headers']['x-amzn-waf-action'],'challenge')

    def test_timeout_is_reported_without_suppressing_other_targets(self):
        self.items[UPDATES]=requests.Timeout('timeout https://unsolprodfiles.blob.core.windows.net/publiclegacyxmlfiles/EN/x.xml?sig=ERROR_SECRET')
        r=self.probe();failed=self.observation(r,UPDATES)
        self.assertTrue(r['probe_completed']);self.assertEqual(len(r['observations']),6)
        self.assertEqual(failed['transport_status'],'FETCH_ERROR');self.assertIsNone(failed['http_status'])
        self.assertEqual(failed['error_type'],'Timeout');self.assertNotIn('ERROR_SECRET',json.dumps(r))
        self.assertEqual(self.observation(r,RSS_URL)['rss_shape']['item_count'],3)

    def test_http_error_keeps_response_metadata_without_inventing_body_bytes(self):
        response=requests.Response();response.status_code=403;response.url=RSS_URL
        response.headers={'Content-Type':'text/html','Set-Cookie':'PRIVATE_COOKIE'}
        self.items[RSS_URL]=requests.HTTPError('forbidden',response=response)
        item=self.observation(self.probe(),RSS_URL)
        self.assertEqual(item['transport_status'],'HTTP_ERROR');self.assertEqual(item['http_status'],403)
        self.assertIsNone(item['body_bytes']);self.assertIsNone(item['sha256'])
        self.assertNotIn('PRIVATE_COOKIE',json.dumps(item))

    def test_unsafe_or_malformed_rss_never_appears_as_rss_shape(self):
        for body,want in [(b'<!DOCTYPE rss [<!ENTITY e "EXPANDED">]><rss><channel>&e;</channel></rss>','unsafe_xml'),
                          (b'<rss><channel>','invalid_xml'),(b'<html/>','other_xml')]:
            with self.subTest(want=want):
                self.items[RSS_URL].body=body
                item=self.observation(self.probe(),RSS_URL)
                self.assertEqual(item['rss_shape']['format'],want)
                self.assertNotIn('item_count',item['rss_shape'])
        self.items[RSS_URL].headers={'x-amzn-waf-action':'challenge'}
        self.items[RSS_URL].body=RSS
        self.assertNotIn('rss_shape',self.observation(self.probe(),RSS_URL))

    def test_real_urllib3_relative_signed_request_target_never_leaks(self):
        from urllib3.connectionpool import HTTPSConnectionPool
        from urllib3.exceptions import MaxRetryError
        pool = HTTPSConnectionPool('unsolprodfiles.blob.core.windows.net')
        self.items[XML] = requests.ConnectionError(MaxRetryError(
            pool, '/publiclegacyxmlfiles/EN/current.xml?sv=1&sig=TEMPORARY_SECRET',
            reason=ConnectionRefusedError('refused')))
        result = self.probe()
        self.assertNotIn('TEMPORARY_SECRET', json.dumps(result))
        self.assertEqual(self.observation(result, XML)['error_type'], 'ConnectionError')
        self.assertEqual(len(result['observations']), 6)

    def test_unknown_rss_encoding_retains_all_observations(self):
        self.items[RSS_URL].body = b'<?xml version="1.0" encoding="nonexistent"?><rss><channel/></rss>'
        try:result = self.probe()
        except Exception as error:self.fail('RSS encoding must be an observation, not discard the report: '+type(error).__name__)
        self.assertTrue(result['probe_completed']);self.assertEqual(len(result['observations']),6)
        self.assertEqual(self.observation(result,RSS_URL)['rss_shape']['format'],'unsupported_encoding')
        self.assertEqual(self.observation(result,XML)['http_status'],200)

    def test_utf16_entity_declarations_are_rejected_before_expansion(self):
        text='<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE rss [<!ENTITY e "EXPANDED">]><rss><channel><item><guid>&e;</guid></item></channel></rss>'
        for encoding in ['utf-16','utf-16-le','utf-16-be']:
            with self.subTest(encoding=encoding):
                self.items[RSS_URL].body=text.encode(encoding)
                item=self.observation(self.probe(),RSS_URL)
                self.assertEqual(item['rss_shape']['format'],'unsafe_xml')
                self.assertNotIn('item_count',item['rss_shape'])

    def test_cli_rejects_business_paths_symlinks_and_unrelated_existing_json(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.chdir(folder):
            Path('data').mkdir();Path('dist').mkdir()
            Path('data/state.json').write_text('{"financial_state":true}')
            Path('settings.json').write_text('{"settings":true}')
            Path('dist/redirect.json').symlink_to('../data/state.json')
            before = {p: p.read_bytes() for p in [Path('data/state.json'), Path('settings.json')]}
            for out in ['data/state.json', 'data/new-report.json', 'dist/redirect.json', 'settings.json', 'dist/file.csv']:
                with self.subTest(out=out),contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError):self.P.main(['--out',out],fetcher=self.getter)
            self.assertEqual(before, {p:p.read_bytes() for p in before})
            self.assertFalse(Path('data/new-report.json').exists())

    def test_cli_writes_only_diagnostic_output_even_if_sources_fail(self):
        self.items[UPDATES].http_status=202;self.items[UPDATES].body=b''
        with tempfile.TemporaryDirectory() as folder,contextlib.chdir(folder):
            data=Path('data/master');data.mkdir(parents=True);(data/'master.csv').write_bytes(b'financial-master')
            Path('data/state.json').write_bytes(b'financial-state')
            before={str(p):p.read_bytes() for p in Path('data').rglob('*') if p.is_file()}
            with contextlib.redirect_stdout(io.StringIO()):
                code=self.P.main(['--out','dist/probe.json'],fetcher=self.getter)
                self.items[UPDATES].http_status=200;self.items[UPDATES].body=b'<html>healthy again</html>'
                code=self.P.main(['--out','dist/probe.json'],fetcher=self.getter)
            report=json.loads(Path('dist/probe.json').read_bytes())
            self.assertEqual(code,0);self.assertTrue(report['probe_completed'])
            self.assertEqual(self.observation(report,UPDATES)['transport_status'],'HTTP200_UNVALIDATED')
            self.assertEqual(before,{str(p):p.read_bytes() for p in Path('data').rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
