"""Frozen expectations for name screening using synthetic sanctions data."""

import json
import unittest
from pathlib import Path

from src import master, screening
from src.fetch import Fetched
from src.normalize import SRC_METI
from src.sources import mof, ofac


FIXTURES = Path(__file__).with_name("fixtures")
INPUT_FORMS = {
    "literal": "{}",
    "ascii_whitespace": " \t{}\n ",
    "unicode_whitespace": "\u3000{}\u00a0",
    "byte_order_mark": "\ufeff{}",
    "zero_width_space": "\u200b{}\u200b",
    "straight_double_quotes": '"{}"',
    "straight_single_quotes": "'{}'",
    "curly_quotes": "“{}”",
    "japanese_quotes": "「{}」",
    "fullwidth_quotes": "＂{}＂",
}


def source_records(names, category):
    return [{"name": name, "category": category} for name in names]


class GoldenScreeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        corpus = json.loads((FIXTURES / "screening_golden.json").read_text(encoding="utf-8"))
        cls.cases = list(corpus["cases"])
        mof_names = list(corpus["additional_mof_names"])
        for category, pairs in corpus["name_pairs"].items():
            for name, near_miss in pairs:
                mof_names.append(name)
                cls.cases.append((f"{category}:{name}:hit", name, "HIT", "EXACT", [name]))
                cls.cases.append((f"{category}:{name}:miss", near_miss, "NO_HIT", "NONE", []))

        payload = (FIXTURES / "screening_golden_ofac.xml").read_bytes()
        advanced = Fetched(url="https://example.invalid/ofac.xml", body=payload)
        cls.parsed_ofac, _ = ofac.parse_advanced(advanced, "SDN")
        cls.screened_ofac = ofac.screening_records(cls.parsed_ofac)
        cls.rows = {}
        master.merge(cls.rows, cls.screened_ofac, ofac.SOURCE, ts=1, delist=False)

        removed_names = ["OLD NORTH LIGHT", "SHARED NORTH LIGHT"]
        master.merge(cls.rows, source_records(mof_names + removed_names, "fixture"), mof.SOURCE, ts=2)
        before_mof_removal = screening.build_screening_index(cls.rows.values())
        cls.pre_removal_decisions = {
            name: screening.screen_name(name, before_mof_removal)["decision"]
            for name in removed_names
        }
        master.merge(
            cls.rows,
            source_records(["SHARED NORTH LIGHT"], "fixture"),
            SRC_METI,
            ts=3,
        )
        master.merge(cls.rows, source_records(mof_names, "fixture"), mof.SOURCE, ts=4)
        cls.index = screening.build_screening_index(cls.rows.values())

    def test_corpus_has_unique_cases_and_minimum_coverage(self):
        self.assertEqual(len(self.cases), len({case[0] for case in self.cases}))
        self.assertEqual(len(self.cases), len({case[1] for case in self.cases}))
        self.assertGreaterEqual(len(self.cases) * len(INPUT_FORMS), 1000)

    def test_golden_decisions_and_candidates(self):
        for case_id, query, decision, match_type, names in self.cases:
            expected = (decision, match_type, sorted(names))
            for form, template in INPUT_FORMS.items():
                with self.subTest(case=case_id, form=form):
                    result = screening.screen_name(template.format(query), self.index)
                    actual = (
                        result["decision"],
                        result["match_type"],
                        sorted(row["display_name"] for row in result["candidates"]),
                    )
                    self.assertEqual(actual, expected)

    def test_xml_screening_retains_only_primary_and_strong_names_per_party(self):
        weak = {
            (record["source_id"], record["name"])
            for record in self.parsed_ofac
            if record["low_quality"]
        }
        self.assertEqual(weak, {("800001", "CEDAR SHORT"), ("800001", "CEDAR SHARED")})
        self.assertCountEqual(
            [(record["source_id"], record["name"]) for record in self.screened_ofac],
            [
                ("800001", "GOLDEN CEDAR WORKS"),
                ("800001", "CEDAR ORBIT"),
                ("800001", "أرز المدار"),
                ("800002", "CEDAR SHARED"),
                ("800003", "CEDAR SHARED"),
            ],
        )

    def test_previously_screenable_name_is_retained_as_inactive_after_delist(self):
        self.assertEqual(
            self.pre_removal_decisions,
            {"OLD NORTH LIGHT": "HIT", "SHARED NORTH LIGHT": "HIT"},
        )
        self.assertEqual(self.rows["oldnorthlight"]["status"], "無効")
        self.assertEqual(self.rows["sharednorthlight"]["sources"], "経産省")


if __name__ == "__main__":
    unittest.main()
