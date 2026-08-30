# Open Zeblaze BLE

Python package for the Zeblaze watches, developed from a Beyond 3 Pro. 

Main features: `battery`, `fitness`, `realtime`, `heartrate`, `notify`, and `workout`

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

`fitness` enumerates and fetches every currently-available steps/distance/
calories/heart-rate/activity-duration/effective-standing bucket the watch
has queued. `realtime` enables the watch's real-time data push and prints
each `REPORT_BASIC_DATA` reading (steps, calories, distance, heart rate,
blood oxygen, effective standing, battery) as it arrives. Both are gated
behind `--i-understand-this-writes` for the same reason as `battery`. Full
protocol writeup, including exactly what fields mean and what's still
unverified (e.g. no GPS/location command has been found yet, and the packed
step/distance/calorie byte arrays are unverified against real non-zero
data since this watch is fresh): `zeblaze_ble/protocol.md`.

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
("please connect the BT in the phone's setting"). Staying BLE-only (no
classic-Bluetooth pairing) per project preference, `send_notification` now
also sends `VERIFY_USER_NUMBER` first, matching what the real app always
does on connect and this tool previously skipped — this measurably changes
the watch's behavior.

### App-style notifications (WhatsApp/Slack-like)

```bash
zeblaze-ble app-notify <ADDRESS> --app "Signal" --sender "Eve" --text "integration test" --i-understand-this-writes
```

Sends `SEND_APP_NOTIFICATION` (command id 179) — the exact path the official
app itself uses for every third-party notification, recovered from the
decompiled APK (`ControlBleTools.sendAppNotification`). Takes `--app`
(appName), `--sender` (title), `--text`, `--page` (pageName), `--warmup`
(`none` default / `verify` / `full`), and `--attempts` (default 8, for the
same BLE ack flakiness every write command shares).

The title/ticker are capped at 50 chars and the text at 200 by the encoder,
exactly as the official app caps them. Live-tested 2026-08-30: acked with
the protocol success code every time, including multi-chunk payloads. As
with `notify` above, the ack proves delivery to the watch's command
handler; on-screen display depends on the watch's state (see the same
known limitation).

### Workout data: steps, GPS track, heart rate

```bash
zeblaze-ble workout <ADDRESS> --i-understand-this-writes
```

Fetches whatever workout data the watch currently has queued: a summary
(distance, duration, calories, steps, avg/max/min heart rate) and a full
GPS track (timestamp + longitude + latitude per point).

Returns an empty `entries` list if nothing is currently queued — per this
protocol's established single-consume-queue behavior (see the "Fitness
data" section), a workout already fetched
won't be offered again.

## License 

MIT