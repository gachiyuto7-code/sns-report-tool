#!/usr/bin/env python3
"""TimeTree (.ics) -> Google Calendar 同期ヘルパー。

サブコマンド:
  export  : timetree-exporter が出力した .ics を読み、期間内の予定を
            (繰り返しを展開した上で) 正規化 JSON にする。
  plan    : export の JSON と、Google カレンダーから取得した既存予定の JSON を
            突き合わせ、create / update / delete の差分を出力する。

同期済みの予定は Google 側の説明欄末尾に
    [timetree-sync key=<キー> hash=<内容ハッシュ>]
というマーカーを持つ。キーは単発予定なら TimeTree の UID、繰り返し予定なら UID + 各回の元の開始日時。
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from icalendar import Calendar

MARKER_RE = re.compile(r"\[timetree-sync key=(?P<key>\S+) hash=(?P<hash>[0-9a-f]+)\]")


def _to_local(value, tz):
    """date / naive datetime / aware datetime を tz の aware datetime に揃える。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    return datetime.combine(value, time.min, tzinfo=tz)


def _norm(value, all_day, tz):
    if all_day:
        return value.isoformat() if not isinstance(value, datetime) else value.date().isoformat()
    return _to_local(value, tz).isoformat()


def _prop_dt(component, name):
    prop = component.get(name)
    return prop.dt if prop is not None else None


def _exdates(component):
    result = set()
    ex = component.get("EXDATE")
    if ex is None:
        return result
    for item in ex if isinstance(ex, list) else [ex]:
        for d in item.dts:
            result.add(d.dt)
    return result


def _occurrences(vevent, window_start, window_end, tz):
    """(開始, 終了) のリストを返す。繰り返しは期間内で展開する。"""
    start = _prop_dt(vevent, "DTSTART")
    end = _prop_dt(vevent, "DTEND")
    all_day = not isinstance(start, datetime)
    if end is None:
        end = start + (timedelta(days=1) if all_day else timedelta(0))
    duration = end - start

    rrule = vevent.get("RRULE")
    if rrule is None:
        return [(start, end)]

    rule_text = rrule.to_ical().decode()
    if all_day:
        # 終日の繰り返しは日付ベースで展開する
        base = datetime.combine(start, time.min)
        rule_text = re.sub(r"UNTIL=(\d{8})T\d{6}Z?", r"UNTIL=\1T235959", rule_text)
        lo = datetime.combine(window_start.date(), time.min) - duration
        hi = datetime.combine(window_end.date(), time.min)
    else:
        base = _to_local(start, tz)
        lo = window_start - duration
        hi = window_end
    rule = rrulestr(rule_text, dtstart=base)
    excluded = {(_to_local(d, tz) if not all_day else (d if not isinstance(d, datetime) else d.date()))
                for d in _exdates(vevent)}

    result = []
    for occ in rule.between(lo, hi, inc=True):
        occ_start = occ.date() if all_day else occ
        if occ_start in excluded:
            continue
        result.append((occ_start, occ_start + duration))
    return result


def _hash(event):
    payload = json.dumps(
        {k: event[k] for k in ("summary", "start", "end", "all_day", "location", "description")},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


def _in_window(start_iso, end_iso, all_day, window_start, window_end, tz):
    if all_day:
        s = datetime.combine(date.fromisoformat(start_iso), time.min, tzinfo=tz)
        e = datetime.combine(date.fromisoformat(end_iso), time.min, tzinfo=tz)
    else:
        s = datetime.fromisoformat(start_iso)
        e = datetime.fromisoformat(end_iso)
    return s < window_end and e > window_start


def cmd_export(args):
    tz = ZoneInfo(args.tz)
    today = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = today - timedelta(days=args.past_days)
    window_end = today + timedelta(days=args.future_days)

    with open(args.ics, "rb") as f:
        cal = Calendar.from_ical(f.read())

    masters, overrides = [], {}
    for vevent in cal.walk("VEVENT"):
        if vevent.get("RECURRENCE-ID") is not None:
            overrides.setdefault(str(vevent.get("UID")), []).append(vevent)
        else:
            masters.append(vevent)

    events = {}

    def add(vevent, key, start, end):
        all_day = not isinstance(start, datetime)
        start_iso = _norm(start, all_day, tz)
        end_iso = _norm(end, all_day, tz)
        if not _in_window(start_iso, end_iso, all_day, window_start, window_end, tz):
            return
        event = {
            "key": key,
            "summary": str(vevent.get("SUMMARY", "")).strip() or "(無題)",
            "start": start_iso,
            "end": end_iso,
            "all_day": all_day,
            "location": str(vevent.get("LOCATION", "")).strip(),
            "description": str(vevent.get("DESCRIPTION", "")).strip(),
        }
        event["hash"] = _hash(event)
        events[event["key"]] = event

    def occ_key(uid, value):
        # 繰り返しの各回は「元の」開始日時で識別する (時間変更されてもキーは不変)
        iso = _norm(value, not isinstance(value, datetime), tz)
        return f"{uid}_{iso.replace(':', '').replace('-', '')[:15]}"

    for vevent in masters:
        uid = str(vevent.get("UID"))
        recurring = vevent.get("RRULE") is not None
        overridden = {}
        for ov in overrides.get(uid, []):
            rid = ov.get("RECURRENCE-ID").dt
            overridden[rid if not isinstance(rid, datetime) else _to_local(rid, tz)] = ov
        for start, end in _occurrences(vevent, window_start, window_end, tz):
            lookup = start if not isinstance(start, datetime) else _to_local(start, tz)
            if lookup in overridden:
                continue  # 個別に変更された回は下で追加する
            add(vevent, occ_key(uid, start) if recurring else uid, start, end)

    for uid, ovs in overrides.items():
        for ov in ovs:
            start = _prop_dt(ov, "DTSTART")
            end = _prop_dt(ov, "DTEND") or start
            add(ov, occ_key(uid, ov.get("RECURRENCE-ID").dt), start, end)

    result = {
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "timezone": args.tz,
        "events": sorted(events.values(), key=lambda e: e["start"]),
    }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


def _gcal_items(data):
    if isinstance(data, dict):
        for key in ("events", "items"):
            if key in data:
                return data[key]
        return []
    return data


def cmd_plan(args):
    with open(args.timetree) as f:
        tt = json.load(f)
    gcal = []
    for path in args.gcal:
        with open(path) as f:
            gcal.extend(_gcal_items(json.load(f)))

    existing = {}
    for item in gcal:
        m = MARKER_RE.search(item.get("description") or "")
        if m:
            existing[m.group("key")] = {"event_id": item.get("id"), "hash": m.group("hash")}

    create, update, delete = [], [], []
    wanted = {e["key"]: e for e in tt["events"]}
    for key, event in wanted.items():
        if key not in existing:
            create.append(event)
        elif existing[key]["hash"] != event["hash"]:
            update.append({**event, "event_id": existing[key]["event_id"]})
    for key, info in existing.items():
        if key not in wanted:
            delete.append({"key": key, "event_id": info["event_id"]})

    for event in create + update:
        marker = f"[timetree-sync key={event['key']} hash={event['hash']}]"
        event["gcal_description"] = (event["description"] + "\n\n" if event["description"] else "") + marker

    json.dump(
        {
            "summary": {"create": len(create), "update": len(update), "delete": len(delete),
                        "unchanged": len(wanted) - len(create) - len(update)},
            "create": create,
            "update": update,
            "delete": delete,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("export", help=".ics を期間内の正規化 JSON に変換")
    p.add_argument("ics")
    p.add_argument("--tz", default="Asia/Tokyo")
    p.add_argument("--past-days", type=int, default=7)
    p.add_argument("--future-days", type=int, default=90)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("plan", help="Google カレンダー既存予定との差分を計算")
    p.add_argument("timetree", help="export の出力 JSON")
    p.add_argument("gcal", nargs="+", help="Google カレンダー list_events の結果 JSON (複数ページ可)")
    p.set_defaults(func=cmd_plan)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
