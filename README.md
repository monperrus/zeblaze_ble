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

### Application binding

```bash
zeblaze-ble bind <ADDRESS> --user-id <ACCOUNT_ID> --i-understand-this-writes
```

Performs the minimal application bind in one connection: ATT MTU 247, SDK
MTU command 0, binding check 17, binding result 18, and binding-status query
16. After binding succeeds, it synchronizes the watch clock and UTC offset
with the host using command 48. The offset is a protobuf `sint32` measured in
quarter-hours; UTC+02:00 is logically `+8` and zigzag-encoded as wire value
16. `--user-id` is the application account
identifier that will be stored on the watch. Use `--phone-type ios` when
binding for an iOS identity; Android is the default. The command succeeds
only when the watch confirms MTU 247, accepts command 18, reports itself
bound, and accepts the time update. It does not change the 12/24-hour format
or send language, real-time telemetry, Classic Bluetooth, or HFP setup.

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
`--i-understand-this-writes` flag. Full protocol details (wire framing, command ids,
message layout): `zeblaze_ble/protocol.md`.

Example:

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
stack. A `bleak`-based reference implementation of the same protocol also exists in `transport.py`
but isn't wired into the CLI because of reliability.

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

Example:

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

The watch must already have a valid application binding. The CLI negotiates
ATT MTU 247 before sending. All three types are verified on the watch:
`message` displays `--sender` and `--text`, `miss_call` displays a missed-call
alert, and `call` displays the incoming-call UI. They can be sent consecutively
over the BLE application link. The transport must acknowledge pending
watch-originated messages, notably command 27 after binding; otherwise the
watch waits and later commands appear to be refused. Message alerts are
transient and disappear automatically. Use `app-notify` for notifications
that need an application label and Android package identity.

Command 178 provides the watch UI, caller metadata, and alert. Answering a
real phone call and carrying its audio are separate operations expected to
use the watch's Classic Bluetooth HFP connection; the button-action signaling
has not yet been captured.

### App-style notifications (WhatsApp/Slack-like)

```bash
zeblaze-ble app-notify <ADDRESS> --app "Signal" --sender "Eve" --text "integration test" --i-understand-this-writes
```

Sends `SEND_APP_NOTIFICATION` (command id 179) — the exact path the official
app itself uses for every third-party notification, recovered from the
decompiled APK (`ControlBleTools.sendAppNotification`). Takes `--app`
(appName), `--sender` (title), `--text` (body), `--ticker` (tickerText,
defaults to `--sender`), `--page` (pageName), and `--attempts` (default 8,
for the same BLE ack flakiness every write command shares).

### Workout data: steps, GPS track, heart rate

```bash
zeblaze-ble workout <ADDRESS> --i-understand-this-writes
```

Fetches whatever workout data the watch currently has queued: a summary
(distance, duration, calories, steps, avg/max/min heart rate) and a full
GPS track (timestamp + longitude + latitude per point).

Returns an empty `entries` list if nothing is currently queued.

### Continuous heart monitoring

```bash
zeblaze-ble hrmonitor <ADDRESS> --i-understand-this-writes
zeblaze-ble hrmonitor-set <ADDRESS> --mode auto --continuous-mode all_day --i-understand-this-writes
```

`hrmonitor` (command 214) reads the watch's heart-rate-monitor setting;
`hrmonitor-set` (command 215) writes it. **`--mode auto` is the switch that
matters**: it is exactly the app's "Continuous Heart Monitoring" toggle, and
with `off` the watch records almost nothing. `--continuous-mode` and
`--frequency` are carried by the protocol but have no observed effect on this
watch -- the official app never writes the first and always sends 0 for the
second, and a written `--frequency 1` reads back as the watch's fixed 5.

The write is a full replace, so the CLI reads the current settings first and
changes only what you asked for; the watch answers 215 with a bare ack, so
the CLI reads back afterwards and reports the result under `stored`.

```json
{
  "mode": 0,
  "frequency": 5,
  "warning": false,
  "warning_value": 0,
  "sport_warning": false,
  "sport_warning_value": 0,
  "continuous_heart_rate_mode": 0
}
```

### REM sleep tracking

```bash
zeblaze-ble rem <ADDRESS> --i-understand-this-writes
zeblaze-ble rem-set <ADDRESS> --on --i-understand-this-writes
```

Reads and writes the watch's REM sleep tracking (commands 251 and 252) — the
app's "Rapid eye movement" switch, whose own screen warns that turning it on
greatly reduces battery runtime, because REM detection keeps the optical
heart-rate sensor running through the night. It is a separate setting from
`hrmonitor`, not a field of it. The watch answers the write with a bare ack,
so the CLI reads back and reports the result under `stored`.

### Watching every channel

```bash
.venv/bin/python scripts/watch_monitor.py <ADDRESS> --seconds 60 --realtime
```

Connects once, subscribes to all five notifying characteristics (6f01
command-response, 6f02 write-ack, 6f03 activity/bulk data, 6f04 large-file,
6f05 voice) and prints each complete message the watch sends, decoded: the
command id and name, a parsed structure where this package has a parser, and
a generic protobuf field dump where it does not. Useful for finding commands
that are not implemented yet.

It sends nothing on its own except the chunked transport's acks; `--realtime`
additionally turns on the watch's live push (command 164) so heart-rate and
step reports arrive every few seconds. `--frames` adds every individual
transport frame, and `--seconds 0` (the default) runs until Ctrl-C.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

The parser tests run offline, against real payloads captured from the
official app's own traffic.

## Recommended Bluetooth config (Linux)

This watch's dual-mode (BR/EDR + LE) bonding and its HID-over-GATT service
trigger real bugs and unhelpful defaults in the standard Linux Bluetooth
stack, independent of anything in this package. Root-caused live against a
real watch on 2026-09-02 -- full narrative in `../bluetooth-problems.md`
(problem #13). Applying these three is recommended on any machine that
talks to this watch (or, generally, any dual-mode-bonded BLE HID-adjacent
peripheral) via BlueZ.

### 1. Disable the `hog` plugin

`bluetoothd`'s built-in `hog` plugin (`profiles/input/hog.c`) auto-probes
every BLE peripheral's HID-over-GATT service on connect. This watch exposes
one (unrelated to its main function) but isn't bonded for it, so every
connection logs a burst of `Request attribute has encountered an unlikely
error` failures (`hog-lib.c:info_read_cb`/`report_reference_cb`/etc.) --
harmless by itself, but repeats on every reconnect and is a real source of
log noise and connection instability.

Use `bluetoothd`'s own `-P`/`--noplugin` command-line flag, via a systemd
drop-in:

```bash
sudo mkdir -p /etc/systemd/system/bluetooth.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/libexec/bluetooth/bluetoothd -P hog\n' \
  | sudo tee /etc/systemd/system/bluetooth.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
```

(Adjust the `bluetoothd` path if your distribution installs it elsewhere,
e.g. `/usr/sbin/bluetoothd`.)

### 2. Disable classic Hands-Free/Headset roles, if this machine has no unrelated use for them

This watch's Class of Device advertises Handsfree support, so it's a
legitimate target for WirePlumber's PipeWire Bluetooth audio-gateway roles
-- and the watch itself makes repeated classic-BT reconnection attempts
that contend for the adapter's radio with this package's own LE sessions. If this machine isn't
used as a Bluetooth phone-call audio device (no soft-phone app, etc.),
drop the Hands-Free/Headset roles globally while keeping ordinary music
playback:

```bash
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cat > ~/.config/wireplumber/wireplumber.conf.d/zeblaze-watch-no-bt-audio.conf <<'EOF'
monitor.bluez.properties = {
  bluez5.roles = [ a2dp_sink a2dp_source bap_sink bap_source ]
}
EOF
systemctl --user restart wireplumber.service
```

### 3. Enable automatic re-pairing after a stale bond

If the watch is factory-reset (or its own bond otherwise gets cleared)
without also removing it on this host, this host keeps retrying the old,
now-invalid encryption key forever: `bluetoothd` retries with backoff (1s,
2s, 4s) then silently gives up re-enabling auto-connect, with no
indication anywhere outside `-d` debug logs.

Add to `/etc/bluetooth/main.conf`'s `[General]` section:

```
JustWorksRepairing = always
```

then `sudo systemctl restart bluetooth`. This lets `bluetoothd` detect and
automatically complete a fresh Just-Works re-pair when a bonded peer's own
side has lost its keys, instead of endlessly retrying a dead key.


## License 

MIT

