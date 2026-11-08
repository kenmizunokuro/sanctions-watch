"""監査で止まったOFAC差分をレビュー専用CSVへ分離する。"""
from __future__ import annotations

import csv
import argparse
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

PATH = Path("data/dashboard/ofac_pending.csv")
DIFF_PATH = Path("data/diff/latest.csv")
DIFF_HEADER = ["出所", "種別", "受取人名", "変更前", "変更後"]
HEADER = ["検知日時（JST）", *DIFF_HEADER, "状態", "GitHub実行URL"]
STATE = "監査停止・master未反映"
KINDS = {"追加", "初回同期（Advanced XML）", "掲載終了", "掲載終了候補（要確認）", "変更"}


def build(body: bytes, digest: str, finished_at: str, run_url: str) -> list[list[str]]:
    if hashlib.sha256(body).hexdigest() != digest:
        raise ValueError("OFAC候補差分のSHA256が今回の取得処理の出力と一致しません")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+", run_url):
        raise ValueError("OFAC候補差分の実行URLが不正です")
    utc = datetime.strptime(finished_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    stamp = utc.astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M:%S")
    reader = csv.reader(io.StringIO(body.decode("utf-8"), newline=""), strict=True)
    if next(reader, None) != DIFF_HEADER:
        raise ValueError("OFAC候補差分のCSV列定義が不正です")
    output = [HEADER]
    for row in reader:
        if len(row) != len(DIFF_HEADER):
            raise ValueError("OFAC候補差分のCSV行構造が不正です")
        source, kind, name, before, after = row
        if source != "OFAC":
            continue
        if kind not in KINDS or not name.strip():
            raise ValueError("OFAC候補差分の種別または対象者名が不正です")
        if kind.startswith("掲載終了"):
            kind = "掲載終了候補"
        output.append([stamp, source, kind, name, before, after, STATE, run_url])
    return output


def write(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(rows)


def read(path: Path) -> list[list[str]]:
    if not path.exists():
        return [HEADER]
    with path.open(encoding='utf-8', newline='') as stream:
        return list(csv.reader(stream, strict=True))


def _indexed(rows: list[list[str]]) -> dict[tuple[str, ...], list[str]]:
    if not rows or rows[0] != HEADER:
        raise ValueError('OFAC候補差分のCSV列定義が不正です')
    result = {}
    for row in rows[1:]:
        if (len(row) != len(HEADER) or row[1] != 'OFAC' or not row[3].strip()
                or row[2] not in {'追加', '初回同期（Advanced XML）', '掲載終了候補', '変更'}
                or row[6] != STATE
                or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+', row[7])):
            raise ValueError('OFAC候補の初回検知証跡が不正です')
        if datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S') != row[0]:
            raise ValueError('OFAC候補の初回検知日時が不正です')
        key = tuple(row[1:6])
        if key in result:
            raise ValueError('OFAC候補差分の同一候補が重複しています')
        result[key] = row
    return result


def retain_first_detection(current: list[list[str]], previous: list[list[str]]) -> list[list[str]]:
    """Keep first observation of an unchanged, still-open diff and its evidence URL.

    Identity includes source, kind, name and exact before/after values. A cleared
    event cannot confer its timestamp on a later recurrence. Latest check times
    remain in the separate monitor receipt and integrity diagnostics.
    """
    _indexed(current)
    prior = _indexed(previous)
    output = [list(HEADER)]
    for row in current[1:]:
        saved = prior.get(tuple(row[1:6]))
        item = list(row)
        if saved:
            if saved[0] > row[0]:
                raise ValueError('OFAC候補の初回検知日時が今回の取得より未来です')
            item[0], item[7] = saved[0], saved[7]
        output.append(item)
    return output


if __name__ == "__main__":
    # The workflow runs this only after the OFAC publication gate passes.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", required=True)
    parser.parse_args()
    write(PATH, [HEADER])
