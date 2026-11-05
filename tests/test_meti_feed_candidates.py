from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src import meti_feed_candidates, meti_manual_event


ROOT = Path(__file__).resolve().parent.parent
CHANNEL_URL = "https://www.meti.go.jp/rss/rss_ja.html"
NOTICE = "https://www.meti.go.jp/press/2025/09/20250929006/20250929006.html"


def entry(title, url, summary="", published=""):
    return f"""
    <entry>
      <title>{title}</title>
      <link rel="alternate" href="{url}"/>
      <summary>{summary}</summary>
      <updated>2025-09-30T01:00:00Z</updated>
      {published}
    </entry>"""


def feed(*entries):
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<title>経済産業省 | 新着情報</title>'
        f'<link rel="alternate" href="{CHANNEL_URL}"/>'
        + "".join(entries)
        + "</feed>"
    ).encode()


class TestMetiFeedCandidates(unittest.TestCase):
    def run_cli(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "release.xml"
            source.write_bytes(payload)
            return subprocess.run(
                [sys.executable, "-m", "src.meti_feed_candidates", str(source)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_candidates_keep_evidence_and_deduplicate_notice_urls(self):
        headline = "「外国ユーザーリスト」を改正しました"
        primary = entry(
            headline, NOTICE, published="<published>2025-09-29T01:00:00Z</published>"
        )
        related_url = "https://www.meti.go.jp/policy/anpo/news.html"
        unrelated = entry("別の制度を改正", "https://www.meti.go.jp/press/other.html")
        payload = feed(
            primary,
            primary,
            unrelated,
            entry("新着情報", related_url, "安全保障貿易管理に関する資料を更新"),
        )
        result = self.run_cli(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "CANDIDATES_FOUND")
        self.assertEqual(report["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(report["entries_checked"], 4)
        self.assertEqual(report["latest_entry_updated_at"], "2025-09-30T01:00:00Z")
        self.assertTrue(report["stale"])
        self.assertEqual(
            {candidate["notice_url"]: candidate["matched_on"] for candidate in report["candidates"]},
            {NOTICE: ["title"], related_url: ["trade_control_update"]},
        )
        candidate = next(row for row in report["candidates"] if row["notice_url"] == NOTICE)
        self.assertEqual(candidate["title"], headline)
        self.assertEqual(candidate["published_at"], "2025-09-29T01:00:00Z")
        self.assertEqual(candidate["updated_at"], "2025-09-30T01:00:00Z")

    def test_title_summary_and_url_are_independent_signals(self):
        url_only = "https://www.meti.go.jp/policy/anpo/foreign_user_list.html"
        summary_only = "https://www.meti.go.jp/press/summary.html"
        now = datetime.now(timezone.utc).isoformat().encode()
        payload = feed(
            entry("お知らせ", url_only),
            entry("報道発表", summary_only, "外国ユーザーリストを改正しました"),
            entry("安全保障貿易管理に関する制度を改正", NOTICE),
        ).replace(b"2025-09-30T01:00:00Z", now)
        result = self.run_cli(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["stale"])
        self.assertEqual(
            {candidate["notice_url"]: candidate["matched_on"] for candidate in report["candidates"]},
            {url_only: ["url"], summary_only: ["summary"]},
        )
        self.assertEqual(report["candidates"][0]["published_at"], "")

    def test_valid_feed_without_match_only_reports_scan_result(self):
        result = self.run_cli(feed(entry("定例資料", NOTICE)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "NO_CANDIDATES_IN_INPUT")

    def test_bad_or_empty_feed_is_an_error(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().encode()
        valid = feed(entry("外国ユーザーリスト", NOTICE))
        invalid = (
            b"",
            b"<html/>",
            b'<?xml version="1.0" encoding="unknown"?>' + valid,
            valid.replace(b"2025-09-30T01:00:00Z", b"9999-12-31T23:59:59-02:00"),
            feed(),
            valid.replace(CHANNEL_URL.encode(), b"https://www.meti.go.jp/rss/other.html"),
            valid.replace(b"2025-09-30T01:00:00Z", future),
            feed(entry("外国ユーザーリスト", "https://example.com/notice")),
            feed(entry("外国ユーザーリスト", NOTICE)).replace(b"01:00:00Z", b"invalid"),
            ('<!DOCTYPE feed [<!ENTITY injected "test">]>' + feed(entry("&injected;", NOTICE)).decode()).encode("utf-16"),
            b"x" * (2 * 1024 * 1024 + 1),
        )
        for index, payload in enumerate(invalid):
            with self.subTest(case=index):
                result = self.run_cli(payload)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("入力エラー", result.stderr)
                self.assertEqual(result.stdout, "")


class TestMetiFeedRegistration(unittest.TestCase):
    def register(self, directory, payload, url=NOTICE):
        source = directory / "release.xml"
        source.write_bytes(payload)
        stdout, stderr = StringIO(), StringIO()
        with patch.object(meti_manual_event, "ROOT", directory), redirect_stdout(stdout), redirect_stderr(stderr):
            code = meti_feed_candidates.main([
                str(source), "--register-notice-url", url, "--operator", "operator-a",
            ])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_selected_candidate_opens_one_detection_with_rss_evidence(self):
        now = datetime.now(timezone.utc).isoformat()
        publication = f"<published>{now}</published>"
        payload = feed(entry("-- 外国ユーザーリストの改正", NOTICE, published=publication))
        payload = payload.replace(b"2025-09-30T01:00:00Z", now.encode())
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = self.register(directory, payload)
            self.assertEqual(first[0], 0, first[2])
            self.assertEqual(self.register(directory, payload), first)
            state = json.loads((directory / "data/manual/meti/state.json").read_text())
            self.assertEqual(state["lifecycle_state"], "MANUAL_FETCH_REQUIRED")
            pending = state["pending_detection"]
            self.assertEqual(pending["notice_url"], NOTICE)
            self.assertEqual(pending["title"], "-- 外国ユーザーリストの改正")
            self.assertTrue(pending["publication_at"])
            evidence = json.loads(pending["note"])["rss_evidence"]
            self.assertEqual(evidence["sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(evidence["matched_on"], ["title"])
            self.assertFalse((directory / "data/master/master.csv").exists())

    def test_stale_or_unselected_candidate_does_not_open_detection(self):
        old = entry("外国ユーザーリストの改正", NOTICE)
        other_url = "https://www.meti.go.jp/press/other.html"
        now = datetime.now(timezone.utc).isoformat()
        recent = entry("別の発表", other_url).replace("2025-09-30T01:00:00Z", now)
        selected = old.replace("2025-09-30T01:00:00Z", now)
        cases = ((feed(old), NOTICE), (feed(old, recent), NOTICE), (feed(selected), other_url))
        for payload, url in cases:
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                code, stdout, stderr = self.register(directory, payload, url)
                self.assertEqual(code, 2, stderr)
                self.assertIn("入力エラー", stderr)
                self.assertEqual(stdout, "")
                self.assertFalse((directory / "data").exists())
