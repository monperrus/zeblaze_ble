# The smartwatch protocol of Zeblaze

This is the documentation the wire protocol to talk to Zebaze Watches
the Zeblaze Beyond 3 Pro.

The app's own SDK version string for this is `ZH_SDK_20260730_V2.3.9`, hence
"ZH_SDK" as the name here. The protocol version is called "Apricot".

## GATT layout

Service `16186f00-0000-1000-8000-00807f9b34fb`, with 5 characteristics
(`6f01`-`6f05`), each `WRITE_NO_RESPONSE | NOTIFY` (properties `0x14`):

| UUID suffix | Name in `protocol.py` | Role |
|---|---|---|
| `6f01` | `COMMAND_READ` | app→watch commands' *responses* arrive here (watch writes, app reads via notify) |
| `6f02` | `COMMAND_WRITE` | app→watch commands are *sent* here (app writes, watch acks via notify) |
| `6f03` | `ACTIVITY_DATA` | observed in the GATT table; role not yet characterized |
| `6f04` | `DATA_UPLOAD` | observed in the GATT table; role not yet characterized |
| `6f05` | (unnamed) | observed in the GATT table; role not yet characterized |

On this watch's firmware (`1.1.2`), the fixed ATT handles are: `6f01` value
handle `0x0021` / CCCD `0x0022`; `6f02` value handle `0x0024` / CCCD `0x0025`.

Both `6f01` and `6f02` are used bidirectionally for the ack handshake below —
"read" and "write" in the names above describe the *command* direction, not
a hardware read/write-only restriction.

## Chunked transport

Every message (command or response) — up to and including the tiny 2-byte
ones — goes through the same four-frame handshake, regardless of which side
initiates it or which characteristic carries it:

```
originator            recipient
    |--- header frame ---->|      announces N data chunks incoming
    |<---- ready ack -------|
    |--- data chunk 1 ----->|
    |--- data chunk 2 ----->|      (N of these)
    |         ...           |
    |<--- complete ack ------|
```

All four frame types are plain byte strings written with **Write Command**
(no response) and delivered to the peer via **Notification** — there is no
ATT-level request/response here, the ack/ready semantics are entirely an
application-layer convention on top of write-without-response + notify.

### Header frame (6 bytes)

`00 00 00 00 <N low> <N high>` — `N` is the chunk count, little-endian
16-bit. In every capture so far `N` has been 1 or 2.

- `protocol.header_frame(chunk_count)` builds it.
- `protocol.is_header_frame(frame)` / `chunk_count_from_header(frame)` parse it.

### Ready ack (6 bytes, fixed)

`00 00 01 01 00 00` — sent by the recipient immediately after a header
frame, before any data chunks are sent.

- `protocol.ACK_READY`, `protocol.is_ready_ack(frame)`.

### Data chunk (2-byte header + payload)

`<index> 00 <payload bytes>` — `index` is 1-based (`0x01`, `0x02`, ...),
one chunk per header-announced count. Chunk size in the captures was well
under 180 bytes (single-chunk for every message observed), so the exact MTU
segmentation boundary for larger multi-chunk payloads isn't confirmed;
`gatttool_transport.py`/`transport.py` use 180 bytes as a conservative
default.

- `protocol.data_chunk(index, payload)` builds it.
- `protocol.split_data_chunk(frame)` parses it → `(index, payload)`.

### Complete ack (6 bytes, fixed)

`00 00 01 00 00 00` — sent by the recipient once it has all `N` chunks.

- `protocol.ACK_COMPLETE`, `protocol.is_complete_ack(frame)`.

### Reassembly

Concatenate the chunk payloads in index order (`b"".join(chunks[i] for i in
sorted(chunks))`) to get the actual message bytes, which are themselves a
small protobuf message (see below).

### Example: a full request/response round trip

Requesting `GET_DEVICE_INFO` (command id 32, `08 20` as protobuf bytes) and
receiving the reply, both on their respective channels:

```
app  -> 6f02: 00 00 00 00 01 00        (header: 1 chunk coming)
app <-  6f02: 00 00 01 01 00 00        (ready ack)
app  -> 6f02: 01 00 08 20              (chunk 1: command id 32)
app <-  6f02: 00 00 01 00 00 00        (complete ack)

app <-  6f01: 00 00 00 00 01 00        (watch announces 1 chunk response)
app  -> 6f01: 00 00 01 01 00 00        (ready ack)
app <-  6f01: 01 00 08 20 22 34 0a 32 0a 05 31 2e 31 2e 32 12 05
              33 30 31 30 38 1a 11 44 36 3a 34 35 3a 31 35 3a 33
              30 3a 30 34 3a 37 31 22 05 31 30 30 30 35 2a 04 08
              43 10 02 30 00 38 01
app  -> 6f01: 00 00 01 00 00 00        (complete ack)
```

The reassembled response payload (single chunk here, so no concatenation
needed) is `08 20 22 34 0a 32 ... 38 01` — see "Message schema" below for
the decode.

## Command ids

Single-byte command ids observed live, sent as the protobuf payload
`{1: command_id}` (i.e. bytes `08 <id>`):

| id (dec) | id (hex) | Name |
|---|---|---|
| 16 | `0x10` | `INQUIRY_BINDING_STATUS` |
| 17 | `0x11` | `BINDING_CHECK` — see "Binding" below |
| 18 | `0x12` | `BINDING_RESULT` — see "Binding" below |
| 19 | `0x13` | `VERIFY_USER_NUMBER` (carries the server-side numeric user id as a string, not a secret) |
| 32 | `0x20` | `GET_DEVICE_INFO` — bundles firmware/MAC/serial **and battery status** |
| 33 | `0x21` | `GET_DEVICE_BATTERY` |
| 48 | `0x30` | `SET_SYSTEM_TIME` |
| 65 | `0x41` | `GET_LANGUAGE_DETAILED` |
| 69 | `0x45` | `SET_USER_INFORMATION` |
| 112 | `0x70` | `GET_FITNESS_TYPE_ID_LIST` — see "Fitness data" below (sleep included) |
| 113 | `0x71` | `REQUEST_FITNESS_TYPE_ID` |
| 115 | `0x73` | `CONFIRM_FITNESS_TYPE_ID` |
| 117 | `0x75` | `GET_FITNESS_SPORT_ID_LIST` — see "Workout data" below |
| 119 | `0x77` | `REQUEST_FITNESS_SPORT_DATA` — see "Workout data" below |
| 121 | `0x79` | `CONFIRM_FITNESS_SPORT_ID_LIST` — see "Workout data" below |
| 164 | `0xa4 0x01` | `REAL_TIME_DATA_SWITCH` — see "Real-time push" below |
| 165 | `0xa5 0x01` | `REPORT_BASIC_DATA` — watch-initiated push, never sent by us |
| 178 | `0xb2 0x01` | `SEND_SYSTEM_NOTIFICATION` — see "Push notification" below |
| 179 | `0xb3 0x01` | `SEND_APP_NOTIFICATION` — see "App push notification" below |
| 211 | `0xd3 0x01` | `GET_EVENT_INFO_LIST` — see "Event reminders" below |
| 212 | `0xd4 0x01` | `SET_EVENT_INFO_LIST` — see "Event reminders" below |
| 214 | `0xd6 0x01` | seen as a watch-initiated push, response `7a 0b 1a 09 {1:1, 2:5, 3:0, 4:130}`; not decoded |
| 247 | `0xf7 0x01` | `GET_SCREEN_SETTING` — response `{15: {14: {1: brightness_level, 2: normally_on_switch, 3: on_screen_duration, 4: double_click_the_highlighted_screen}}}` |
| 249 | `0xf9 0x01` | `REQUEST_SCREEN_SETTING` — watch-initiated, asks the app to re-read the screen settings |
| 495 | `0x1ef` | `GET_NOTIFICATION_SETTINGS` — observed returning `errorCode = 1` (unsupported on this model) |
| 480 | `0x1e0` | seen as `getClassicBluetoothState()`; multi-byte varint (`e0 03`) since >127 |

`GET_DEVICE_INFO` (32), the fitness-data commands (112/113/115), the
real-time push (164/165, including the `heartrate` CLI convenience command
that just filters that stream for `heart_rate`), the notification pushes
(178/179), and the workout-data commands (117/119/121) are implemented in
`zeblaze_ble` — see `protocol.py` and `gatttool_transport.py`. The others
are documented here for whoever extends this next; their request encoding
follows the same `08 <varint id>` pattern (varint-encode the id if ≥128),
but their response schemas haven't been decoded.

There is no known command that performs a destructive action (factory
reset, firmware update).

## Message schema (protobuf, no compiled `.proto` available)

Messages are small ad-hoc protobuf (varint + length-delimited fields only,
confirmed sufficient for everything seen so far — see
`protocol.decode_protobuf`, a minimal hand-rolled decoder).

### `GET_DEVICE_INFO` response (command id 32)

Two levels of wrapper before the real fields — easy to miss (see
`protocol.parse_device_info`'s docstring, this tripped up the first
implementation attempt):

```
{
  1: command_id (32, varint — echoes the request)
  4: {                              <- one extra wrapper level
    1: {                            <- the actual device-info message
      1: firmware_version (string, e.g. "1.1.2")
      2: equipment_number (string, e.g. "30108")
      3: mac (string, e.g. "D6:45:15:30:04:71")
      4: serial_number (string, e.g. "10005")
      5: device_battery_status {
        1: capacity (varint, percent)
        2: charge_status (varint, 2 = NOT_CHARGING observed; other values unconfirmed)
      }
      6: remote_device_remote_camera_switch (varint bool, 0 observed)
      7: sports_icon_function_protocol_switch (varint bool, 1 observed)
    }
  }
}
```

Decoding the example payload above (`08 20 22 34 0a 32 0a 05 31 2e 31 2e 32
12 05 33 30 31 30 38 1a 11 44 36 3a 34 35 3a 31 35 3a 33 30 3a 30 34 3a 37 31
22 05 31 30 30 30 35 2a 04 08 43 10 02 30 00 38 01`):

- `08 20` → field 1, varint 32
- `22 34` → field 4, length 0x34=52 bytes → wrapper
  - `0a 32` → field 1, length 0x32=50 bytes → the real device-info message
    - `0a 05 "1.1.2"` → field 1 (firmware_version)
    - `12 05 "30108"` → field 2 (equipment_number)
    - `1a 11 "D6:45:15:30:04:71"` → field 3 (mac)
    - `22 05 "10005"` → field 4 (serial_number)
    - `2a 04 08 43 10 02` → field 5 (battery): `{1: 0x43=67, 2: 0x02}`
    - `30 00` → field 6, varint 0
    - `38 01` → field 7, varint 1

Example exchange:
`GET_DEVICE_INFO_VALUE device = firmware_version: "1.1.2" ... capacity: 67
charge_status: NOT_CHARGING ...`.

### Other command payloads seen but not decoded

- `VERIFY_USER_NUMBER` (19) request: `08 13 1a 09 32 07 "2011999"` — field 3
  wraps field 6 (a string) holding the numeric user id as ASCII decimal.
  This is the "application authorization exchange" the top-level README's
  binding-capture note refers to; it identifies the account, not a device
  secret.
- `INQUIRY_BINDING_STATUS` (16) response: `08 10 1a 02 08 01` → field 3 = `{1:
  1}`, i.e. `request_binding_status: true`.

## Fitness data (steps, distance, calories, sleep, heart rate, activity, standing)

### The `time` submessage

Used everywhere a date/timestamp is needed in this part of the protocol:
`{1: year, 2: month, 3: day, 4: hour, 5: minute, 6: second}`, all varints.
`protocol.encode_time(...)`.

### `GET_FITNESS_TYPE_ID_LIST` (112) — the menu

Request: just `08 70` (no payload beyond the command id).

Response shape: `{1: 112, 9: {2: {1: [{1: time, 2: function_type}, ...
repeated]}}}` — two wrapper levels before a repeated list of `(date,
function_type)` pairs, each one a currently-fetchable data bucket.
`protocol.parse_fitness_type_id_list`.

`function_type` values observed live: `0` = daily (steps/distance/calories),
`1` = sleep, `2` = continuous heart rate, `11` = effective standing,
`12` = activity duration (see `protocol.FITNESS_TYPE_*`). These match the
app's own `FitnessProtos$SEFitnessTypeId$SEFitnessFunctionType` enum, which
declares 25 values in total; the rest (blood oxygen, pressure, temperature,
ECG, nap, drink water, and the "GoMore" algorithm outputs) have never been
offered by this model, whose `DEVICE_SETTING`
capability flags (`android-observations.md`) disable them. A sleep bucket
appears only for a date the watch actually recorded a night on, so the
number of entries varies with what the watch has to offer.

### `REQUEST_FITNESS_TYPE_ID` (113) — fetch one bucket

Request shape: `{1: 113, 9: {1: {1: time, 2: function_type}}}` — note this
has **one more wrapper level** than the list entries above (`9.1.{1,2}`
here vs. `9.2.1[].{1,2}` there — different nesting depth for the single-item
request than for the repeated-list response, verified by encoding and
byte-comparing against 8 live examples, see `protocol.encode_fitness_type_id_request`).

Response shape: `{1: 113, 9: {N: <bean>}}` where `N` depends on
`function_type` (a lookup table, not a formula —
`protocol._FITNESS_RESPONSE_FIELD`):

| function_type | response field N | bean |
|---|---|---|
| 0 (daily) | 4 | `DailyData` |
| 1 (sleep) | 5 | `SleepData` |
| 2 (continuous heart rate) | 6 | `ContinuousHeartRate` |
| 11 (effective standing) | 14 | `EffectiveStanding` |
| 12 (activity duration) | 15 | `ActivityDuration` |

Both the function-type values and these field numbers are also the app's own
protobuf constants (`FitnessProtos$SEFitness`'s `*_FIELD_NUMBER`), which is
how the 11/12 pair was settled: the wire evidence pairs type 11 with field 14
and type 12 with field 15, and the app names field 14 effective standing and
field 15 activity duration.

Bean field layouts (all verified against real payloads, see
`protocol.parse_daily_data` etc.):

- **`DailyData`**: `{1: {fitness_type_id echo}, 2: steps_frequency_minutes,
  3: steps_raw, 4: distance_frequency_minutes, 5: distance_raw,
  6: calorie_frequency_minutes, 7: calorie_raw}`. `frequency` was `60`
  (minutes) in every capture, giving 24 hourly buckets; each `*_raw` array
  was 48 bytes = **2 bytes per bucket, big-endian unsigned**. Byte order is
  confirmed by a non-zero capture: a day whose 21:00 and 23:00 hours held 29
  and 95 steps encoded those buckets as `00 1d` and `00 5f`, matching the
  app's own decode of the same bytes. `protocol.unpack_buckets` does that
  decode; `DailyData` carries both the raw bytes and the decoded
  `steps`/`distance`/`calories` lists. Distance is metres (the app stores a
  3 km goal as `3000`, and live pushes report ~0.8 m per step); the calorie
  unit is unconfirmed. The app's protobuf class declares further fields
  (8: HBA data, 9..15: today-only step/calorie variants) that this model has
  never sent.
- **`ContinuousHeartRate`**: `{1: echo, 2: frequency_minutes (5 observed,
  giving 288 buckets/day), 3: heart_rate_raw, 4: max_value, 5: min_value,
  6: resting_value, 7: hour_max_raw (24 bytes), 8: hour_min_raw (24 bytes)}`.
  **1 byte per bucket**, confirmed against a non-zero capture: a day whose
  only reading was 60 bpm at 05:20 had byte 64 of the 288-byte array set to
  60, matching the app's decode. A zero bucket means "no sample taken", not
  a measured zero — the hour arrays were all-zero even on that day.
- **`SleepData`** (function_type 1): `{1: echo, 2: start_sleep_timestamp,
  3: end_sleep_timestamp, 4: sleep_duration, 5: sleep_score,
  6: awake_time, 7: awake_time_percentage, 8: light_sleep_time,
  9: light_sleep_time_percentage, 10: deep_sleep_time,
  11: deep_sleep_time_percentage, 12: rapid_eye_movement_time,
  13: rapid_eye_movement_time_percentage, 14: {1: [stage, ...repeated]},
  15: sleep_type, 16: sleep_readiness_score}` — see "Sleep data" below.
- **`EffectiveStanding`** (function_type 11, response field 14):
  `{1: echo, 2: frequency_minutes (60), 3: raw (24 bytes)}` — no further
  fields exist in the app's protobuf class either.
- **`ActivityDuration`** (function_type 12, response field 15):
  `{1: echo, 2: frequency_minutes (60), 3: raw (24 bytes),
  4: daily_time, 5: daily_percentage, 6..31: 13 (time, percentage) pairs,
  one per sport category}`. Unlike the other beans, everything past `raw` is
  **separate varint fields**, not a packed byte array — so those are an
  exact decode, no byte-order guessing needed. The category order (running,
  walking, cycling, swimming, fitness exercise, outdoor, ball game, yoga,
  winter, dance movement, aquatic, leisure, other) is the app's own
  `SEActivityDurationData` field order. Field 5 (`daily_percentage`) was
  `100` with everything else 0 in the captures so far.

### `CONFIRM_FITNESS_TYPE_ID` (115) — acknowledge a bucket

Identical request shape to `REQUEST_FITNESS_TYPE_ID` (same
`encode_fitness_type_id_request`, different command id), sent immediately
after processing that bucket's data. Response is just the generic
ack/error-code shape below — no bean data.

### The generic ack/error-code response shape

`{1: <command_id echo>, 100: <varint, 0 = success>}` — e.g. `08 71 a0 06 00`
decodes to `{1: 113, 100: 0}`. Seen for `SET_SYSTEM_TIME`, `CONFIRM_FITNESS_TYPE_ID`,
and — importantly — **also returned by `REQUEST_FITNESS_TYPE_ID` itself**
when there's nothing to send.

Buckets are **not** single-consume, though: in the 2026-08-31 capture the
app re-requested and re-confirmed the same `(2026-08-30, sleep)` bucket in
four separate sessions and got the full, byte-identical data bean every
time. The empty-success shape means the watch has no data for that
`(date, function_type)` — not that it has already handed it over once.

### Heart-rate sampling frequency (`function_type` 2)

`ContinuousHeartRate.frequency_minutes` is the sampling interval the watch
recorded that day with, and it is the **only** thing that says how the
array maps onto the clock — never assume a fixed bucket count. Every
capture so far reported `5`, giving `1440 / 5` = **288 buckets per day**, so
bucket `i` covers local time `i * 5` minutes past midnight (bucket 64 =
05:20). The array length has matched `1440 / frequency` exactly in every
capture; treat a mismatch as a decode error rather than padding.

Two consequences worth keeping in mind when consuming this data:

- **A zero bucket means no sample was taken, not a heart rate of zero.**
  Skip zeros rather than averaging them in. On a real captured day, 287 of
  the 288 buckets were zero and one held 60 bpm — the watch is not sampling
  every 5 minutes around the clock even though the frequency says 5.
- `max_value` / `min_value` are the watch's own summary over the non-zero
  samples (both were 60 on that day, consistent with the single sample);
  `resting_value` was 0, i.e. not computed, with that little data.

The frequency is a per-day property of the returned bean, not a constant of
this model: the watch's own "continuous heart rate" setting is expected to
change it (see `TODO.md`), so re-read `frequency_minutes` per bucket instead
of hard-coding 5 or 288.

The separate `hour_max_raw` / `hour_min_raw` arrays are 24 bytes each, one
byte per hour of the day, and were all-zero even on the day that had a
sample — so their exact semantics are unconfirmed.

### Sleep data (`function_type` 1)

One bucket = one night, requested exactly like any other fitness bucket:
`REQUEST_FITNESS_TYPE_ID` (113) with `function_type` 1 and the date of the
**evening** the night started (a night spanning 2026-08-30 22:39 → 2026-08-31
05:17 is offered and returned under the date 2026-08-30). The bean comes
back in field 5 of the response's field-9 wrapper. `protocol.parse_sleep_data`
→ `SleepData`; `zeblaze-ble sleep <ADDRESS>` fetches only these buckets.

Summary fields: `start_sleep_timestamp` / `end_sleep_timestamp` are Unix
seconds in the watch's local time zone. `sleep_duration` and every
`*_time` field are **minutes**; every `*_percentage` is a whole percent of
`sleep_duration`. `sleep_score` is the app's 0-100 sleep score.
`sleep_type` (field 15) is `1` = night sleep, `0` = daytime sleep
(`protocol.SLEEP_TYPE_*`).

Field 14 is the stage timeline, with the usual extra wrapper level:
`{1: [{1: start_timestamp, 2: sleep_duration, 3: sleep_distribution_type},
...repeated]}`. Each entry is one contiguous stretch of a single stage, in
chronological order; `sleep_duration` is again minutes. The stage enum
(`protocol.SLEEP_STAGE_*`) is `0` = awake, `1` = light, `2` = deep,
`3` = rapid eye movement. The **last entry is a wake-up marker**: stage
awake with duration 0, timestamped at `end_sleep_timestamp`.

Verified against a live night (see `tests/test_protocol.py`, which parses
the captured payload): 16 stage entries, and the per-stage durations summed
by stage reproduce `light_sleep_time` = 295 and `deep_sleep_time` = 103
exactly, with `sleep_duration` = 398 = their sum.

### Real-time push: `REAL_TIME_DATA_SWITCH` (164) / `REPORT_BASIC_DATA` (165)

Request (verified against the one live capture, `protocol.encode_real_time_data_switch_request`):
`{1: 164, 12: {3: <0 or 1>}}` — field 3's exact on/off encoding is a
best-effort guess (only an "enable" capture exists; `0` was captured for
enable, so `False`/disable is assumed to be `1`, unverified).

Sending this makes the watch start pushing unsolicited `REPORT_BASIC_DATA`
(165) messages on the notify channel every few seconds (no further request
needed per push — this is the one message in the whole protocol that
arrives without the app having asked for that specific instance).
`gatttool_transport.enable_real_time_data_and_listen` just calls
`receive_message()` in a loop, since unsolicited and solicited messages use
the identical chunked-transport framing.

Response shape: `{1: 165, 12: {4: <bean>}}` (note: same double-wrapper
pattern as `GET_DEVICE_INFO`'s field 4). Bean, `protocol.RealTimeData`:

```
1: steps (varint)
2: calories (varint)
3: distance (varint)
4: heart_rate (varint)
5: blood_oxygen (varint)
6: effective_standing (varint)
7: battery {1: capacity, 2: charge_status}   -- identical shape to GET_DEVICE_INFO's battery field
8, 9: varint, always 0 in captures, meaning unidentified
10: physiologicalCycle submessage (19 bytes, menstrual-cycle tracking fields) -- not parsed, not exposed
13: varint, always 0, meaning unidentified
16: steps_hourly_raw (48 bytes, 2 bytes/hour -- same byte-order caveat as DailyData)
17: distance_hourly_raw (48 bytes)
18: calorie_hourly_raw (48 bytes)
```

Live-tested 2026-08-29: connected, enabled real-time data, and successfully
decoded a real `RealTimeData` reading — `battery_capacity=67,
battery_charge_status=2` (matching the independently-verified `battery`
command result from the same session), everything else `0` since this was
a same-day-bound device with no accumulated activity yet.

### `steps`/`calories`/`distance` update live; `heart_rate` may not, on the timescale tested

Live-tested again later the same day with an actual workout in progress
(`zeblaze-ble realtime` polled repeatedly): `steps`, `calories`, and
`distance` visibly incremented between polls (e.g. `steps` 2205→2223 over
106s, `calories` continuing to climb even in a later poll where `steps`
and `distance` had stopped moving), and the current-hour bucket in
`steps_hourly_raw`/`distance_hourly_raw` incremented by *exactly* the same
amount as the running totals each time — strong confirmation these are
genuinely live, not cached. `heart_rate`, however, read exactly `95` across
three consecutive polls spanning about 4 minutes, while everything else
around it changed. Not conclusive either way yet: 4 minutes might still be
inside a single measurement interval this field updates on (the
*different* command `ContinuousHeartRateBean.continuousHeartRateFrequency`
observed `5` (minutes) elsewhere in this protocol, which is suggestive but
not proven to be the same interval `RealTimeData.heart_rate` uses — no
capture has confirmed that assumption). Needs a longer-spaced poll (>5-10
min apart) to confirm whether it's periodic-but-slower or actually stuck;
see `TODO.md`.


## Workout data: GPS track and summary (117 / 119 / 121, activity channel `6f03`)

### GATT channel: `16186f03`

Workout data uses a **third** characteristic, `16186f03`
(`ACTIVITY_DATA` in `protocol.py`),
value handle `0x0027` / CCCD `0x0028` on this firmware. Same chunked
transport (header/ready-ack/data-chunks/complete-ack, byte-identical to
what's documented above), just carrying much larger payloads and, unlike
`6f01`/`6f02`, spanning **multiple consecutive rounds** of that transport
for one logical transfer (see below).

### `GET_FITNESS_SPORT_ID_LIST` (117) — what's queued

Request: `08 75` (just the command id, like `GET_FITNESS_TYPE_ID_LIST`).
Response shape `{1: 117, 9: {3: <sport ids>}}`, where `sport ids` is a
concatenation of 7-byte entries, one per available data blob for the
queued workout(s):

```
[0:4] timestamp (LE uint32, this workout's start time)
[4]   constant byte (0x08 in the one capture -- meaning unconfirmed)
[5]   sport_type (single byte, 2 = observed for a walk/run)
[6]   flags byte; low 2 bits = data type (SPORT_DATA_POINT=0,
      SPORT_DATA_REPORT=1, SPORT_DATA_GPS=2); upper 6 bits constant
      across all 3 entries in the one capture (meaning unconfirmed)
```

The one capture had exactly 3 entries for the one workout: GPS, POINT, and
REPORT (in that order). `protocol.parse_sport_id_list`.

The queue can hold entries for **more than one** past workout at once, each
group of (typically 3) entries sharing the same `timestamp` field
(confirmed live 2026-08-29: recording a second workout added a second
group of 3 entries with a different timestamp alongside the first, still
listed). A `REQUEST_FITNESS_SPORT_DATA`/`CONFIRM_FITNESS_SPORT_ID_LIST`
call's `sport_ids` blob should therefore select **one workout's entries at
a time** (`protocol.latest_workout_entries` picks the most recent group) —
bundling entries from more than one workout into a single request means
that request only splits successfully if every one of those entries' data
can be located, so one unfetchable workout (e.g. one the watch has stopped
offering real data for, see "`CONFIRM_FITNESS_SPORT_ID_LIST` must only
follow a verified-complete transfer" below) blocks every other workout
bundled with it too.

### `REQUEST_FITNESS_SPORT_DATA` (119) — fetch it, and `CONFIRM_FITNESS_SPORT_ID_LIST` (121) — acknowledge it

Both share the identical request shape `{1: command_id, 9: {3: sport_ids}}`
(the *same* 21-byte blob from 117's response, echoed back verbatim — no
need to re-encode the individual 7-byte entries, `encode_fitness_sport_id_list_request`
just takes the raw concatenated bytes). This is a shallower shape than
`encode_fitness_type_id_request`'s date+type selector; don't confuse the
two.

**119 gets no reply on the normal command channel.** Every other command
in this protocol gets a response (or at least an ack) on `6f01`; 119
doesn't. Instead, sending it makes the watch start pushing the actual data
on the activity channel (`6f03`) instead — the bulk transfer itself *is*
the reply. Only after that transfer finishes does the app send 121, which
*does* get a normal `{1: 121, 100: 0}` ack on `6f01`.

### The activity-channel transfer: multiple rounds, no wire-level boundary marker, either GATT channel

The single offline capture showed the watch sending the 3 requested
entries' combined ~7.8KB of data as **7 separate header/ready-ack/chunks/
complete-ack rounds** back to back on `6f03` (chunk counts `7, 7, 7, 7, 5,
1, 1` — 35 individual data-chunk notifications, ~225 bytes each, consistent
with the negotiated 247-byte ATT MTU).

A round's header is not guaranteed to arrive on `6f03`: it can also arrive
on `6f01` (the normal command-response channel), observed live for the
first round of a real transfer. A receiver must accept a round's header on
*either* handle and complete that round (ready-ack, chunks, complete-ack)
on whichever handle it arrived on — see
`GatttoolSession.receive_all_activity_data`.

`zeblaze_ble` uses a robust strategy: `receive_all_activity_data` keeps
receiving rounds and concatenating them into one buffer until no new round
*starts* within a grace period (default 3s after the first round, longer —
matching the normal per-message timeout — for the first round itself,
since a live transfer's first round can take noticeably longer than 3s to
begin). A round already in progress still gets the normal per-notification
timeout, only the wait for the *next* round's header is bounded by the
grace period. Then, since every entry's data starts with that entry's own
7-byte id (already known from the 117 response),
`protocol.split_sport_data_blobs` finds each id's byte offset in the
combined buffer and slices between them.

### `CONFIRM_FITNESS_SPORT_ID_LIST` must only follow a verified-complete transfer

Send 121 only after `split_sport_data_blobs` has confirmed every requested
entry is present in the received bytes. Confirming a partial or empty
transfer tells the watch the data was delivered: a repeated
`REQUEST_FITNESS_SPORT_DATA` for the same entries after a premature confirm
no longer returns the real payload, only a fixed 29-byte reply, byte-identical
across retries:

```
08 1b 1a 19 42 17 08 00 10 01 1a 11 44 36 3a 34 35 3a 31 35 3a 33 30 3a 30 34 3a 37 31
```

which decodes to `{1: 27, 3: {8: {1: 0, 2: 1, 3: "<watch MAC as ASCII>"}}}`
— command id 27 is never sent by this tool, and the shape doesn't match the
generic ack (`{1: <echoed command id>, 100: <code>}`) used everywhere else
in this protocol. Its exact meaning (a distinct "already delivered, nothing
to send" reply vs. an unrelated periodic identity broadcast that merely
coincides with this window) is **not confirmed** — but a premature confirm
reproducibly and, so far, permanently makes the real payload for those
entries unobtainable. `request_workout_data` raises instead of confirming
when the drain comes back incomplete.

**`REQUEST_FITNESS_SPORT_DATA` itself may be one-shot per entry id,
independent of whether `CONFIRM_FITNESS_SPORT_ID_LIST` is ever sent.**
Live-tested 2026-08-29: a workout whose entries had only ever been sent in
a *bundled* `REQUEST_FITNESS_SPORT_DATA` call (mixed with another,
already-stale, workout's entries — a call that never reached the confirm
step, since the mixed-in stale entry made `split_sport_data_blobs` fail
first) later returned only the `{1: 27, ...}` stub on every subsequent
`REQUEST_FITNESS_SPORT_DATA` retry for *just that workout's own entries in
isolation*, across 8 separate attempts. If confirming were the only thing
that marked an entry as delivered, an unconfirmed entry should still have
been re-offerable. It wasn't. Treat every `REQUEST_FITNESS_SPORT_DATA` call
for a given entry id as consuming that id's one real-data delivery, not
just every confirmed one — so don't retry a failed/partial workout fetch by
re-sending `REQUEST_FITNESS_SPORT_DATA` for the same ids; a fresh workout
recording is the only known way to get a usable id again.

### `SPORT_DATA_REPORT` (dataType 1) — workout summary, 103 bytes in the one capture

Every offset below was found by searching the real 103-byte blob for this
workout's already-known values (from `sportmodleinfo`/`exerciseoutdoor` in
the phone's own database) and confirming the match exactly — not guessed:

```
[0:7]   sport entry id (7 bytes)
[7]     status byte (0 observed)
[8:12]  unknown
[12:16] start_time (LE uint32 -- duplicates the entry id's own timestamp)
[16:20] end_time (LE uint32)
[20:24] duration_seconds (LE uint32)
[24:28] distance_meters (LE uint32)
[28:30] calories (LE uint16)
[30:42] unknown
[42:44] steps (LE uint16)
[44:48] unknown
[48]    avg_heart_rate (single byte)
[49]    max_heart_rate (single byte)
[50]    min_heart_rate (single byte)
[51:84] zero in the one capture (probably reserved/other-sport-type fields,
        e.g. swim laps or cycling cadence, not applicable to a walk)
[84:]   non-zero tail (19 bytes), meaning unidentified -- possibly a checksum
```

Live values matched the phone's database exactly: `distance_meters=1552`,
`steps=1657`, `avg_heart_rate=105`, `max_heart_rate=131`,
`min_heart_rate=63`, `duration_seconds=1428`. `protocol.parse_workout_report`.

### `SPORT_DATA_GPS` (dataType 2) — GPS track, 6745 bytes / 561 points in the one capture

```
[0:7] sport entry id [7] status byte (0) [8] unknown (0xE0 observed)
[9:]  repeating 12-byte point records:
        [+0:4] timestamp (LE uint32, absolute Unix seconds)
        [+4:8] longitude (LE float32)
        [+8:12] latitude (LE float32)
```

A 4-byte remainder after the last full 12-byte record is
unaccounted for (too short to be another point; likely a footer/checksum).
`protocol.parse_gps_track`.

### `SPORT_DATA_POINT` (dataType 0) — not decoded

1020 bytes in the one capture, same 9-byte header pattern as GPS, but the
per-record structure wasn't cracked (unlike GPS/REPORT, no known ground
truth values were available to search for — the app's own
`DevSportInfoBean` fields didn't expose an obviously-corresponding parsed
array to check candidate byte offsets against). Byte-level inspection
found plausible-range values resembling per-interval heart rate samples
(bytes in the 85-95 range recurring with some regularity) but nothing
confirmed. Exposed as `WorkoutData.point_data_raw` (raw bytes only).

## Push notification: `SEND_SYSTEM_NOTIFICATION` (178) (INCOMPLETE)

Structure: `{1: 178, 13: {1: {1: type, 2: phoneNumber, 3: contactsInfo,
4: messageText}}}` — i.e. field 13 of the same `SEWear{1: id, ...}` envelope
every other command uses, holding a `SENotification{1: SESystemNotification{...}}`.
`type` (varint enum, `com.zh.ble.wear.protobuf.NotificationProtos.SESystemNotification.SEType`):
`0` = `CALL`, `1` = `MISS_CALL`, `2` = `MESSAGE`. For `CALL`, the app itself
always forces `messageText` to `""` regardless of what's passed —
`protocol.encode_system_notification_request` replicates this.

The real app always sends `VERIFY_USER_NUMBER` (19, see "Other command
payloads seen but not decoded" above) immediately after connecting, before
any data command. `gatttool_transport.send_notification` once had a
`warmup` parameter (`none`/`verify`/`full`) replicating that prelude; it
was removed 2026-08-30 after live testing across all three modes showed
every one gets the same `{1: 178, 100: 0}` ack (`08 b2 01 a0 06 00`, code
0 = success) — the ack does not depend on which of these BLE commands, if
any, precede the notification — and each extra prelude command was itself
a source of the documented ack flakiness.

**Live-tested, still unresolved**: whether/when the watch actually
*displays* the notification's content is a separate question from the ack
above, and is not yet understood — investigation history and current
hypotheses live in `TODO.md`'s "notify" entry (and `JOURNAL.md` for the
narrative); local-Bluetooth-stack diagnostics attempted along the way are
in `bluetooth-problems.md`.

## App push notification: `SEND_APP_NOTIFICATION` (179)

Structure: `{1: 179, 13: {2: {1: appName, 2: pageName, 3: title, 4: text,
5: tickerText}}}` — the same `SEWear` envelope and field 13 as 178, but
holding `SENotification{2: SEAppNotification{...}}` (appNotification is
field 2 of `SENotification`; systemNotification is field 1). This is the
command the real app uses for every third-party notification.

**Ground truth (2026-08-31, 07:39:21):** a Gmail notification the watch
displayed correctly, captured in the app's own BLE debug log and confirmed
byte-for-byte in the HCI snoop of the same write:

```
01 00 08 b3 01 6a 5f 12 5d
  0a 05 "Gmail"
  12 15 "com.google.android.gm"
  1a 10 "Martin Monperrus"
  22 19 "hello martin how are you?"
  2a 10 "Martin Monperrus"
```

(`01 00` is the data-chunk header; the frame is a single chunk, sent on
`6f02` after the usual `00 00 00 00 01 00` header / `00 00 01 01 00 00`
ready-ack handshake, and acked with `08 b3 01 a0 06 00` arriving on
`6f01`. No precondition command of any kind precedes it in the capture.)
`tests/test_protocol.py` pins `encode_app_notification_request` to these
exact bytes.

### Where each field comes from, and why `pageName` matters

`MyNotificationsService.onNotificationPosted` fills the five strings from
one Android `StatusBarNotification`:

| field | source |
|---|---|
| `pageName` | `StatusBarNotification.getPackageName()` — e.g. `com.google.android.gm` |
| `appName` | `AppUtils.getAppName(pageName)` — the package's display label, e.g. `Gmail` |
| `title` | the `android.title` extra |
| `text` | the `android.text` extra |
| `tickerText` | `Notification.tickerText` — Android's short summary line |

So `pageName` is the **package name, and the root the app derives `appName`
from**; it is structurally never empty in a real send. An earlier note here
called it "unused by the app for third-party notifications" — that was
wrong, and it was the one field this repo's `app-notify` always sent empty.
`encode_app_notification_request` now rejects an empty `page_name`.

Note also that in the working capture `tickerText` equals the **title**
(the sender name), not the body — Android's ticker is a summary line, not
the message. The CLI's `--ticker` therefore defaults to `--sender`.

`ControlBleTools.sendAppNotification` truncates title/ticker at 50 chars
and text at 200 (`s[:max-1] + "..."`, `BleUtils.truncateString`) and applies
**no** cap to appName/pageName — its smali truncates only its p3/p4/p5
arguments. `protocol.py` replicates that exactly.

### Prior live tests, and what they were actually showing

Live-tested 2026-08-30, BLE-only: every 179 was acked `{1: 179, 100: 0}`
(`08 b3 01 a0 06 00`) with no precondition commands, including multi-chunk
payloads (~226 bytes → 2 chunks). **Ack ≠ display**: during that session
every 179 was acked while the watch showed one stale cached banner and
discarded the new payloads — the watch was in a user-id-mismatch bind state
at the time (see "Binding" below), which has since been repaired. So the
ack proves delivery to the watch's protocol layer only.

Re-tested 2026-08-31, BLE-only, after the `pageName` fix: a 179 carrying
the *captured working frame's own* field values (appName `Gmail`,
pageName `com.google.android.gm`, tickerText = title, only the body text
changed) was acked `08 b3 01 a0 06 00` and still did not display -- the
watch kept showing the same stale cached banner. A populated `pageName` is
therefore **not** what makes the watch display a notification, and neither
is bind state (see "Binding" below, where 16/19 were queried in the same
session and both came back healthy). The ack-vs-display gap is unexplained;
the remaining documented difference between this tool and the phone is the
link itself -- the phone's LE link is bonded and encrypted and it holds a
classic HFP link, this tool's is neither (see "Link security" below).

An earlier note claimed the watch draws its body line from tickerText and
falls back to the title; that was an artifact of the stale-banner session
above, and is retracted — the field mapping in the table above supersedes
it.

## Event reminders: `SET_EVENT_INFO_LIST` (212) / `GET_EVENT_INFO_LIST` (211)

Captured live 2026-08-31 from the official app (`controlbletools ->
setEventInfoList()/getEventInfoList()`), cross-checked byte-for-byte against
the HCI snoop of the same session. **This is the only confirmed way to get
arbitrary phone-authored text to render on this watch's screen** — see the
caveat at the end.

Envelope field is **15** (`0x7a`, `SEEvent`), not the 13 that the
notification commands use. Inside it, field 2 is the list message:

```
SEEventInfoList {
  1: repeated EventInfo   # the whole list, resent in full on every set
  2: support_max_events   # read-only, watch-reported; 5 on this model
}
EventInfo {
  1: description  # string, the text the watch displays
  2: time { 1:year 2:month 3:day 4:hour 5:minute 6:second }   # same submessage as everywhere else
  3: is_finish    # bool, request-only; the watch's echo omits it
}
```

`SET_EVENT_INFO_LIST` (212) request — two events, "vgvg" at 07:09 and "hgv"
at 07:29 on 2026-08-31:

```
08 d4 01 7a 33 12 31
  0a 17 0a 04 76 67 76 67  12 0d 08 ea 0f 10 08 18 1f 20 07 28 09 30 00  18 00
  0a 16 0a 03 68 67 76     12 0d 08 ea 0f 10 08 18 1f 20 07 28 1d 30 00  18 00
```

Response is the plain ack `08 d4 01 a0 06 00` (`{1: 212, 100: 0}`).

`GET_EVENT_INFO_LIST` (211) request is the bare `08 d3 01`; the response
echoes the stored list plus the cap, with field 3 (`is_finish`) stripped:

```
08 d3 01 7a 31 12 2f
  0a 15 0a 04 76 67 76 67 12 0d 08 ea 0f 10 08 18 1f 20 07 28 09 30 00
  0a 14 0a 03 68 67 76    12 0d 08 ea 0f 10 08 18 1f 20 07 28 1d 30 00
  10 05
```

With no events stored the response is `08 d3 01 7a 04 12 02 10 05`, i.e.
just `support_max_events: 5`.

Semantics, as observed: 212 replaces the entire list (there is no add/delete
command — to remove an event, resend the list without it). The watch then
fires each event **locally, from its own clock**, at the stored
minute — in the capture, the 07:29 event displayed its text on the watch at
07:29 with **no BLE or classic-BT traffic at all in that second**. So this
is a scheduled local reminder, not a push: it gets text onto the screen, but
only at a minute-granular pre-programmed time, and only 5 at a time. It does
not answer the open `SEND_SYSTEM_NOTIFICATION` (178) display question.

## The watch holds a classic-BT HFP link while the app is connected

Also from the 2026-08-31 capture, and relevant to the 178-display question:
the watch (`d6:45:15:30:04:71`, the "…Calling_0471" model) opens a **BR/EDR
ACL** to the phone (`HCI Connection Complete`, handle 0x8) and then a full
**Hands-Free Profile** session over RFCOMM — the watch is the HF, the phone
the AG: SDP queries for `0x111e` (Handsfree) followed by `AT+BRSF=255`,
`AT+CIND=?`, `AT+CMER`, `AT+CHLD`, `AT+COPS` and periodic `+CIEV:` indicator
pushes from the phone. This is a live, ordinary HFP link running alongside
the BLE GATT session for the entire capture.

No message-access (MAP/MNS) channel is opened, and no notification text
crosses the classic link — so classic BT is not *carrying* notification
content here. But its mere presence is what a pure-BLE Linux client lacks,
and matches the standing hypothesis in `TODO.md` that this model gates
notification *display* on having a classic link.

## Binding: `BINDING_CHECK` (17) and `BINDING_RESULT` (18)

Captured live 2026-08-30, from the official app's own BLE debug log while
re-binding the watch after the user-id-mismatch state (see JOURNAL.md's
2026-08-30 entry). Field numbers verified against the decompiled
protobuf classes (`BindAccountProtos.SEBindAccount`: bindCheck = 2,
bindResult = 3; `SEBindCheck`: bindCheckResult = 1, deviceVerify = 2,
bindRandomKey = 3; `SEBindResult`: bindResultType = 1, userId = 2,
phoneType = 3, and enums `SEBindResultType` REFUSE=1/OVER_TIME=2, so
SUCCESS=0 by elimination, and `SEPhoneType` ANDROID=0/IOS=1).

Bind sequence the real app runs:

1. `INQUIRY_BINDING_STATUS` (16) → `request_binding_status: false` means
   the watch considers itself unbound.
2. `BINDING_CHECK` (17): `08 11 1a 04 12 02 08 01`
   = `{1: 17, 3: {2: {2: {1: 1}}}}` — `SEBindAccount.bindCheck{
   deviceVerify: true}`. (The smali overload with `deviceVerify=false`
   instead sets `bindRandomKey`, used for the verify-by-key path.)
3. `BINDING_RESULT` (18): `08 12 1a 0f 1a 0d 08 00 12 07 "2011999" 18 00`
   = `{1: 18, 3: {3: {1: 0, 2: "2011999", 3: 0}}}` —
   `SEBindAccount.bindResult{bindResultType: SUCCESS, userId,
   phoneType: ANDROID}`.
4. `INQUIRY_BINDING_STATUS` (16) → now `request_binding_status: true`.
5. `VERIFY_USER_NUMBER` (19) → `verify_result_type: true`.

`VERIFY_USER_NUMBER`'s reply `3a 04 08 00 10 01` decodes to
`{7: {1: 0, 2: 1}}` = `verifyResult{verify_result_type: false,
binding_status: true}` (SEBindAccount.verifyResult = field 7,
SEVerifyResult: verifyResultType = 1, bindingStatus = 2). While
`verify_result_type` is false the watch is in the user-id-mismatch state.

**A healthy bind is not sufficient for display** (measured 2026-08-31,
BLE-only from this tool, minutes after a 179 that acked and did not
display):

- `INQUIRY_BINDING_STATUS` (16) → `08 10 1a 02 08 01` =
  `{1: 16, 3: {1: 1}}`, `request_binding_status: true`.
- `VERIFY_USER_NUMBER` (19) → `08 13 1a 06 3a 04 08 01 10 01` =
  `{1: 19, 3: {7: {1: 1, 2: 1}}}` = `verifyResult{verify_result_type: true,
  binding_status: true}` — note the `08 01`, the opposite of the
  mismatch reply's `08 00` above.

So the bind is intact and verified, and the watch still acked a 179 it did
not show. Bind state alone does not gate display.

**Not yet live-tested from this tool**: sending 17/18 ourselves to repair
a broken bind over BLE-only (the 2026-08-30 attempt was blocked by the
host's adapter going away mid-session). The exact bytes above are
reproducible with `protocol.encode_field_varint`/`encode_field_bytes`;
dedicated encoders are a natural next addition once tested.

## Link security: no application-layer crypto; encryption is the peer's choice

There is **no application-layer cryptography anywhere in this protocol**: no
secret key, no nonce exchange, no HMAC, no AES-CCM. Every payload documented
above is plain protobuf. Confidentiality, if any, comes entirely from BLE
link-layer encryption, which is a property of the *peer*, not of the watch's
protocol:

- **This repo's Linux client**: connects and exchanges the full protobuf
  protocol — reads fitness data, writes notifications — with **no pairing
  and no encryption at all**. The watch does not require an encrypted link
  for the `1618` service's characteristics.
- **The official Android app** (`btsnoop`, 2026-08-31 06:28): pairs with
  **LE Secure Connections** (`Pairing Public Key` / `DHKey Check` exchange,
  i.e. ECDH P-256, max key size 16) and the LE link is then encrypted —
  `HCI Encryption Change status=0 enabled=1` on the LE ACL handle, 12
  seconds before the first protobuf byte. The classic HFP link is encrypted
  too (`Authentication Complete`, then `Encryption Change` on the BR/EDR
  handle).

  The association model is **Just Works**, and therefore unauthenticated:
  initiator IO capability `0x04` (KeyboardDisplay), responder `0x03`
  (NoInputNoOutput), and the responder clears the MITM bit in its AuthReq
  (`0x29`; the initiator sets it, `0x2d`). Both sides set the SC bit.

  There is also a short unencrypted window at the start of each connection —
  ATT traffic begins ~12 s before `Encryption Change` — but in the capture it
  carries only GATT discovery, no protocol payload.

An earlier revision of this section claimed "zero SMP packets ... unpaired
and unencrypted throughout", generalising from the 2026-08-29 capture. That
is **retracted**: it was true of that session's link state, not of the
protocol. Note also that an Android `btsnoop_hci.log` records HCI traffic,
which is *above* the controller's link-layer encryption — plaintext in a
snoop log is never evidence of plaintext over the air.

## What isn't known yet

- Characteristics `6f04`, `6f05` — present in the GATT table, never seen
  carrying traffic in either capture. `DATA_UPLOAD`'s name in `protocol.py`
  is a guess based on position, not a confirmed role (`6f03`'s neighboring
  guess, `ACTIVITY_DATA`, turned out to be accurate -- see "Workout data"
  above -- so `6f04`/`6f05` guesses are at least plausible, not baseless).
- `RealTimeData`'s hourly arrays — every captured example has been all-zero,
  so their per-bucket width and byte order are still unchecked.
  `DailyData`'s (2-byte big-endian) and `ContinuousHeartRate`'s (1-byte)
  arrays are both settled against non-zero captures, see "Fitness data".
- The calorie unit in `DailyData` (kcal is the obvious guess, unverified).
- `SleepData` field 16 (`sleep_readiness_score`, a float in the app's class)
  and the `DAYTIME_SLEEP` sleep type — never sent by this model so far.
- `RealTimeData` fields 8, 9, and 13 (varints, always 0 so far) and field 10
  (a physiologicalCycle submessage, not parsed) — present but unidentified.
- Command ids other than the ones documented above have request encodings
  that follow the same `08 <varint id>` pattern but undecoded response
  schemas (see the command id table).
- Chunking behavior for a payload spanning more than one data chunk (every
  capture so far fit in a single chunk) — the split points, and whether
  there's a negotiated MTU step before chunk size matters, are unconfirmed.
- Whether any command exists that performs a destructive write (e.g.
  factory reset, firmware update) — not investigated, and out of scope for
  this read-mostly tool regardless.
- ~~`ControlBleTools.sendAppNotification(...)`~~ — traced and implemented
  2026-08-30 as `SEND_APP_NOTIFICATION` (179), see "App push notification"
  above.
- The "Berry" protocol branch (`ControlBleTools.isBerryProtocol(...)`,
  `com.zhapp.ble.a$a`, classes like `WearSocketMessageData`) — a sibling
  code path in the same SDK for other watch models. Not relevant to this
  watch (confirmed "Apricot" via a live log line: `protocol = Apricot`
  during connect), not investigated further.
