---
name: timetree-to-gcal
description: TimeTreeの共有カレンダーの予定をGoogleカレンダーに同じ内容で登録(同期)する。「TimeTreeの予定をGoogleカレンダーに入れて」「TimeTreeと同期」などの依頼で使う。
---

# TimeTree → Googleカレンダー 反映スキル

TimeTreeカレンダーの予定を読み取り、Googleカレンダーに同じ予定として登録する。

既定のカレンダーURL: https://timetreeapp.com/calendars/pwKoWTfpBTjX
(ユーザーが別URLを指定した場合はそちらを使う)

## 手順

1. **予定の取得**(上から順に試す)
   1. WebFetch で共有URLを取得し、日付・時刻・タイトル・場所・メモを抽出する。
   2. `EGRESS_BLOCKED` などで取得できない場合は、プロキシ回避を試みず、ユーザーに次のいずれかを依頼する:
      - TimeTreeの「カレンダーのエクスポート」で出力した `.ics` ファイル
      - 予定の一覧テキストやスクリーンショット
2. **正規化** — 各予定を次の形にそろえる:
   タイトル / 開始 / 終了 / 終日か / 場所 / メモ(URL含む) / 繰り返し(RRULE)
   - 時刻不明で日付のみなら終日(`allDay: true`)。
   - 終了未指定の時刻付き予定は1時間とする。
   - タイムゾーンは指定がなければ `Asia/Tokyo`。
3. **重複チェック** — `mcp__Google_Calendar__list_events` で対象期間を取得し、
   同じタイトル+同じ開始日時の予定が既にあればスキップする(`fullText` も併用可)。
4. **登録前の確認** — 登録予定の一覧(件数・期間)をユーザーに見せ、件数が多い場合(目安10件超)は確認を取ってから実行する。
   登録先は `list_calendars` で確認し、指定がなければプライマリ。
5. **登録** — `mcp__Google_Calendar__create_event` で1件ずつ作成する。
   - `summary`=タイトル、`location`=場所、`description`=メモ + 末尾に `Source: TimeTree`
   - 参加者は追加しない・`notificationLevel: NONE` にして通知メールを出さない。
6. **報告** — 登録した件数、スキップ(重複)した件数、失敗した件数を一覧で報告する。

## 注意
- TimeTree側の予定は変更・削除しない(一方向の反映のみ)。
- 既存のGoogleカレンダーの予定は更新・削除しない。
- 再実行しても重複しないよう、必ず手順3を行う。
