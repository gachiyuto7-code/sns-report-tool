---
name: timetree-to-gcal
description: TimeTreeのカレンダー（既定 https://timetreeapp.com/calendars/pwKoWTfpBTjX ）の予定をGoogleカレンダーに反映（追加・更新・削除）する。「TimeTreeの予定をGoogleカレンダーに同期して」「TimeTree反映して」「/timetree-to-gcal」などで使う。
---

# TimeTree → Google カレンダー同期

TimeTree の予定を Google カレンダーへ一方向で同期する。何度実行しても重複しない（冪等）。

## 設定値

| 項目 | 既定値 | 備考 |
|---|---|---|
| TimeTree カレンダーコード | `pwKoWTfpBTjX` | URL `timetreeapp.com/calendars/<コード>` の末尾 |
| 同期先 Google カレンダー | `gachiyuto7@gmail.com`（メイン） | ユーザーが別カレンダーを指定したらそれを使う（`list_calendars` で ID を確認） |
| タイムゾーン | `Asia/Tokyo` | |
| 同期期間 | 過去 7 日〜未来 90 日 | ユーザー指定があれば `--past-days` / `--future-days` で変更 |

ユーザーが引数で別のコードや期間を指定した場合はそちらを優先する。

## 前提

- ネットワークで `timetreeapp.com` に接続できること。クラウド環境でブロックされる場合は、環境設定の Network access で `timetreeapp.com` を許可ドメインに追加してもらう。
- （後述のブラウザ経由で取得する場合は次の項目は不要）TimeTree のログイン情報が環境変数 `TIMETREE_EMAIL` / `TIMETREE_PASSWORD` に設定されていること（非公開カレンダーのため）。パスワードをチャットに書かせたり、ファイルやコミットに残したりしない。未設定なら環境の Secrets / 環境変数に登録するよう案内して止まる。
- Google Calendar コネクタ（`mcp__Google_Calendar__*`）が使えること。ツールが未ロードなら ToolSearch で `select:mcp__Google_Calendar__list_events,mcp__Google_Calendar__create_event,mcp__Google_Calendar__update_event,mcp__Google_Calendar__delete_event` を読み込む。

## 手順

作業ファイルはスクラッチパッド（なければ `/tmp/timetree-sync/`）に置く。以下 `$W` はその作業ディレクトリ、`$SKILL` はこの SKILL.md のあるディレクトリ。

### 1. TimeTree から .ics を取得

```bash
pip install -q timetree-exporter
timetree-exporter -c pwKoWTfpBTjX -o "$W/timetree.ics"
```

- メール・パスワードは環境変数から自動で読まれる。
- 「No calendars found with the specified codes」と出て一覧選択の入力待ちになった場合は、そのアカウントにこのカレンダーが無い。一覧をユーザーに見せてどれを使うか確認する。
- 公開カレンダー（`timetreeapp.com/public_calendars/...`）の場合はログイン不要: `timetree-exporter --public-calendar -c <公開ID> -o "$W/timetree.ics"`

**ブラウザ経由（Claude in Chrome）**: ブラウザ操作ツール（`mcp__claude-in-chrome__*` など。ローカルの Claude Code を `claude --chrome` で起動すると使える）があり、ユーザーがその Chrome で TimeTree にログイン済みなら、ログイン情報の環境変数は不要。
1. `https://timetreeapp.com/calendars/pwKoWTfpBTjX` を開き、同期期間に含まれる月を順に表示する。
2. 各予定をクリックして詳細（タイトル・日付・開始/終了時刻・終日か・場所・メモ）を読み取る。
3. 手順 2 の出力と同じ形式の JSON を `$W/timetree.json` に書く（`window` は期間、`events[]` は `key` / `summary` / `start` / `end` / `all_day` / `location` / `description` / `hash`）。`key` は `web_<開始日>_<タイトル>`、`hash` は `summary`・`start`・`end`・`location`・`description` を連結した文字列の SHA-1 先頭 12 桁にする。
4. 手順 2 を飛ばして手順 3 へ進む。
この方法では予定の時刻やタイトルが変わると「削除＋新規作成」扱いになる。

**フォールバック**: TimeTree に接続できない・ログインできない場合は、ユーザーに次のいずれかを頼む。
- 手元の PC で上記コマンドを実行して得た `timetree.ics` をアップロードしてもらう（以降の手順はそのまま使える）
- 予定のスクリーンショットやテキストを貼ってもらう → 内容を読み取り、手順 2 の JSON 形式（`events` 配列、`key` は `manual_<日付>_<タイトル>` など一意な値、`hash` は空でない任意の文字列）を手で作って続行する

### 2. 期間内の予定を正規化

```bash
python3 "$SKILL/scripts/tt_sync.py" export "$W/timetree.ics" --tz Asia/Tokyo --past-days 7 --future-days 90 > "$W/timetree.json"
```

繰り返し予定は期間内の各回に展開され、除外日・個別変更も反映される。出力の `window.start` / `window.end` を次の手順で使う。

### 3. Google カレンダーの既存予定を取得

`mcp__Google_Calendar__list_events` を同期先カレンダーに対して `startTime=window.start`、`endTime=window.end`、`pageSize=250` で呼ぶ。`nextPageToken` があれば全ページ取得する。

各ページの結果（ツールが返した JSON そのまま）を `$W/gcal_1.json`, `$W/gcal_2.json` … に保存する。

### 4. 差分を計算

```bash
python3 "$SKILL/scripts/tt_sync.py" plan "$W/timetree.json" "$W"/gcal_*.json > "$W/plan.json"
```

同期済みの予定は Google 側の説明欄末尾にある `[timetree-sync key=... hash=...]` マーカーで識別される。マーカーの無い予定（ユーザーが Google で直接作った予定）には一切触れない。

`plan.json` の `summary` をユーザーに提示する（追加 / 更新 / 削除 / 変更なし の件数と、追加・削除の主な予定名）。

### 5. Google カレンダーに反映

- **create**: `mcp__Google_Calendar__create_event`
  - `summary` ← `summary`、`location` ← `location`（空なら省略）、`description` ← `gcal_description`（マーカー入り。必ずこれを使う）
  - 時間指定: `startTime` / `endTime` ← `start` / `end`、`timeZone: "Asia/Tokyo"`
  - 終日（`all_day: true`）: `allDay: true`、`startTime` ← `<start>T00:00:00+09:00`、`endTime` ← `<end>T00:00:00+09:00`（end は翌日＝排他的終了日）
  - 通知メールを出さないよう `notificationLevel: "NONE"`
- **update**: `mcp__Google_Calendar__update_event` で `event_id` の予定を同じ項目で上書きする（`description` は `gcal_description`）。
- **delete**: TimeTree 側で消えた予定。削除は取り消せないので、**件数と予定名を一覧で示してユーザーの確認を取ってから** `mcp__Google_Calendar__delete_event` を実行する。定期実行などユーザー不在の場合は削除せず、報告だけする。

件数が多い場合も 1 件ずつ確実に実行し、失敗したものは記録しておく。

### 6. 報告

追加・更新・削除の件数、失敗があればその予定名と理由を短く報告する。何も変わらなかった場合は「差分なし」とだけ伝える。

## 注意

- 同期は TimeTree → Google の一方向。Google 側で同期済み予定を編集しても、次回の同期で TimeTree の内容に戻る。
- マーカー行を消された予定は同期対象外になり、次回に同じ予定が新規作成される。
- timetree-exporter は TimeTree の非公式 Web API を使うため、TimeTree の仕様変更で動かなくなることがある。その場合はフォールバック手順を使う。
