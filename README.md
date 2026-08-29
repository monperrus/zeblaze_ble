# Zeblaze BLE

Discovery, packet capture, and live reads for the Zeblaze Beyond 3 Pro. Its
GATT services sit in a vendor UUID namespace that looks
vendor-family-ish at a glance, but a live capture of the real
`com.zhapp.zeblazefit` app (2026-08-29) showed the production protocol is a
small, unencrypted, proprietary scheme ("ZH_SDK"): zero SMP pairing packets,
zero LE-encryption events, just a chunked transport with numeric command ids.
No secret key, nonce, HMAC or AES-CCM is involved. See `zeblaze_ble/protocol.md`
for the full protocol writeup, and `../NOTES.md` /
`../plan-full-ble-encryption.md` for the investigation story, including why
the originally-planned encryption-key extraction (rooting a phone, pulling
`com.zhapp.zeblazefit`'s app storage) turned out to be unnecessary.

`battery`, `fitness`, `realtime`, `heartrate`, and `notify` perform protocol
writes. The remaining commands scan for the watch, verify its GATT layout,
subscribe to its notification channels, and save received packets as JSON
Lines for protocol work.

## Live GATT exploration finding (2026-08-29)

A live session (see `../NOTES.md` for full detail) confirmed the watch has
**no Battery Service (`0x180F`)** at all, and found a previously undocumented
HID-over-GATT service (`0x1812`) that only needs standard BLE pairing (no
vendor-protocol handshake) — its Report Map shows a Consumer Control
(media-key) report plus a second vendor-defined 20-byte input / 1-byte output
report pair, distinct from the `16186f0x` channels below. Also found the
ZH_SDK service actually has 5 channels (`6f01`–`6f05`); `NOTIFICATION_CHANNELS`
below only lists 4.

## Binding-capture finding

The 2026-08-10 binding capture confirms the `16186f02` write channel and
`16186f01` response channel use a small, chunked protobuf transport. It also
contains an application authorization exchange. That exchange is stateful: a
standalone replay of its identity packet is rejected by the watch, so the tool
does not store, expose, or replay the captured identity. In particular, the
capture alone does not provide a reusable session key or a safe battery query.
(Superseded by the 2026-08-29 finding above: no session key is needed for the
`battery` command below — this note is kept for history.)

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Current heart rate

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

## Continuous heart-rate monitor

```bash
.venv/bin/python scripts/heartrate_every_minute.py <ADDRESS> --i-understand-this-writes
```

The monitor establishes one GATT connection, enables the real-time stream
once, and prints a JSON Lines heart-rate value immediately and then every 60
seconds. It keeps the same connection and acknowledges the incoming stream
packets between prints, so it does not reconnect for each reading. Stop it
with Ctrl-C; use `--interval` to change the output cadence. It retries only
initial connection failures (three attempts by default; change with
`--attempts`) and keeps the successful session open.

## Scan and inspect

```bash
zeblaze-ble scan
zeblaze-ble inspect D6:45:15:30:04:71
```

The address in the second command is only an example. BLE privacy addresses can
change, so use the address reported by `scan` **when the watch is advertising**.
An already-connected watch may not appear in a scan; that is expected and does
not mean it is unavailable. In that case, use its known address directly. Close
or force-stop Zeblaze Fit before running a command that connects to the watch,
because most watches allow only one BLE central at a time.

On Linux, `inspect` automatically uses BlueZ's `gatttool` because it can
reliably enumerate this watch even when the calling profile shares its address.
See `../bluetooth-problems.md` for why.

## Passive capture

```bash
zeblaze-ble listen <ADDRESS> --seconds 90 --output capture.jsonl
```

A passive capture may remain empty until a verified, read-only history request
is implemented; no command bytes are guessed here.

## Device info and battery (writes)

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

This is currently flaky in practice — see `../TODO.md`. Retrying a failed
attempt a few times has always eventually succeeded so far.

On Linux this goes through `gatttool -I` rather than `bleak`/BlueZ D-Bus:
`bleak`'s service resolution has proven unreliable for this watch on this
stack. See `../bluetooth-problems.md` for the full list of local BlueZ/bleak
issues hit and how each was worked around. A `bleak`-based reference
implementation of the same protocol also exists in `transport.py` (matches
the official app's wire bytes byte-for-byte against two independent live
captures) but isn't wired into the CLI because of that resolution issue.

## Fitness data and real-time push (writes)

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

## Push a notification (writes)

```bash
zeblaze-ble notify <ADDRESS> --type message --sender "Alice" --text "Hi!" --i-understand-this-writes
zeblaze-ble notify <ADDRESS> --type call --phone "+1234567890" --sender "Alice" --i-understand-this-writes
zeblaze-ble notify <ADDRESS> --type miss_call --phone "+1234567890" --sender "Alice" --i-understand-this-writes
```

Sends `SEND_SYSTEM_NOTIFICATION` (command id 178). Unlike every other write
in this tool, this command was never observed in a live capture — no
notification was ever sent during any capture session. It was instead
recovered by pulling the real `com.zhapp.zeblazefit` APK off the phone
(`adb`), decompiling it, and running its own compiled notification-encoding
code directly in a small JVM harness with the real protobuf runtime, so the
bytes this produces are exactly what the official app would send, not a
guess. See `zeblaze_ble/protocol.md` for the full method and the verified
example bytes, and `android-observations.md` for the APK-pull/decompile
mechanics.

**Known limitation, confirmed live**: the watch acks this command as
successful every time, but does **not** display the `--sender`/`--text`
content — it shows a fixed system prompt instead ("please connect the BT in
the phone's setting"), for all three `--type` values. Likely needs a
classic-Bluetooth (BR/EDR) pairing alongside the BLE link, which this tool
has never established — see protocol.md's "Push notification" section for
the detail and the leading hypothesis. Useful today for verifying the wire
protocol and getting an ack; not yet useful for actually notifying anyone.

Live example (2026-08-29): `zeblaze-ble notify` sent a `message` type
notification and got back `{"response_hex": "08b201a00600"}` — the generic
ack shape (`{1: 178, 100: 0}`, `100: 0` = success).
