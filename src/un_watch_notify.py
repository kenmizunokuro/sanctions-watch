"""Send the existing UN workflow notifications from validated observations."""
from __future__ import annotations

import os
from pathlib import Path

from . import un_watch as W
from .notify import post


def send(state, counts, *, failed):
    if failed:
        post('【国連 監視エラー】取得・解析・監視範囲を確認してください。直前の有効snapshotとmasterは保持。')
    if counts['new_notices']:
        post('【国連 更新発表検知】新着・改訂発表 ' + str(counts['new_notices'])
             + '件。XML検証・名簿反映とは別に要確認。data/dashboard/un_reviews.csv を参照。')
    if counts['new_updates']:
        post('【国連 公式更新履歴検知】追加・指定解除・情報改訂の新規候補 ' + str(counts['new_updates'])
             + '件。恒久参照番号を公式XMLと照合してください。'
             + ' 観測・レビューのみ、master未反映。data/dashboard/un_reviews.csv を参照。')
    if counts['new_xml_events']:
        event = state['events'][-1]
        kind = '初回収録' if event['baseline'] else '差分候補'
        post('【国連 XML検証済・' + kind + '】追加 ' + str(len(event['added']))
             + ' / 掲載終了候補 ' + str(len(event['removed']))
             + ' / 変更 ' + str(len(event['changed']))
             + '\nSHA256: ' + event['source_hash'] + '\n状態: 要レビュー・master未反映')
    if counts['new_press_rss']:
        post('【国連 公式RSS補助検知】新着・改訂候補 ' + str(counts['new_press_rss'])
             + '件。直近記事のみ・委員会の全件網羅を保証しません。'
             + ' 一次発表・XML要照合、master未反映。data/dashboard/un_reviews.csv を参照。')


def main():
    env = ('new_xml_events', 'new_notices', 'new_updates', 'new_press_rss')
    keys = ('NEW_XML', 'NEW_NOTICES', 'NEW_UPDATES', 'NEW_PRESS_RSS')
    counts = {name: int(os.environ.get(key) or 0) for name, key in zip(env, keys)}
    send(W.load(Path.cwd()), counts, failed=os.environ['MONITOR_OUTCOME'] != 'success')


if __name__ == '__main__':
    main()
