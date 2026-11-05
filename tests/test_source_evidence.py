import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src import source_evidence
from src.source_evidence import lookup


MOF_URL = "https://www.mof.go.jp/example.csv"
MOF_DOC = {
    "source": "mof",
    "role": "mof",
    "sha256": "a" * 64,
    "raw_path": "data/raw/mof/example.csv.gz",
    "source_url": MOF_URL,
    "name_count": 1,
}
MOF_NAME = {
    "source": "財務省",
    "category": "9",
    "source_id": "009-000047",
    "un_id": "CDi.047",
    "name": "LUMISA, MUHAMAD",
    "primary": False,
    "weak": False,
    "source_url": MOF_URL,
    "source_hash": "a" * 64,
    "raw_path": MOF_DOC["raw_path"],
    "locator": "line:10/別名（英語）",
    "master_key": "muhamadlumisa",
    "master_rows": 1,
    "id_link": "reconstructed_by_name_and_category",
}


def report():
    return {
        "schema_version": 1,
        "scope": ["mof"],
        "status": "REVIEW",
        "documents": [dict(MOF_DOC)],
        "lineage": [dict(MOF_NAME)],
        "findings": [],
    }


def ofac_report():
    data = report() | {"scope": ["ofac"], "documents": [], "lineage": []}
    for source, category in (("ofac_sdn", "SDN"), ("ofac_cons", "Consolidated")):
        for role in ("primary", "alias", "advanced"):
            url = f"https://sanctionslistservice.ofac.treas.gov/{source}/{role}"
            document = MOF_DOC | {
                "source": source, "role": role, "source_url": url,
                "raw_path": f"data/raw/{source}/{role}.gz",
            }
            data["documents"].append(document)
            data["lineage"].append(MOF_NAME | {
                "source": "OFAC", "category": category, "source_id": "123", "un_id": "",
                "raw_path": document["raw_path"], "source_url": url,
                "id_link": "explicit_party_alias_index",
            })
    return data


class SourceEvidenceTest(unittest.TestCase):
    def test_name_lookup_returns_document_and_source_identity(self):
        result = lookup(report(), "ＭＵＨＡＭＡＤ　ＬＵＭＩＳＡ")
        self.assertEqual(result["audit_status"], "REVIEW")
        self.assertEqual(result["sources"]["mof"]["status"], "FOUND")
        self.assertEqual(result["sources"]["ofac"]["status"], "OUT_OF_SCOPE")
        self.assertEqual(result["sources"]["mof"]["evidence"], [MOF_NAME])

    def test_historical_key_remains_visible_when_name_matches_with_current_rules(self):
        data = report() | {"lineage": [MOF_NAME | {"master_key": "historicalkey"}]}
        result = lookup(data, "Muhamad Lumisa")["sources"]["mof"]
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["evidence"][0]["master_key"], "historicalkey")
        self.assertEqual(result["evidence"][0]["match_basis"], "CURRENT_NAME_KEY")

    def test_no_witness_keeps_legacy_finding_separate_from_official_evidence(self):
        data = report()
        data["findings"] = [{
            "kind": "EXTRA_REGISTRATION",
            "checked_source": "財務省",
            "detail": "確認対象",
            "blocking": False,
            "legacy_accepted": True,
            "legacy_expires_at": "2026-11-25T08:21:00Z",
            "master": {"match_key": "oldname", "display_name": "Old Name", "status": "有効"},
        }]
        result = lookup(data, "Old Name")
        self.assertEqual(result["sources"]["mof"]["status"], "NO_WITNESS")
        self.assertEqual(result["sources"]["mof"]["evidence"], [])
        finding, = result["findings"]
        self.assertTrue(finding["legacy_accepted"])
        self.assertEqual(finding["master"][0]["status"], "有効")

    def test_source_incomplete_is_distinct_from_no_witness(self):
        data = report()
        data["scope"].append("ofac")
        data["findings"] = [{"kind": "UNDETERMINED", "source": "ofac_sdn", "detail": "原本なし"}]
        result = lookup(data, "Old Name")
        self.assertEqual(result["sources"]["mof"]["status"], "NO_WITNESS")
        self.assertEqual(result["sources"]["ofac"]["status"], "INCOMPLETE")
        self.assertEqual(result["source_issues"], data["findings"])

    def test_a_missing_lineage_or_invalid_link_is_incomplete(self):
        for changes in ({"lineage": []}, {"lineage": [MOF_NAME | {"source_hash": "b" * 64}]}):
            with self.subTest(changes=changes):
                result = lookup(report() | changes, "Muhamad Lumisa")
                self.assertEqual(result["sources"]["mof"]["status"], "INCOMPLETE")
                self.assertEqual(result["sources"]["mof"]["evidence"], [])

    def test_broken_witness_fields_do_not_count_as_checked(self):
        for field in ("master_key", "name", "source_id", "locator", "master_rows", "weak"):
            with self.subTest(field=field):
                data = report()
                del data["lineage"][0][field]
                result = lookup(data, "Muhamad Lumisa")["sources"]["mof"]
                self.assertEqual(result, {"status": "INCOMPLETE", "evidence": []})

    def test_finding_identifies_disputed_witness_without_raw_data(self):
        data = report()
        data["findings"] = [{
            "kind": "SOURCE_MISMATCH",
            "official": MOF_NAME | {"raw_record": {"unrelated": "value"}},
            "master": {"match_key": "wrongkey", "display_name": "Muhamad Lumisa"},
        }]
        finding, = lookup(data, "Muhamad Lumisa")["findings"]
        self.assertEqual(finding["official"]["source_id"], "009-000047")
        self.assertEqual(finding["master"][0]["match_key"], "wrongkey")
        self.assertNotIn("raw_record", finding["official"])

    def test_ofac_same_fixed_ref_is_kept_separate_by_list_and_document(self):
        data = ofac_report()
        result = lookup(data, "Muhamad Lumisa")["sources"]["ofac"]
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(len(result["evidence"]), 6)
        self.assertEqual({row["category"] for row in result["evidence"]}, {"SDN", "Consolidated"})
        data["documents"].pop()
        result = lookup(data, "Muhamad Lumisa")["sources"]["ofac"]
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(len(result["evidence"]), 5)

    def test_mof_record_lookup_preserves_leading_zeroes_and_excludes_namesakes(self):
        data = report()
        alias = MOF_NAME | {"name": "Different Alias", "master_key": "differentaliaskey"}
        namesake = MOF_NAME | {"source_id": "009-000048"}
        data["documents"][0]["name_count"] = 3
        data["lineage"].extend([alias, namesake])
        result = source_evidence.lookup_party(data, "mof", "009-000047")["sources"]
        self.assertEqual(result, {"mof": {"status": "FOUND", "evidence": [MOF_NAME, alias]}})
        missing = source_evidence.lookup_party(data, "mof", "9-47")["sources"]["mof"]
        self.assertEqual(missing, {"status": "NO_WITNESS", "evidence": []})

    def test_ofac_party_lookup_checks_only_requested_list_coverage(self):
        data = ofac_report()
        data["documents"].pop()
        data["findings"] = [{"kind": "UPSTREAM_FAILED", "source": "ofac_cons"}]
        sdn = source_evidence.lookup_party(data, "ofac-sdn", "123")
        self.assertEqual(sdn["source_issues"], [])
        self.assertEqual(sdn["sources"], {
            "ofac-sdn": {"status": "FOUND", "evidence": data["lineage"][:3]},
        })
        cons = source_evidence.lookup_party(data, "ofac-consolidated", "123")["sources"]
        self.assertEqual(cons, {
            "ofac-consolidated": {"status": "INCOMPLETE", "evidence": data["lineage"][3:5]},
        })
        outside = source_evidence.lookup_party(report(), "ofac-sdn", "123")["sources"]
        self.assertEqual(outside, {"ofac-sdn": {"status": "OUT_OF_SCOPE", "evidence": []}})

    def test_invalid_ofac_witness_id_cannot_count_as_checked(self):
        data = ofac_report()
        for witness in data["lineage"][:3]:
            witness["source_id"] = "invalid"
        result = source_evidence.lookup_party(data, "ofac-sdn", "123")["sources"]
        self.assertEqual(result, {"ofac-sdn": {"status": "INCOMPLETE", "evidence": []}})

    def test_party_findings_require_an_explicit_match_in_same_list(self):
        data = ofac_report()
        sdn = {"list": "SDN", "party_id": "123", "name": "SDN name"}
        cons = {"list": "Consolidated", "party_id": "123", "name": "Other name"}
        data["findings"] = [
            {"kind": "DUPLICATE_RECORD", "index": [cons, sdn, sdn | {"party_id": "234"}]},
            {"kind": "ALIAS_QUALITY_MISMATCH", "official": data["lineage"][0]},
            {"kind": "EXTRA_REGISTRATION", "master": {"display_name": MOF_NAME["name"]}},
            {"kind": "STATUS_MISMATCH", "official": data["lineage"][3]},
        ]
        index_finding, official_finding = source_evidence.lookup_party(data, "ofac-sdn", "123")["findings"]
        self.assertEqual(index_finding["index"], [sdn])
        self.assertEqual(official_finding["official"]["source_id"], "123")
        self.assertEqual(official_finding["official"]["category"], "SDN")

    def test_cli_requires_source_and_id_together_and_excludes_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            path.write_text(json.dumps(report()), encoding="utf-8")
            args = [sys.executable, "-m", "src.source_evidence", str(path)]
            selector = ["--source", "mof", "--source-id", "009-000047"]
            completed = subprocess.run(args + selector, capture_output=True, text=True, check=True)
            result = json.loads(completed.stdout)
            self.assertEqual(result["query"], {"source": "mof", "source_id": "009-000047"})
            self.assertEqual(result["sources"]["mof"]["status"], "FOUND")
            for invalid in (selector[:2], selector[2:], selector + ["--name", "Any"],
                            ["--source", "ofac-sdn", "--source-id", "12 3"]):
                with self.subTest(invalid=invalid):
                    completed = subprocess.run(args + invalid, capture_output=True, text=True)
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")

    def test_matching_nonofficial_url_is_not_exposed_as_evidence(self):
        url = "https://mof.go.jp.example.invalid/not-official"
        data = report() | {
            "documents": [MOF_DOC | {"source_url": url}],
            "lineage": [MOF_NAME | {"source_url": url}],
        }
        result = lookup(data, "Muhamad Lumisa")["sources"]["mof"]
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["evidence"], [])

    def test_upstream_failure_cannot_produce_no_witness(self):
        data = report()
        data["findings"] = [{"kind": "UPSTREAM_FAILED", "detail": "監視失敗"}]
        result = lookup(data, "Old Name")
        self.assertEqual(result["sources"]["mof"]["status"], "INCOMPLETE")

    def test_cli_accepts_gzip_and_records_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json.gz"
            payload = gzip.compress(json.dumps(report(), ensure_ascii=False).encode())
            path.write_bytes(payload)
            args = [sys.executable, "-m", "src.source_evidence", str(path), "--name", "Muhamad Lumisa"]
            completed = subprocess.run(args, capture_output=True, text=True, check=True)
            result = json.loads(completed.stdout)
            self.assertEqual(result["report_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(result["sources"]["mof"]["status"], "FOUND")
            path.write_bytes(b"not a report")
            failed = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(failed.stdout, "")


if __name__ == "__main__":
    unittest.main()
