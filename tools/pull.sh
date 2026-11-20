#!/bin/bash
# 制裁リストマスターを GitHub Releases から取得する。
# Public リポジトリのリリース資産は認証不要なのでトークンは要らない。
# 内容が変わったときだけ差し替え、macOS の通知を出す。
set -euo pipefail

REPO="${REPO:-kenmizunokuro/sanctions-watch}"
DEST="${DEST:-$HOME/Desktop/files/sanctions-master}"
FILES="${FILES:-black_receiver_name_all.xlsx latest.csv}"
LOG="$DEST/pull.log"

mkdir -p "$DEST"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
notify() {
  [ -x /usr/bin/osascript ] || return 0
  /usr/bin/osascript - "$1" "$2" <<'APPLESCRIPT' 2>/dev/null || true
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
APPLESCRIPT
}
sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

changed=0
failed=0

for name in $FILES; do
  url="https://github.com/$REPO/releases/latest/download/$name"
  tmp="$(mktemp "$DEST/.pull.XXXXXX")" || { log "mktemp 失敗"; exit 1; }

  if ! curl -fsSL --retry 3 --retry-delay 5 --max-time 300 -o "$tmp" "$url"; then
    log "取得失敗: $name"; rm -f "$tmp"; failed=1; continue
  fi
  # 空ファイルを掴んで既存を壊さない
  if [ ! -s "$tmp" ]; then
    log "空のファイルを受信したため破棄: $name"; rm -f "$tmp"; failed=1; continue
  fi

  new="$(sha256_file "$tmp")"
  old=""
  [ -f "$DEST/$name" ] && old="$(sha256_file "$DEST/$name")"

  if [ "$new" = "$old" ]; then
    log "変更なし: $name"; rm -f "$tmp"; continue
  fi

  # 直前の版を1つ残す。取り込み後に問題が出たら戻せる。
  [ -f "$DEST/$name" ] && cp -p "$DEST/$name" "$DEST/.$name.prev"
  chmod 0644 "$tmp"
  mv "$tmp" "$DEST/$name"
  log "更新: $name ($new)"
  changed=1
done

[ "$changed" = 1 ] && notify "制裁リスト更新" "マスターが更新されました。$DEST を確認してください"
[ "$failed" = 1 ] && notify "制裁リスト取得エラー" "取得に失敗しました。$LOG を確認してください"

# ログが際限なく伸びないよう直近1000行に切り詰める
[ -f "$LOG" ] && { tail -n 1000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }
exit "$failed"
