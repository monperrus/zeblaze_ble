# Open Zeblaze BLE

Python package for the Zeblaze watches, developed from a Beyond 3 Pro. 

Main features: `battery`, `fitness`, `daily`, `sleep`, `realtime`, `heartrate`, `notify`, and `workout`

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```
## Usage

### Scan and inspect

```bash
zeblaze-ble scan
zeblaze-ble inspect D6:45:15:30:04:71
```

The address in the second command is only an example. BLE privacy addresses can
change, so use the address reported by `scan` **when the watch is advertising**.
An already-connected watch may not appear in a scan; that is expected and does
not mean it is unavailable. In that case, use its known address directly. Close
or force-stop Zeblaze Fit on a phone before running a command that connects to the watch,
because most watches allow only one BLE central at a time.

On Linux, `inspect` automatically uses BlueZ's `gatttool` because it can
reliably enumerate this watch even when the calling profile shares its address.

### Passive capture

```bash
zeblaze-ble listen <ADDRESS> --seconds 90 --output capture.jsonl
```

A passive capture may remain empty until a verified, read-only history request
is implemented; no command bytes are guessed here.

### Current heart rate

```bash
zeblaze-ble heartrate <ADDRESS> --i-understand-this-writes
```

This is the quickest path for a known watch: it connects directly to the
address, enables the verified live-data stream, and prints one value such as
`{"heart_rate_bpm": 62}`. It does not scan first, ignores the stream-enable
acknowledgment, retries transient connection failures up to three times, and
releases this computer's stale BlueZ connection before and after the request.
Use `--seconds` or `--attempts` to adjust the 30-second per-attempt wait and
retry count.

Keep the watch awake and firmly on your wrist. If it is already known to this
computer, use its known address directly; a connected or sleeping watch may
not appear in a scan.

### Device info and battery

```bash
zeblaze-ble battery <ADDRESS> --i-understand-this-writes
```

Sends `GET_DEVICE_INFO` (command id 32) and prints firmware version, MAC,
serial number, and battery status. It is gated behind the explicit
`--i-understand-this-writes` flag, justified because the command and its
framing come from a verified live capture of the official app's own traffic
rather than a guess. Full protocol details (wire framing, command ids,
message layout): `zeblaze_ble/protocol.md`.

Live example (2026-08-29):

```json
{
  "firmware_version": "1.1.2",
  "equipment_number": "30108",
  "mac": "D6:45:15:30:04:71",
  "serial_number": "10005",
  "battery_capacity_percent": 66,
  "battery_charge_status": 2
}
```

On Linux this goes through `gatttool -I` rather than `bleak`/BlueZ D-Bus:
`bleak`'s service resolution has proven unreliable for this watch on this
stack. A `bleak`-based reference implementation of the same protocol also exists in `transport.py` (matches
the official app's wire bytes byte-for-byte against two independent live
captures) but isn't wired into the CLI because of reliability.

### Fitness data

```bash
zeblaze-ble fitness <ADDRESS> --i-understand-this-writes
zeblaze-ble realtime <ADDRESS> --seconds 20 --i-understand-this-writes
```

`fitness` enumerates and fetches every currently-available bucket the watch
has queued: steps/distance/calories, sleep, heart rate, activity duration
and effective standing. `realtime` enables the watch's real-time data push
and prints each `REPORT_BASIC_DATA` reading (steps, calories, distance,
heart rate, blood oxygen, effective standing, battery) as it arrives. Both
are gated behind `--i-understand-this-writes` for the same reason as
`battery`. Full protocol writeup, including exactly what fields mean and
what's still unverified (e.g. no GPS/location command has been found yet):
`zeblaze_ble/protocol.md`.

### Daily steps, distance and calories

```bash
zeblaze-ble daily <ADDRESS> --i-understand-this-writes
zeblaze-ble daily <ADDRESS> --buckets --i-understand-this-writes
```

Fetches the steps/distance/calories buckets, one entry per day the watch
still has. By default it prints the day totals; `--buckets` adds the
per-bucket arrays.

```json
[
  {
    "date": "2026-08-30",
    "total_steps": 124,
    "total_distance_metres": 100,
    "total_calories": 2,
    "bucket_minutes": 60
  }
]
```

Buckets run from local midnight at `bucket_minutes` each (60 in every
capture so far, so 24 hourly buckets). Distance is metres; the calorie unit
is unconfirmed. `DailyData` also exposes the raw bytes next to the decoded
lists, and `ContinuousHeartRate` (via `fitness`) decodes the same way at one
byte per bucket, every `frequency_minutes` — a zero there means no sample
was taken, not a measured zero.

### Sleep

```bash
zeblaze-ble sleep <ADDRESS> --i-understand-this-writes
```

Fetches only the sleep buckets — one per recorded night, listed under the
date the night started on. Each night gives the summary the watch itself
computes plus the full stage timeline.

Live example (night of 2026-08-30, abridged):

```json
[
  {
    "date": "2026-08-30",
    "data": {
      "start_timestamp": 1788125940,
      "end_timestamp": 1788149820,
      "duration_minutes": 398,
      "score": 79,
      "awake_time": 0,
      "light_sleep_time": 295,
      "light_sleep_time_percentage": 75,
      "deep_sleep_time": 103,
      "deep_sleep_time_percentage": 25,
      "rem_time": 0,
      "sleep_type": 1,
      "stages": [
        {"start_timestamp": 1788125940, "duration_minutes": 27, "stage": 1},
        {"start_timestamp": 1788127560, "duration_minutes": 22, "stage": 2}
      ]
    }
  }
]
```

All times are minutes, percentages are of `duration_minutes`, and stage is
`0` awake / `1` light / `2` deep / `3` REM. The timeline's final entry is a
zero-length awake marker at wake-up time.

### Notifications

```bash
zeblaze-ble notify <ADDRESS> --type message --sender "Alice" --text "Hi!" --i-understand-this-writes
zeblaze-ble notify <ADDRESS> --type call --phone "+1234567890" --sender "Alice" --i-understand-this-writes
zeblaze-ble notify <ADDRESS> --type miss_call --phone "+1234567890" --sender "Alice" --i-understand-this-writes
```

Sends `SEND_SYSTEM_NOTIFICATION` (command id 178).

**Known limitation, confirmed live, unresolved**: the watch acks this
command as successful every time, but a bare connection's notification is
not reliably displayed — the watch shows a fixed system prompt instead
("please connect the BT in the phone's setting"). A `--warmup` prelude
(`VERIFY_USER_NUMBER` etc., matching the real app's connect sequence) was
tried and removed 2026-08-30: it changed neither the ack nor the display.
For text notifications that actually display, use `app-notify` below.

### App-style notifications (WhatsApp/Slack-like)

```bash
zeblaze-ble app-notify <ADDRESS> --app "Signal" --sender "Eve" --text "integration test" --i-understand-this-writes
```

Sends `SEND_APP_NOTIFICATION` (command id 179) — the exact path the official
app itself uses for every third-party notification, recovered from the
decompiled APK (`ControlBleTools.sendAppNotification`). Takes `--app`
(appName), `--sender` (title), `--text` (body), `--ticker` (tickerText,
defaults to `--text`), `--page` (pageName), and `--attempts` (default 8,
for the same BLE ack flakiness every write command shares). No
precondition commands are sent — live testing showed they're not needed.

Live-tested 2026-08-30: acked with the protocol success code every time,
including multi-chunk payloads. **Caveat, learned the hard way**: ack !=
display. While the watch was in a user-id-mismatch bind state it acked
every 179 but showed one stale cached banner and discarded the new
payloads (the "K: K" that never changed, which contained no K in the sent
bytes at all). Display is gated on a valid bind -- see protocol.md's
"Binding" section for the 17/18 repair sequence; sending that from this
tool is the next untested step. Once bound, 179 was the first notification
variant confirmed to display over a BLE-only link.
The title/ticker are capped at 50 chars and the text at 200 by the
encoder, exactly as the official app caps them.

### Workout data: steps, GPS track, heart rate

```bash
zeblaze-ble workout <ADDRESS> --i-understand-this-writes
```

Fetches whatever workout data the watch currently has queued: a summary
(distance, duration, calories, steps, avg/max/min heart rate) and a full
GPS track (timestamp + longitude + latitude per point).

Returns an empty `entries` list if nothing is currently queued.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The parser tests run offline, against real payloads captured from the
official app's own traffic.

## License 

MIT