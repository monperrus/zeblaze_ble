# Zeblaze Beyond 3 Pro protocol

This document describes the Apricot protocol used by the Zeblaze Beyond 3
Pro. It is a wire-format reference for implementations.

## GATT service

Service UUID: `16186f00-0000-1000-8000-00807f9b34fb`.

All five characteristics support `WRITE_NO_RESPONSE | NOTIFY`:

| UUID | Name | Value handle | CCCD | Role |
| --- | --- | ---: | ---: | --- |
| `16186f01-0000-1000-8000-00807f9b34fb` | `COMMAND_READ` | `0x0021` | `0x0022` | Command responses and watch-originated messages |
| `16186f02-0000-1000-8000-00807f9b34fb` | `COMMAND_WRITE` | `0x0024` | `0x0025` | Host commands and transport acknowledgements |
| `16186f03-0000-1000-8000-00807f9b34fb` | `ACTIVITY_DATA` | `0x0027` | `0x0028` | Bulk workout data |
| `16186f04-0000-1000-8000-00807f9b34fb` | `LARGE_FILE_DATA` | `0x002a` | `0x002b` | Binary large-file transfer, including OTA firmware, watch faces, and AGPS/LTO data |
| `16186f05-0000-1000-8000-00807f9b34fb` | `VOICE_DATA` | `0x002d` | `0x002e` | Voice-assistant/Alexa data |

Writing `01 00` to a characteristic's Client Characteristic Configuration
Descriptor (CCCD) enables notifications from that characteristic for the
current connection. `6f02` notifications carry transport acknowledgements;
`6f01` notifications carry command responses and watch-originated messages.
The other three subscriptions are needed only for traffic assigned to their
respective channels. These handles apply to firmware `1.1.2`.

## Session initialization and MTU

Application binding requires ATT MTU 247 and a matching SDK-level MTU
exchange:

1. Connect over LE.
2. Complete ATT Exchange MTU with MTU 247.
3. Enable at least the `6f01` and `6f02` notification CCCDs for bidirectional
   command transport.
4. Send command 0:

```text
-> 08 00 9a 06 09 08 f7 01 10 0c 18 0c 20 00
<- 08 00 10 f7 01
```

Command 0 has this protobuf shape:

```text
{
  1: 0,
  99: {
    1: 247,  # negotiated ATT MTU
    2: 12,   # minimum chunk setting
    3: 12,   # maximum chunk setting
    4: 0     # mode
  }
}
```

The response field 2 contains the active ATT MTU. Implemented by
`protocol.encode_mtu_request_change()` and
`GatttoolSession(..., att_mtu=247)`.

## Chunked message transport

Every protobuf message uses the same application-layer handshake:

```text
originator                         recipient
    |--- 00 00 00 00 NN NN ---------->|  header: chunk count, uint16 LE
    |<-- 00 00 01 01 00 00 -----------|  ready acknowledgement
    |--- II 00 <payload> -------------->|  one frame per chunk
    |                 ...               |
    |<-- 00 00 01 00 00 00 -----------|  complete acknowledgement
```

- Chunk indexes are one-based.
- Reassemble payloads in index order.
- Frames use ATT Write Command and Notification. The ready and complete
  frames are protocol acknowledgements, not ATT responses.
- The Linux transport uses payload chunks of at most 180 bytes.
- `protocol.header_frame`, `data_chunk`, `split_data_chunk`,
  `is_ready_ack`, and `is_complete_ack` implement the framing.

Host commands are written to `6f02`; their protobuf responses arrive on
`6f01`. The same handshake is reversed for watch-originated messages.

Example request for `GET_DEVICE_INFO`:

```text
host -> 6f02  00 00 00 00 01 00
host <- 6f02  00 00 01 01 00 00
host -> 6f02  01 00 08 20
host <- 6f02  00 00 01 00 00 00

host <- 6f01  00 00 00 00 01 00
host -> 6f01  00 00 01 01 00 00
host <- 6f01  01 00 08 20 ...
host -> 6f01  00 00 01 00 00 00
```

## Protobuf envelope

Messages use protobuf varints and length-delimited fields. Field 1 is the
command identifier:

```text
{1: command_id, ...command-specific fields...}
```

Commands with no request data contain field 1 alone. Command 32, for example,
is `08 20`. Identifiers above 127 use normal protobuf varint encoding.

Generic success response:

```text
{1: command_id, 100: 0}
```

Command 179, for example, succeeds with `08 b3 01 a0 06 00`.

## Command identifiers

| Decimal | Varint bytes | Name |
| ---: | --- | --- |
| 0 | `00` | `MTU_REQUEST_CHANGE` |
| 16 | `10` | `INQUIRY_BINDING_STATUS` |
| 17 | `11` | `BINDING_CHECK` |
| 18 | `12` | `BINDING_RESULT` |
| 19 | `13` | `VERIFY_USER_NUMBER` |
| 23 | `17` | `UNBIND_REQUEST` |
| 25 | `19` | `INQUIRY_CLASSIC_BLUETOOTH_CONNECT_STATUS` |
| 27 | `1b` | `REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS` |
| 32 | `20` | `GET_DEVICE_INFO` |
| 33 | `21` | `GET_DEVICE_BATTERY` |
| 48 | `30` | `SET_SYSTEM_TIME` |
| 49 | `31` | `SET_12_24_TIME_TYPE` |
| 65 | `41` | `GET_LANGUAGE_DETAILED` |
| 69 | `45` | `SET_USER_INFORMATION` |
| 112 | `70` | `GET_FITNESS_TYPE_ID_LIST` |
| 113 | `71` | `REQUEST_FITNESS_TYPE_ID` |
| 115 | `73` | `CONFIRM_FITNESS_TYPE_ID` |
| 117 | `75` | `GET_FITNESS_SPORT_ID_LIST` |
| 119 | `77` | `REQUEST_FITNESS_SPORT_DATA` |
| 121 | `79` | `CONFIRM_FITNESS_SPORT_ID_LIST` |
| 164 | `a4 01` | `REAL_TIME_DATA_SWITCH` |
| 165 | `a5 01` | `REPORT_BASIC_DATA` |
| 178 | `b2 01` | `SEND_SYSTEM_NOTIFICATION` |
| 179 | `b3 01` | `SEND_APP_NOTIFICATION` |
| 211 | `d3 01` | `GET_EVENT_INFO_LIST` |
| 212 | `d4 01` | `SET_EVENT_INFO_LIST` |
| 214 | `d6 01` | `GET_HEART_RATE_MONITOR` |
| 215 | `d7 01` | `SET_HEART_RATE_MONITOR` |
| 247 | `f7 01` | `GET_SCREEN_SETTING` |
| 249 | `f9 01` | `REQUEST_SCREEN_SETTING` |
| 480 | `e0 03` | `GET_CLASSIC_BLUETOOTH_STATE` |
| 495 | `ef 03` | `GET_NOTIFICATION_SETTINGS` |

## Application binding

Binding associates the watch with an application user identifier. The user
identifier is an ASCII decimal string, not a cryptographic credential.

### Binding sequence

The minimal binding sequence uses one uninterrupted BLE session:

1. Negotiate ATT MTU 247 and send command 0.
2. Send command 17 and receive the watch identity.
3. Send command 18 with bind result `SUCCESS`, user ID, and phone type.
4. Receive and acknowledge any watch-originated messages, notably command 27.
5. Query command 16; binding is complete when it reports true.
6. Send command 48 to synchronize the clock and UTC offset.

Commands 48, 65, 49, and 164 are post-bind device initialization, not
binding prerequisites. The CLI sends command 48 after a successful bind to
synchronize the clock. Command 27 and command 19 are not required to commit
the binding. However, command 27 must be received and transport-acknowledged
when emitted; leaving it pending blocks later protocol exchanges. Command 19
may be used for the stronger check that the stored user identifier matches.

### Binding status: command 16

```text
-> 08 10
<- 08 10 1a 02 08 00  # {1:16, 3:{1:false}} unbound
<- 08 10 1a 02 08 01  # {1:16, 3:{1:true}}  bound
```

### Binding check: command 17

Request:

```text
08 11 1a 04 12 02 08 01
```

Shape:

```text
{1:17, 3:{2:{2:{1:true}}}}
```

The response contains the bind-check result, device verification state,
equipment number, BLE MAC address, serial number, firmware version, and
device name. `protocol.encode_binding_check_request()` builds the request.

### Binding result: command 18

Example for Android user `2011999`:

```text
08 12 1a 0f 1a 0d 08 00 12 07 32 30 31 31 39 39 39 18 00
```

Shape:

```text
{
  1:18,
  3:{
    3:{
      1:0,          # SUCCESS
      2:"2011999", # user ID
      3:0           # ANDROID; IOS is 1
    }
  }
}
```

The immediate response is generic success. Binding is complete when command
16 reports true; command 19 can additionally verify the user identifier.
`protocol.encode_binding_result_request(user_id)` builds the request.

### Initialization commands

Command 48 sets the system time:

```text
{
  1:48,
  5:{
    1:{
      1:unix_timestamp_seconds,
      2:utc_offset_in_quarter_hours  # protobuf sint32
    }
  }
}
```

The logical offset uses four units per hour: CEST (`UTC+02:00`) is `+8`.
Because this is a protobuf `sint32`, zigzag encoding puts the unsigned varint
value 16 on the wire. The standard clock-sync call
omits the optional time-format field and thus does not alter the 12/24-hour
preference. `protocol.encode_set_system_time_request()` implements this
shape. Command 49 sets the 12/24-hour selection. Command 65 requests the
supported language list. Command 164 enables real-time reports:

```text
08 a4 01 62 02 18 00
```

The watch answers command 164 by pushing command 165.

### Binding completion event: command 27

Command 27 is watch-originated and carries classic-radio state:

```text
08 1b 1a 19 42 17 08 00 10 01 1a 11 "D6:45:15:30:04:71"
```

Do not synthesize command 27 from the host. Receive and acknowledge it using
the normal chunked transport when it is emitted. The watch waits for that
acknowledgement before serving later application messages, so clients must
not discard notifications received on another characteristic while awaiting
a command ACK.

### User verification: command 19

Request for user `2011999`:

```text
08 13 1a 09 32 07 32 30 31 31 39 39 39
```

Shape: `{1:19, 3:{6:"2011999"}}`.

Verified and bound response:

```text
08 13 1a 06 3a 04 08 01 10 01
```

The nested fields are `{verify_result_type:true, binding_status:true}`.

## Unbinding

Command 23 clears the application binding:

```text
-> 08 17
<- 08 17 a0 06 00
```

This operation is destructive. Disconnecting BLE or removing an operating
system bond is separate from protocol-level unbinding.

## Classic Bluetooth status

Commands 25 and 27 carry `SEClassicBluetoothStatus` in envelope field 8:

| Field | Meaning |
| ---: | --- |
| 1 | Classic link connected |
| 2 | Classic radio enabled |
| 3 | Watch classic MAC address |

Command 25 queries the state:

```text
-> 08 19
<- 08 19 1a 19 42 17 08 00 10 01 1a 11 "D6:45:15:30:04:71"
```

Command 27 is the watch-originated form. A false first field and true second
field means that the classic radio is enabled with no classic link connected.

Classic Bluetooth and HFP are not required for command 179 after application
binding. Notification content travels over BLE.

## Device information

Command 32 request: `08 20`.

Response shape:

```text
{
  1:32,
  4:{
    1:{
      1: firmware_version,
      2: equipment_number,
      3: mac,
      4: serial_number,
      5:{1:battery_percent, 2:charge_status},
      6: remote_camera_switch,
      7: sports_icon_protocol_switch
    }
  }
}
```

`protocol.parse_device_info()` decodes this structure.

## App notifications: command 179

```text
{
  1:179,
  13:{
    2:{
      1: appName,
      2: pageName,
      3: title,
      4: text,
      5: tickerText
    }
  }
}
```

Example:

```text
08 b3 01 6a 5f 12 5d
  0a 05 "Gmail"
  12 15 "com.google.android.gm"
  1a 10 "Martin Monperrus"
  22 19 "hello martin how are you?"
  2a 10 "Martin Monperrus"
```

Field semantics:

| Wire field | CLI option | Meaning |
| --- | --- | --- |
| `appName` | `--app` | Display label, such as `Gmail` |
| `pageName` | `--page` | Android package name, such as `com.google.android.gm` |
| `title` | `--sender` | Notification title/sender line |
| `text` | `--text` | Body text |
| `tickerText` | `--ticker` | Short summary; defaults to `--sender` |

`pageName` must not be empty and should correspond to `appName`. Title and
ticker text are capped at 50 characters; body text is capped at 200. The
encoder shortens overlong values using `value[:limit-1] + "..."`.

Success response:

```text
08 b3 01 a0 06 00
```

The watch displays the fields when application binding is valid. No Classic
Bluetooth or HFP connection is required.

## System notifications: command 178

```text
{
  1:178,
  13:{
    1:{
      1:type,
      2:phone_number,
      3:contacts_info,
      4:message_text
    }
  }
}
```

Types:

| Value | Meaning |
| ---: | --- |
| 0 | Call |
| 1 | Missed call |
| 2 | Message |

`protocol.encode_system_notification_request()` implements this shape.
With a valid application binding and ATT MTU 247, all three types display on
the watch and return generic success:

- Type 0 displays the incoming-call UI using `phone_number` and
  `contacts_info`. The official encoder sends an empty `message_text`.
- Type 1 displays a missed-call alert.
- Type 2 displays `contacts_info` and `message_text` as a transient message
  that disappears automatically.

Multiple command-178 messages may be sent consecutively in one BLE session.
As with every command, watch-originated messages must be transport-acknowledged;
an unacknowledged command 27 can otherwise block the channel and make later
command headers appear to be refused.

Command 178 supplies notification UI and metadata over BLE. Classic Bluetooth
HFP is a separate path expected to carry real call control and audio; the
watch-button signaling for accepting or rejecting calls is not yet decoded.

## Event reminders

Commands 211 and 212 use envelope field 15:

```text
SEEventInfoList {
  1: repeated EventInfo
  2: support_max_events
}

EventInfo {
  1: description
  2: time {1:year, 2:month, 3:day, 4:hour, 5:minute, 6:second}
  3: is_finish
}
```

Command 212 replaces the complete reminder list and returns generic success.
Command 211 returns the stored list and maximum list size. This watch reports
a maximum of five reminders. Reminders fire locally from the watch clock at
minute granularity.

## Fitness data

### Time submessage

Fitness selectors use:

```text
{1:year, 2:month, 3:day, 4:hour, 5:minute, 6:second}
```

Implemented by `protocol.encode_time()`.

### Available buckets: command 112

Request: `08 70`.

Response:

```text
{1:112, 9:{2:{1:[{1:time, 2:function_type}, ...]}}}
```

Known function types:

| Type | Data |
| ---: | --- |
| 0 | Daily steps, distance, calories |
| 1 | Sleep |
| 2 | Continuous heart rate |
| 11 | Effective standing |
| 12 | Activity duration |

### Request and confirm: commands 113 and 115

Both use:

```text
{1:command_id, 9:{1:{1:time, 2:function_type}}}
```

Command 113 returns a function-specific bean. Command 115 confirms successful
processing and receives generic success.

| Function type | Response field inside field 9 | Parser |
| ---: | ---: | --- |
| 0 | 4 | `parse_daily_data` |
| 1 | 5 | `parse_sleep_data` |
| 2 | 6 | `parse_continuous_heart_rate` |
| 11 | 14 | `parse_effective_standing` |
| 12 | 15 | `parse_activity_duration` |

### Daily data

```text
{
  1: selector echo,
  2: steps_frequency_minutes,
  3: steps_raw,
  4: distance_frequency_minutes,
  5: distance_raw,
  6: calorie_frequency_minutes,
  7: calorie_raw
}
```

Each raw array contains big-endian unsigned 16-bit buckets. With a frequency
of 60 minutes, each array contains 24 buckets. Distance is in metres.

### Continuous heart rate

```text
{
  1: selector echo,
  2: frequency_minutes,
  3: heart_rate_raw,
  4: max_value,
  5: min_value,
  6: resting_value,
  7: hour_max_raw,
  8: hour_min_raw
}
```

Heart-rate buckets are one unsigned byte each. A zero bucket means no sample.
The bucket count must equal `1440 / frequency_minutes`.

### Sleep

```text
{
  1: selector echo,
  2: start_sleep_timestamp,
  3: end_sleep_timestamp,
  4: sleep_duration,
  5: sleep_score,
  6: awake_time,
  7: awake_percentage,
  8: light_sleep_time,
  9: light_sleep_percentage,
  10: deep_sleep_time,
  11: deep_sleep_percentage,
  12: rem_time,
  13: rem_percentage,
  14:{1:[stage, ...]},
  15: sleep_type,
  16: sleep_readiness_score
}
```

Stage entries are `{1:start_timestamp, 2:duration_minutes, 3:stage_type}`.
Stage types are 0 awake, 1 light, 2 deep, and 3 REM. The final zero-duration
awake entry marks wake-up. Sleep type 1 is night sleep; 0 is daytime sleep.

### Effective standing

```text
{1:selector echo, 2:frequency_minutes, 3:raw}
```

The raw value contains one byte per bucket.

### Activity duration

Fields 1 through 5 contain selector echo, frequency, raw buckets, daily time,
and daily percentage. Fields 6 through 31 are time/percentage pairs for the
sport categories defined by the SDK.

## Real-time data

Command 164 enables or disables reports:

```text
{1:164, 12:{3:<0-or-1>}}
```

`protocol.encode_real_time_data_switch_request()` builds the request. The
watch pushes command 165 with steps, calories, distance, heart rate, battery
state, and other current fields. `protocol.parse_real_time_data()` decodes
the supported fields.

## Workout data

Workout transfer uses commands 117, 119, and 121.

### Entry list: command 117

Request: `08 75`.

The response contains opaque entry identifiers. The low two bits of the last
identifier byte select the component:

| Value | Component |
| ---: | --- |
| 0 | Point/sample data |
| 1 | Workout report |
| 2 | GPS data |

### Fetch data: command 119

```text
{1:119, 9:{3:<concatenated entry identifiers>}}
```

Bulk data may arrive on `6f03` or `6f01` in multiple chunked rounds. The
transport has no explicit end marker. Collect rounds until a grace interval
expires, concatenate their payloads, then split the result using identifiers
and component headers.

### Confirm list: command 121

Command 121 uses the same selector shape as command 119. Send it only after
every requested component has been received and parsed successfully.

### Components

- The report component is decoded by `protocol.parse_workout_report()` and
  contains sport type, start/end time, duration, calories, distance, steps,
  heart-rate summaries, training effect, recovery time, cadence, speed, and
  stride-related fields.
- The GPS component contains fixed-width points with timestamp, latitude,
  and longitude. Coordinates are signed integers scaled by `1e7`.
  `protocol.parse_gps_points()` performs the conversion.
- The point/sample component header is recognized, but its sample payload
  schema is unknown.

## Screen settings

Command 247 returns:

```text
{
  15:{
    14:{
      1:brightness_level,
      2:normally_on_switch,
      3:on_screen_duration,
      4:double_click_highlighted_screen
    }
  }
}
```

Command 249 is a watch-originated request to refresh screen settings.

## Heart-rate monitor settings

Commands 214 (`GET_HEART_RATE_MONITOR`) and 215 (`SET_HEART_RATE_MONITOR`)
read and write the app's "Heart Rate Monitor" setting screen, including its
"Continuous Heart Monitoring" toggle. This section's shape and field numbers
were first read directly from the official ZH_SDK Android SDK
(`ZH_SDK_20250808_V2.2.0.aar`, obtained from
[jagatheeswaran-noise/noise-ai-ble-smartwatch](https://github.com/jagatheeswaran-noise/noise-ai-ble-smartwatch),
decompiled with jadx): `com.zhapp.ble.ControlBleTools#getHeartRateMonitor`/
`#setHeartRateMonitor`, its private encoder `a#a(int, HeartRateMonitorBean)`,
and the generated `com.zh.ble.wear.protobuf.{WearProtos,SettingMenuProtos}`
field-number constants. Both commands are now confirmed against a live
Beyond 3 Pro (2026-09-06): 214 returned the bean under the nesting below, and
215 changed the setting, the change surviving a fresh 214 read on a new
connection.

Command 214 takes no payload (`08 d6 01`). Command 215's request:

```text
{
  1:215,
  15:{                                # SESettingMenu (envelope field 15)
    3:{                               # SEHeartRateMonitor
      1: mode,                        # SEMode: bean value 0 -> AUTO, nonzero -> OFF (inverted on the wire)
      2: frequency_minutes,
      3: warning,                     # bool
      4: warning_value,
      5: sport_warning,               # bool
      6: sport_warning_value,
      7: continuous_heart_rate_mode   # SEContinuousHeartRateMode: 0 ALL_DAY_HEART_RATE, 1 INTELLIGENT_HEART_RATE
    }
  }
}
```

`continuous_heart_rate_mode` is the wire representation of the app's
"Continuous Heart Monitoring" toggle: `ALL_DAY_HEART_RATE` (0) samples on a
fixed `frequency_minutes` clock all day; `INTELLIGENT_HEART_RATE` (1) samples
sparsely, triggered by movement -- the likely explanation for
`ContinuousHeartRate.frequency_minutes` reading 5 while sampled data was
mostly zero buckets (see "Continuous heart rate" above and `../../TODO.md`).
Command 215 is a full-replace write, not a patch on individual fields, so a
client should read back current settings with command 214 first and only
change the field(s) it means to change.

Command 214's response uses the same field-15 -> field-3 nesting as the
command 215 request above. It omits zero-valued fields the way proto3
does, so a reply can stop short of field 7 (`08 d6 01 7a 0a 1a 08 08 00 10
05 18 00 20 00` is a real one: mode 0, frequency 5, everything else
default). Field 8 (`low_warning_value`) has not been seen.
Command 215's own response is a bare
`{1:215}` ack with no field 15, so a client that wants to see what was
stored must re-read with 214 rather than parse the reply. Observed live: a
watch with monitoring off answers 214 with `mode` 1 and
`continuous_heart_rate_mode` 0; after a 215 write of `mode` 0 the next 214
read returns `mode` 0. The watch also zeroed `warning_value` (130 -> 0) on
that write, with `warning` false in both the read-back and the written
request -- so `warning_value` appears to be kept only while `warning` is
set.
`protocol.encode_set_heart_rate_monitor_request()` and
`protocol.parse_heart_rate_monitor_response()` implement this shape;
`zeblaze-ble hrmonitor`/`hrmonitor-set` expose it on the CLI.

## Link security

The Apricot protocol has no application-layer encryption, nonce, MAC, or
signature. Messages are plain protobuf inside the chunked transport.

The GATT characteristics accept an unencrypted LE connection. BLE link-layer
encryption may be enabled independently when the peers have a usable bond; it
does not change protobuf or chunk framing. Application binding and
notification delivery do not require BLE encryption, Classic Bluetooth, or
HFP.

## Unknown fields and commands

- The calorie unit in daily fitness data is unknown.
- Several real-time report fields and hourly arrays are not decoded.
- The workout point/sample component is not decoded.
- Commands not described above may use the same outer command envelope but
  have unknown schemas.
- The Berry protocol used by other watch models is outside this document.
