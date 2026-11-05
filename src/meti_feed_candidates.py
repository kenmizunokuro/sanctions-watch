"""Identify METI update candidates from a locally saved news Atom feed."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .meti_manual_event import validate_notice_url


FEED_URL = "https://www.meti.go.jp/ml_index_release_atom.xml"
CHANNEL_URL = "https://www.meti.go.jp/rss/rss_ja.html"
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_FEED_AGE = timedelta(days=7)
MAX_CLOCK_SKEW = timedelta(minutes=5)
LIST_NAME = "外国ユーザーリスト"
UPDATE_WORDS = ("改正", "更新", "改訂", "見直し")


class FeedTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("Atom XMLにDTDは指定できません")


def _text(element, tag):
    child = element.find(ATOM + tag)
    return "".join(child.itertext()).strip() if child is not None else ""


def _timestamp(entry, tag, *, required=False):
    value = _text(entry, tag)
    if not value and not required:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError(f"Atom記事の{tag}が欠落しているか不正です") from None


def _notice_url(entry):
    links = [
        link.get("href", "")
        for link in entry.findall(ATOM + "link")
        if link.get("rel", "alternate") == "alternate"
        and link.get("type", "text/html") in {"text/html", "application/xhtml+xml"}
    ]
    if len(links) != 1 or any(
        char.isspace() or ord(char) < 32 or char == "\\" for char in links[0]
    ):
        raise ValueError("Atom記事の通知リンクを一意に特定できません")
    url = validate_notice_url(links[0])
    if urlsplit(url).path in {"", "/"}:
        raise ValueError("Atom記事の通知URLに記事パスがありません")
    return url


def _iso_utc(timestamp):
    return timestamp.isoformat().replace("+00:00", "Z")


def _candidate(entry, updated_at):
    title = _text(entry, "title")
    if not title:
        raise ValueError("Atom記事のタイトルがありません")
    url = _notice_url(entry)
    published_at = _timestamp(entry, "published")
    headline = unicodedata.normalize("NFKC", title)
    summary = unicodedata.normalize("NFKC", _text(entry, "summary"))
    path = unquote(urlsplit(url).path).lower().replace("_", "-")

    matched_on = []
    if LIST_NAME in headline:
        matched_on.append("title")
    if LIST_NAME in summary:
        matched_on.append("summary")
    if "foreign-user-list" in path or LIST_NAME in path:
        matched_on.append("url")
    text = f"{headline} {summary}"
    if not matched_on and (
        path.startswith("/policy/anpo/")
        and "安全保障貿易管理" in text
        and any(word in text for word in UPDATE_WORDS)
    ):
        matched_on.append("trade_control_update")

    if not matched_on:
        return None
    return {
        "title": title,
        "notice_url": url,
        "published_at": _iso_utc(published_at) if published_at else "",
        "updated_at": _iso_utc(updated_at),
        "matched_on": matched_on,
    }


def scan_feed(path: Path) -> dict:
    checked_at = datetime.now(timezone.utc)
    with path.open("rb") as stream:
        payload = stream.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("Atom XMLが2 MiBの上限を超えています")
    try:
        root = ET.fromstring(payload, parser=ET.XMLParser(target=FeedTreeBuilder()))
    except LookupError as exc:
        raise ValueError("Atom XMLの文字コードを読み取れません") from exc
    if root.tag != ATOM + "feed":
        raise ValueError("経産省ニュースリリースのAtom XMLが必要です")
    channel_links = {
        (link.get("rel", "alternate"), link.get("href"))
        for link in root.findall(ATOM + "link")
    }
    if not channel_links.intersection({("self", FEED_URL), ("alternate", CHANNEL_URL)}):
        raise ValueError("経産省ニュースリリース配信の識別リンクがありません")
    entries = root.findall(ATOM + "entry")
    if not entries:
        raise ValueError("Atom XMLに記事がありません")

    candidates = {}
    updates = []
    for entry in entries:
        updated_at = _timestamp(entry, "updated", required=True)
        updates.append(updated_at)
        candidate = _candidate(entry, updated_at)
        if candidate:
            candidates.setdefault(candidate["notice_url"], candidate)
    latest_update = max(updates)
    if latest_update - checked_at > MAX_CLOCK_SKEW:
        raise ValueError("Atom記事の更新日時が現在より5分を超えて未来です")
    return {
        "status": "CANDIDATES_FOUND" if candidates else "NO_CANDIDATES_IN_INPUT",
        "expected_feed_url": FEED_URL,
        "checked_at": _iso_utc(checked_at),
        "latest_entry_updated_at": _iso_utc(latest_update),
        "stale": checked_at - latest_update > MAX_FEED_AGE,
        "input_file": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "entries_checked": len(entries),
        "candidates": list(candidates.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="保存済みの経産省ニュースリリースAtom XMLから更新候補を抽出します"
    )
    parser.add_argument("feed", type=Path, help="保存したAtom XMLのパス")
    args = parser.parse_args()
    try:
        report = scan_feed(args.feed)
    except (OSError, ET.ParseError, ValueError) as exc:
        print(f"入力エラー: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
