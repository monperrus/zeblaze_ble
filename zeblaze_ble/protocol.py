"""GATT identifiers and wire framing for the watch's own "ZH_SDK" protocol.

This is an unencrypted, unauthenticated proprietary command protocol, not a
generic vendor-family scheme. A live capture of the real com.zhapp.zeblazefit
app (2026-08-29, see ../../NOTES.md and ../../android-observations.md) showed
zero SMP packets and zero LE-encryption events on the wire: the app talks to
the watch over an unpaired, unencrypted BLE link using a small chunked
transport, with commands identified by a single numeric id (e.g. 32 =
GET_DEVICE_INFO, which bundles battery status). No secret key, nonce, HMAC or
AES-CCM is involved anywhere in that capture. Full protocol writeup: see
README.md in this directory.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

ZH_SDK_SERVICE = "16186f00-0000-1000-8000-00807f9b34fb"
COMMAND_READ = "16186f01-0000-1000-8000-00807f9b34fb"
COMMAND_WRITE = "16186f02-0000-1000-8000-00807f9b34fb"
ACTIVITY_DATA = "16186f03-0000-1000-8000-00807f9b34fb"
LARGE_FILE_DATA = "16186f04-0000-1000-8000-00807f9b34fb"
VOICE_DATA = "16186f05-0000-1000-8000-00807f9b34fb"
# Backward-compatible names used by early captures of these channels.
DATA_UPLOAD = LARGE_FILE_DATA
CHANNEL_6F05 = VOICE_DATA

NOTIFICATION_CHANNELS = (COMMAND_READ, COMMAND_WRITE, ACTIVITY_DATA, LARGE_FILE_DATA, VOICE_DATA)

# Command ids observed in a live capture of the official app (BLE_2026-08-29.zh).
CMD_MTU_REQUEST_CHANGE = 0
CMD_INQUIRY_BINDING_STATUS = 16
CMD_BINDING_CHECK = 17
CMD_BINDING_RESULT = 18
CMD_VERIFY_USER_NUMBER = 19
CMD_UNBIND_REQUEST = 23

# Watch-originated only: the watch announces its classic (BR/EDR) radio state
# a moment after every connection, and waits for the transport ack before
# serving anything else. Never sent by us -- see protocol.md's
# "Classic-radio status announcement".
CMD_REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS = 27
CMD_GET_DEVICE_INFO = 32
CMD_GET_DEVICE_BATTERY = 33
CMD_SET_SYSTEM_TIME = 48
CMD_GET_LANGUAGE_DETAILED = 65
CMD_GET_FITNESS_TYPE_ID_LIST = 112
CMD_REQUEST_FITNESS_TYPE_ID = 113
CMD_CONFIRM_FITNESS_TYPE_ID = 115
CMD_GET_FITNESS_SPORT_ID_LIST = 117
CMD_REQUEST_FITNESS_SPORT_DATA = 119
CMD_CONFIRM_FITNESS_SPORT_ID_LIST = 121
CMD_REAL_TIME_DATA_SWITCH = 164
CMD_REPORT_BASIC_DATA = 165  # watch-initiated push, never sent by us
CMD_SEND_SYSTEM_NOTIFICATION = 178

# SESystemNotification.type enum values (com.zh.ble.wear.protobuf.NotificationProtos).
NOTIFICATION_TYPE_CALL = 0
NOTIFICATION_TYPE_MISS_CALL = 1
NOTIFICATION_TYPE_MESSAGE = 2

# SEND_APP_NOTIFICATION (179), the "Apricot"-protocol branch of
# ControlBleTools.sendAppNotification -- see encode_app_notification_request.
CMD_SEND_APP_NOTIFICATION = 179

# GET/SET_HEART_RATE_MONITOR -- command ids, envelope nesting (field 15 ->
# SESettingMenu field 3 -> SEHeartRateMonitor fields 1-7), and enum values
# below are all confirmed from the official ZH_SDK Android AAR (v2.2.0,
# `ZH_SDK_20250808_V2.2.0.aar`, decompiled `com.zhapp.ble.ControlBleTools` /
# `a` and `com.zh.ble.wear.protobuf.{WearProtos,SettingMenuProtos}`), not
# from a live capture. Both are now confirmed live against a Beyond 3 Pro
# (2026-09-06): 214 returns the bean under that nesting, 215 changes the
# setting and answers with a bare `{1:215}` ack -- read back with 214.
CMD_GET_HEART_RATE_MONITOR = 214
CMD_SET_HEART_RATE_MONITOR = 215

# SEHeartRateMonitor.SEMode -- inverted on the wire: ControlBleTools' encoder
# maps HeartRateMonitorBean.mode == 0 to SEMode.AUTO (monitor on) and any
# nonzero bean.mode to SEMode.OFF. These constants are that bean-side value,
# not the wire enum ordinal.
HEART_RATE_MONITOR_MODE_AUTO = 0
HEART_RATE_MONITOR_MODE_OFF = 1

# SEHeartRateMonitor.SEContinuousHeartRateMode -- ALL_DAY_HEART_RATE samples
# on a fixed frequency_minutes clock; INTELLIGENT_HEART_RATE samples sparsely,
# triggered by movement. This is the wire representation of the app's
# "Continuous Heart Monitoring" toggle.
CONTINUOUS_HEART_RATE_MODE_ALL_DAY = 0
CONTINUOUS_HEART_RATE_MODE_INTELLIGENT = 1

# BindAccountProtos.SEBindResultType / SEPhoneType values observed in the
# official Android app's successful bind exchange.
BIND_RESULT_SUCCESS = 0
PHONE_TYPE_ANDROID = 0
PHONE_TYPE_IOS = 1

# Field-length caps the official app itself applies before sending an app
# notification (ControlBleTools.sendAppNotification -> BleUtils.truncateString):
# title/ticker capped at 50, text at 200 (result is 49/199 chars + "...").
# appName and pageName are passed through uncapped -- the smali truncates only
# its p3/p4/p5 (title/text/ticker) arguments.
APP_NOTIFICATION_TITLE_MAX = 50
APP_NOTIFICATION_TEXT_MAX = 200

# Human-readable names for every command id this package knows, for logging
# and exploration (see scripts/watch_monitor.py). An id missing from here is
# not necessarily unknown to the watch -- only to us.
COMMAND_NAMES = {
    CMD_MTU_REQUEST_CHANGE: "MTU_REQUEST_CHANGE",
    CMD_INQUIRY_BINDING_STATUS: "INQUIRY_BINDING_STATUS",
    CMD_BINDING_CHECK: "BINDING_CHECK",
    CMD_BINDING_RESULT: "BINDING_RESULT",
    CMD_VERIFY_USER_NUMBER: "VERIFY_USER_NUMBER",
    CMD_UNBIND_REQUEST: "UNBIND_REQUEST",
    CMD_REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS: "REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS",
    CMD_GET_DEVICE_INFO: "GET_DEVICE_INFO",
    CMD_GET_DEVICE_BATTERY: "GET_DEVICE_BATTERY",
    CMD_SET_SYSTEM_TIME: "SET_SYSTEM_TIME",
    CMD_GET_LANGUAGE_DETAILED: "GET_LANGUAGE_DETAILED",
    CMD_GET_FITNESS_TYPE_ID_LIST: "GET_FITNESS_TYPE_ID_LIST",
    CMD_REQUEST_FITNESS_TYPE_ID: "REQUEST_FITNESS_TYPE_ID",
    CMD_CONFIRM_FITNESS_TYPE_ID: "CONFIRM_FITNESS_TYPE_ID",
    CMD_GET_FITNESS_SPORT_ID_LIST: "GET_FITNESS_SPORT_ID_LIST",
    CMD_REQUEST_FITNESS_SPORT_DATA: "REQUEST_FITNESS_SPORT_DATA",
    CMD_CONFIRM_FITNESS_SPORT_ID_LIST: "CONFIRM_FITNESS_SPORT_ID_LIST",
    CMD_REAL_TIME_DATA_SWITCH: "REAL_TIME_DATA_SWITCH",
    CMD_REPORT_BASIC_DATA: "REPORT_BASIC_DATA",
    CMD_SEND_SYSTEM_NOTIFICATION: "SEND_SYSTEM_NOTIFICATION",
    CMD_SEND_APP_NOTIFICATION: "SEND_APP_NOTIFICATION",
    CMD_GET_HEART_RATE_MONITOR: "GET_HEART_RATE_MONITOR",
    CMD_SET_HEART_RATE_MONITOR: "SET_HEART_RATE_MONITOR",
}

# Low 2 bits of a sport-entry id's last byte (see SportEntryId below).
SPORT_DATA_POINT = 0  # per-interval samples, structure not decoded (see protocol.md)
SPORT_DATA_REPORT = 1  # workout summary -> WorkoutReport
SPORT_DATA_GPS = 2  # GPS track -> list[GpsPoint]

# fitness_function_type values observed live (there is no known "GPS"/location
# type among these -- GPS data lives under the separate, unexplored
# GET_FITNESS_SPORT_ID_LIST (117) workout-session mechanism instead).
FITNESS_TYPE_DAILY = 0  # steps/distance/calories -> DailyData
FITNESS_TYPE_SLEEP = 1  # one night's sleep summary + stage timeline -> SleepData
FITNESS_TYPE_CONTINUOUS_HEART_RATE = 2  # -> ContinuousHeartRate
FITNESS_TYPE_EFFECTIVE_STANDING = 11  # -> EffectiveStanding
FITNESS_TYPE_ACTIVITY_DURATION = 12  # -> ActivityDuration

# SESleepData.SESleepDistributionData.SESleepDistributionType
SLEEP_STAGE_AWAKE = 0
SLEEP_STAGE_LIGHT = 1
SLEEP_STAGE_DEEP = 2
SLEEP_STAGE_REM = 3

SLEEP_STAGE_NAMES = {
    SLEEP_STAGE_AWAKE: "awake",
    SLEEP_STAGE_LIGHT: "light",
    SLEEP_STAGE_DEEP: "deep",
    SLEEP_STAGE_REM: "rem",
}

# SESleepData.SESleepType
SLEEP_TYPE_DAYTIME = 0
SLEEP_TYPE_NIGHT = 1

# Field number, inside a REQUEST_FITNESS_TYPE_ID response's field 9, that
# holds that function type's data bean. Not a formula -- an observed lookup
# table (protobuf field numbers follow .proto declaration order, not the
# function_type value).
_FITNESS_RESPONSE_FIELD = {
    FITNESS_TYPE_DAILY: 4,
    FITNESS_TYPE_SLEEP: 5,
    FITNESS_TYPE_CONTINUOUS_HEART_RATE: 6,
    FITNESS_TYPE_EFFECTIVE_STANDING: 14,
    FITNESS_TYPE_ACTIVITY_DURATION: 15,
}

_HEADER_PREFIX = bytes((0x00, 0x00, 0x00, 0x00))
ACK_READY = bytes((0x00, 0x00, 0x01, 0x01, 0x00, 0x00))
ACK_COMPLETE = bytes((0x00, 0x00, 0x01, 0x00, 0x00, 0x00))


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint must be non-negative")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_field_varint(field_number: int, value: int) -> bytes:
    return encode_varint((field_number << 3) | 0) + encode_varint(value)


def encode_field_int32(field_number: int, value: int) -> bytes:
    """Encode a protobuf ``int32`` field."""
    if not -(1 << 31) <= value < (1 << 31):
        raise ValueError("int32 value out of range")
    encoded_value = value if value >= 0 else (1 << 64) + value
    return encode_varint((field_number << 3) | 0) + encode_varint(encoded_value)


def encode_field_sint32(field_number: int, value: int) -> bytes:
    """Encode a protobuf ``sint32`` field using zigzag encoding."""
    if not -(1 << 31) <= value < (1 << 31):
        raise ValueError("sint32 value out of range")
    zigzag = (value << 1) ^ (value >> 31)
    return encode_field_varint(field_number, zigzag)


def encode_field_bytes(field_number: int, data: bytes) -> bytes:
    return encode_varint((field_number << 3) | 2) + encode_varint(len(data)) + data


def encode_request(command_id: int) -> bytes:
    """Build the single-field protobuf payload `{1: command_id}` used for GET_* requests."""
    return encode_field_varint(1, command_id)


def encode_mtu_request_change(
    mtu: int = 247, minimum_chunk_size: int = 12, maximum_chunk_size: int = 12, mode: int = 0
) -> bytes:
    """Build the SDK-level MTU/chunk negotiation sent before bind status.

    This is distinct from, and must agree with, ATT Exchange MTU. The
    successful Android and Linux binds sent
    ``08 00 9a 06 09 08 f7 01 10 0c 18 0c 20 00`` before command 16 after
    negotiating ATT MTU 247; the watch replied ``08 00 10 f7 01``. With the
    identical application request over ATT MTU 23, it instead replied
    ``08 00 10 17`` and did not commit the bind.
    """
    settings = (
        encode_field_varint(1, mtu)
        + encode_field_varint(2, minimum_chunk_size)
        + encode_field_varint(3, maximum_chunk_size)
        + encode_field_varint(4, mode)
    )
    return encode_field_varint(1, CMD_MTU_REQUEST_CHANGE) + encode_field_bytes(99, settings)


def encode_unbind_request() -> bytes:
    """Build the destructive UNBIND_REQUEST (23) payload: ``08 17``.

    This only clears the watch's protocol-level app binding. The official
    Android app subsequently removes its OS Bluetooth bond separately; see
    protocol.md's "Unbinding" section. Do not send this merely to disconnect.
    """
    return encode_request(CMD_UNBIND_REQUEST)


def encode_binding_check_request(device_verify: bool = True) -> bytes:
    """Build BINDING_CHECK (17), which starts the on-watch part of an app bind.

    The observed Android request with ``device_verify=True`` is
    ``08 11 1a 04 12 02 08 01``. It must be followed by a successful
    :func:`encode_binding_result_request`, but the official app also makes a
    vendor-backend bind request before sending that result. These encoders
    alone are therefore not a complete reproduction of a real bind; do not
    use this as a read-only status check.
    """
    bind_check = encode_field_varint(1, int(device_verify))
    bind_account = encode_field_bytes(2, bind_check)
    return encode_field_varint(1, CMD_BINDING_CHECK) + encode_field_bytes(3, bind_account)


def encode_binding_result_request(user_id: str, phone_type: int = PHONE_TYPE_ANDROID) -> bytes:
    """Build BINDING_RESULT (18), the wire half of a BINDING_CHECK exchange.

    The observed Android success request for user ``2011999`` is
    ``08 12 1a 0f 1a 0d 08 00 12 07 32 30 31 31 39 39 39 18 00``.
    It changes the watch's app binding; the supplied user id is an app
    account identifier, not a cryptographic secret. The official app sends
    this only after the vendor backend accepts a device-registration request,
    so this payload alone is not a complete reproduction of the app bind.
    """
    if phone_type not in (PHONE_TYPE_ANDROID, PHONE_TYPE_IOS):
        raise ValueError("phone_type must be PHONE_TYPE_ANDROID or PHONE_TYPE_IOS")
    bind_result = (
        encode_field_varint(1, BIND_RESULT_SUCCESS)
        + encode_field_bytes(2, user_id.encode("utf-8"))
        + encode_field_varint(3, phone_type)
    )
    bind_account = encode_field_bytes(3, bind_result)
    return encode_field_varint(1, CMD_BINDING_RESULT) + encode_field_bytes(3, bind_account)


def encode_set_system_time_request(timestamp: int, utc_offset_quarter_hours: int) -> bytes:
    """Build command 48 using Unix seconds and UTC offset in quarter-hours.

    This matches ``ControlBleTools.setSystemTime(long)``: its optional
    ``time_format`` field is omitted, so synchronizing the clock does not
    alter the watch's 12/24-hour preference.
    """
    if not 0 <= timestamp < (1 << 31):
        raise ValueError("timestamp must fit a positive protobuf int32")
    if not -48 <= utc_offset_quarter_hours <= 56:
        raise ValueError("UTC offset must be between -12:00 and +14:00")
    time_set = encode_field_varint(1, timestamp) + encode_field_sint32(2, utc_offset_quarter_hours)
    system_time = encode_field_bytes(1, time_set)
    return encode_field_varint(1, CMD_SET_SYSTEM_TIME) + encode_field_bytes(5, system_time)


def parse_mtu_response(payload: bytes) -> int:
    """Return the active MTU from a command-0 response."""
    fields = decode_protobuf(payload)
    if _int_field(fields, 1) != CMD_MTU_REQUEST_CHANGE:
        raise ValueError("response is not for MTU_REQUEST_CHANGE")
    return _int_field(fields, 2)


def parse_generic_response_status(payload: bytes, command_id: int) -> int:
    """Return field 100 from a generic command response."""
    fields = decode_protobuf(payload)
    if _int_field(fields, 1) != command_id:
        raise ValueError(f"response is not for command {command_id}")
    return _int_field(fields, 100)


def parse_binding_status_response(payload: bytes) -> bool:
    """Parse command 16's nested application-binding status flag."""
    fields = decode_protobuf(payload)
    if _int_field(fields, 1) != CMD_INQUIRY_BINDING_STATUS:
        raise ValueError("response is not for INQUIRY_BINDING_STATUS")
    status = decode_protobuf(_bytes_field(fields, 3))
    return bool(_int_field(status, 1))


@dataclass(frozen=True)
class ClassicBluetoothStatus:
    """The watch's own report of its classic (BR/EDR) radio, from command 27.

    Mirrors the SDK's `ClassicBleStatusBean{isConnect, isSwitch, mac}`, which
    it fills from `SEBindAccount.classic_bluetooth_status`. `mac` is the
    watch's classic address, which on this watch equals its BLE address.
    """

    connected: bool
    radio_enabled: bool
    mac: str


def parse_classic_bluetooth_status(payload: bytes) -> ClassicBluetoothStatus:
    """Parse a watch-originated command 27, `{1:27, 3:{8:{1:connect, 2:switch, 3:mac}}}`."""
    fields = decode_protobuf(payload)
    if _int_field(fields, 1) != CMD_REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS:
        raise ValueError("message is not REQUEST_CLASSIC_BLUETOOTH_CONNECT_STATUS")
    bind_account = decode_protobuf(_bytes_field(fields, 3))
    status = decode_protobuf(_bytes_field(bind_account, 8))
    return ClassicBluetoothStatus(
        connected=bool(_optional_int(status, 1)),
        radio_enabled=bool(_optional_int(status, 2)),
        mac=_bytes_field(status, 3).decode(errors="replace") if 3 in status else "",
    )


def encode_time(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> bytes:
    """Build the 6-varint-field `time` submessage used throughout the fitness-data protocol."""
    return (
        encode_field_varint(1, year)
        + encode_field_varint(2, month)
        + encode_field_varint(3, day)
        + encode_field_varint(4, hour)
        + encode_field_varint(5, minute)
        + encode_field_varint(6, second)
    )


def encode_fitness_type_id_request(command_id: int, function_type: int, time_bytes: bytes) -> bytes:
    """Build a REQUEST_FITNESS_TYPE_ID (113) or CONFIRM_FITNESS_TYPE_ID (115) request.

    Both share the identical `{1: command_id, 9: {1: {1: time, 2: function_type}}}`
    shape -- verified against 8 live examples (4 function types x 2 dates) by
    replaying the captured log through this module's own encode/decode pair.
    """
    fitness_type_id = encode_field_bytes(1, time_bytes) + encode_field_varint(2, function_type)
    wrapper = encode_field_bytes(1, fitness_type_id)
    return encode_field_varint(1, command_id) + encode_field_bytes(9, wrapper)


def encode_fitness_sport_id_list_request(command_id: int, sport_ids: bytes) -> bytes:
    """Build a GET/REQUEST/CONFIRM_FITNESS_SPORT_ID_LIST (117/119/121) request.

    Shape `{1: command_id, 9: {3: sport_ids}}` -- note this is a different,
    shallower shape than `encode_fitness_type_id_request`'s (that one wraps
    a single date+type selector two levels deep; this one just carries the
    watch's own opaque sport-entry-id bytes back to it verbatim, however
    many entries there are). Verified byte-for-byte against a real workout
    sync capture for commands 119 and 121 (id 117 takes no payload -- use
    `encode_request(CMD_GET_FITNESS_SPORT_ID_LIST)`).
    """
    return encode_field_varint(1, command_id) + encode_field_bytes(9, encode_field_bytes(3, sport_ids))


def encode_real_time_data_switch_request(enabled: bool) -> bytes:
    """Build a REAL_TIME_DATA_SWITCH (164) request.

    Verified against the one live example captured: `08 A4 01 62 02 18 00`
    for the enable call. The inner `18 00` (field 3, varint 0) did not
    visibly vary with on/off in the single capture available, so the on/off
    encoding here is a best-effort guess (`enabled` maps to that field);
    treat as unverified until confirmed against a real "disable" capture.
    """
    inner = encode_field_varint(3, 0 if enabled else 1)
    return encode_field_varint(1, CMD_REAL_TIME_DATA_SWITCH) + encode_field_bytes(12, inner)


def encode_set_heart_rate_monitor_request(
    *,
    frequency: int,
    mode: int = HEART_RATE_MONITOR_MODE_AUTO,
    warning: bool = False,
    warning_value: int = 0,
    sport_warning: bool = False,
    sport_warning_value: int = 0,
    continuous_heart_rate_mode: int = CONTINUOUS_HEART_RATE_MODE_ALL_DAY,
) -> bytes:
    """Build a SET_HEART_RATE_MONITOR (215) request.

    Shape `{1:215, 15:{3:{1:mode, 2:frequency, 3:warning, 4:warning_value,
    5:sport_warning, 6:sport_warning_value, 7:continuous_heart_rate_mode}}}`,
    from the official SDK's encoder (`ControlBleTools.setHeartRateMonitor` ->
    `a.a(215, HeartRateMonitorBean)`) -- see the CMD_SET_HEART_RATE_MONITOR
    comment. `frequency` has no default: this is a full-replace write (the
    official app always sends every field), so read back the watch's current
    settings with GET_HEART_RATE_MONITOR (214) first and pass its values
    through for any field you don't intend to change, rather than guessing.
    """
    inner = (
        encode_field_varint(1, mode)
        + encode_field_varint(2, frequency)
        + encode_field_varint(3, 1 if warning else 0)
        + encode_field_varint(4, warning_value)
        + encode_field_varint(5, 1 if sport_warning else 0)
        + encode_field_varint(6, sport_warning_value)
        + encode_field_varint(7, continuous_heart_rate_mode)
    )
    setting_menu = encode_field_bytes(3, inner)
    return encode_field_varint(1, CMD_SET_HEART_RATE_MONITOR) + encode_field_bytes(15, setting_menu)


@dataclass(frozen=True)
class HeartRateMonitorSettings:
    """Decoded SEHeartRateMonitor, as read back from a GET_HEART_RATE_MONITOR (214) response.

    Field layout and enum values are confirmed from the official SDK (see the
    CMD_GET_HEART_RATE_MONITOR comment). The response's nesting under envelope
    field 15 -> SESettingMenu field 3 was confirmed live on 2026-09-06:
    a real Beyond 3 Pro answered 214 with exactly that shape. Zero-valued
    fields are omitted proto3-style, so every field is optional here; field 8
    (`low_warning_value`) has not been seen at all.
    """

    mode: int
    frequency: int
    warning: bool
    warning_value: int
    sport_warning: bool
    sport_warning_value: int
    continuous_heart_rate_mode: int


def parse_heart_rate_monitor_response(payload: bytes, command_id: int) -> HeartRateMonitorSettings | None:
    """Parse a GET_HEART_RATE_MONITOR (214) or SET_HEART_RATE_MONITOR (215) response.

    Returns `None` when the watch acknowledges without echoing the settings.
    Verified live on 2026-09-06: 214 answers with the full field-15 -> field-3
    bean, while 215 answers with a bare `{1: 215}` ack -- re-read with 214 to
    confirm what was actually stored.
    """
    fields = decode_protobuf(payload)
    if _int_field(fields, 1) != command_id:
        raise ValueError(f"response is not for command {command_id}")
    if 15 not in fields:
        return None
    setting_menu = decode_protobuf(_bytes_field(fields, 15))
    inner = decode_protobuf(_bytes_field(setting_menu, 3))
    return HeartRateMonitorSettings(
        mode=_optional_int(inner, 1),
        frequency=_optional_int(inner, 2),
        warning=bool(_optional_int(inner, 3)),
        warning_value=_optional_int(inner, 4),
        sport_warning=bool(_optional_int(inner, 5)),
        sport_warning_value=_optional_int(inner, 6),
        continuous_heart_rate_mode=_optional_int(inner, 7),
    )


def encode_verify_user_number_request(user_id: str) -> bytes:
    """Build a VERIFY_USER_NUMBER (19) request: `{1: 19, 3: {6: user_id}}`.

    Verified against the live capture: `08 13 1a 09 32 07 "2011999"` for
    user id "2011999". The app always sends this right after connecting,
    before any data command -- `notify`/`battery`/etc. skip it entirely,
    which may be why the watch doesn't treat those bare connections as a
    legitimate bound phone (see TODO.md's "notify is acked but not actually
    displayed" entry).
    """
    inner = encode_field_bytes(6, user_id.encode("utf-8"))
    return encode_field_varint(1, CMD_VERIFY_USER_NUMBER) + encode_field_bytes(3, inner)


def encode_system_notification_request(
    notification_type: int, phone_number: str = "", contacts_info: str = "", message_text: str = ""
) -> bytes:
    """Build a SEND_SYSTEM_NOTIFICATION (178) request.

    The wire shape was recovered from the app's own bytecode
    (`com.zhapp.ble.a`, the "Apricot" protocol branch --
    `com.zhapp.ble.ControlBleTools.sendSystemNotification` -- see
    ../../android-observations.md) and invoked directly in a small JVM
    harness with the real protobuf runtime, so these bytes match what the
    official app sends: `{1: 178, 13: {1: {1: type,
    2: phoneNumber, 3: contactsInfo, 4: messageText}}}`. For
    `NOTIFICATION_TYPE_CALL`, the app itself always forces `messageText` to
    `""` regardless of what's passed -- replicated here for parity. Type
    `NOTIFICATION_TYPE_MESSAGE` is also verified live: with a valid binding
    and ATT MTU 247, it displays contacts and text transiently.
    """
    if notification_type == NOTIFICATION_TYPE_CALL:
        message_text = ""
    system_notification = (
        encode_field_varint(1, notification_type)
        + encode_field_bytes(2, phone_number.encode("utf-8"))
        + encode_field_bytes(3, contacts_info.encode("utf-8"))
        + encode_field_bytes(4, message_text.encode("utf-8"))
    )
    notification = encode_field_bytes(1, system_notification)
    return encode_field_varint(1, CMD_SEND_SYSTEM_NOTIFICATION) + encode_field_bytes(13, notification)


def encode_app_notification_request(
    app_name: str = "", page_name: str = "", title: str = "", text: str = "", ticker_text: str = ""
) -> bytes:
    """Build a SEND_APP_NOTIFICATION (179) request.

    `{1: 179, 13: {2: {1: appName, 2: pageName, 3: title, 4: text,
    5: tickerText}}}` (SENotification.appNotification = field 2, versus
    systemNotification = field 1). Verified byte-for-byte against a real
    app notification captured 2026-08-31 that the watch displayed correctly
    -- see protocol.md's "App push notification" section for those bytes.

    Where each field comes from in the real app
    (`MyNotificationsService.onNotificationPosted` -> `sendAppNotification`):

    - `page_name` is the *Android package name* of the app that posted the
      notification (`StatusBarNotification.getPackageName()`), e.g.
      `com.google.android.gm`. It is never empty in a real send, and it is
      the value the app derives everything else from, so an empty
      `page_name` is rejected here rather than silently sent -- see the
      note in protocol.md about what the watch does with an unidentifiable
      source.
    - `app_name` is that package's human-readable label
      (`AppUtils.getAppName(page_name)`), e.g. `Gmail`.
    - `title`/`text` are the notification's `android.title`/`android.text`
      extras; `ticker_text` is `Notification.tickerText`, which is a short
      sender/summary line -- in the captured send it equalled the title,
      *not* the body.

    `ControlBleTools.sendAppNotification` caps title/ticker at 50 chars and
    text at 200 (truncating to max-1 chars and appending "...") and applies
    no cap at all to appName/pageName -- replicated exactly here.
    """
    if not page_name:
        raise ValueError(
            "page_name is required: it is the sending app's Android package name "
            "(e.g. 'com.google.android.gm'), which the watch uses to identify the "
            "notification's source; the real app never sends it empty"
        )
    app_notification = (
        encode_field_bytes(1, app_name.encode("utf-8"))
        + encode_field_bytes(2, page_name.encode("utf-8"))
        + encode_field_bytes(3, _truncate_string(title, APP_NOTIFICATION_TITLE_MAX).encode("utf-8"))
        + encode_field_bytes(4, _truncate_string(text, APP_NOTIFICATION_TEXT_MAX).encode("utf-8"))
        + encode_field_bytes(5, _truncate_string(ticker_text, APP_NOTIFICATION_TITLE_MAX).encode("utf-8"))
    )
    notification = encode_field_bytes(2, app_notification)
    return encode_field_varint(1, CMD_SEND_APP_NOTIFICATION) + encode_field_bytes(13, notification)


def _truncate_string(value: str, max_length: int) -> str:
    """Byte-for-byte port of the app's BleUtils.truncateString."""
    if value is not None and len(value) > max_length:
        return value[: min(max_length - 1, len(value))] + "..."
    return value


def header_frame(chunk_count: int) -> bytes:
    """Control frame announcing how many data chunks will follow."""
    if not 0 <= chunk_count <= 0xFFFF:
        raise ValueError("chunk_count out of range")
    return _HEADER_PREFIX + chunk_count.to_bytes(2, "little")


def is_header_frame(frame: bytes) -> bool:
    return len(frame) == 6 and frame[:4] == _HEADER_PREFIX


def chunk_count_from_header(frame: bytes) -> int:
    return int.from_bytes(frame[4:6], "little")


def is_ready_ack(frame: bytes) -> bool:
    return frame == ACK_READY


def is_complete_ack(frame: bytes) -> bool:
    return frame == ACK_COMPLETE


def data_chunk(index: int, payload: bytes) -> bytes:
    """Wrap one chunk of the reassembled payload with its 2-byte chunk header."""
    if not 1 <= index <= 0xFF:
        raise ValueError("chunk index out of range")
    return bytes((index, 0x00)) + payload


def split_data_chunk(frame: bytes) -> tuple[int, bytes]:
    """Return (chunk_index, payload) for a data-chunk frame (index byte + reserved byte + payload)."""
    if len(frame) < 2:
        raise ValueError("data chunk frame too short")
    return frame[0], frame[2:]


@dataclass(frozen=True)
class ProtobufValue:
    """One decoded protobuf field: either a raw int/bytes, or a nested message."""

    wire_type: int
    raw: int | bytes


def decode_protobuf(data: bytes) -> dict[int, list[ProtobufValue]]:
    """Minimal generic protobuf decoder: varint and length-delimited fields only.

    Good enough for this watch's small command/response messages, without
    needing a compiled .proto schema. Returns field number -> list of values
    (repeated fields keep every occurrence, in order).
    """
    fields: dict[int, list[ProtobufValue]] = {}
    offset = 0
    length = len(data)
    while offset < length:
        tag, offset = _read_varint(data, offset)
        field_number, wire_type = tag >> 3, tag & 0x07
        if wire_type == 0:  # varint
            value, offset = _read_varint(data, offset)
            raw: int | bytes = value
        elif wire_type == 2:  # length-delimited
            field_length, offset = _read_varint(data, offset)
            raw = data[offset : offset + field_length]
            offset += field_length
        elif wire_type == 5:  # 32-bit
            raw = int.from_bytes(data[offset : offset + 4], "little")
            offset += 4
        elif wire_type == 1:  # 64-bit
            raw = int.from_bytes(data[offset : offset + 8], "little")
            offset += 8
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type} at offset {offset}")
        fields.setdefault(field_number, []).append(ProtobufValue(wire_type, raw))
    return fields


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7


@dataclass(frozen=True)
class DeviceInfo:
    firmware_version: str
    equipment_number: str
    mac: str
    serial_number: str
    battery_capacity: int
    battery_charge_status: int


def parse_device_info(payload: bytes) -> DeviceInfo:
    """Parse a GET_DEVICE_INFO (command id 32) response payload.

    Field layout observed live: {1: command_id, 4: {1: {1: firmware_version,
    2: equipment_number, 3: mac, 4: serial_number, 5: {1: capacity,
    2: charge_status}, ...}}} -- note the extra single-field wrapper at 4.1.
    """
    fields = decode_protobuf(payload)
    wrapper_bytes = fields[4][0].raw
    if not isinstance(wrapper_bytes, bytes):
        raise ValueError("unexpected GET_DEVICE_INFO payload shape")
    wrapper = decode_protobuf(wrapper_bytes)
    device_bytes = wrapper[1][0].raw
    if not isinstance(device_bytes, bytes):
        raise ValueError("unexpected GET_DEVICE_INFO payload shape")
    device = decode_protobuf(device_bytes)
    battery_bytes = device[5][0].raw
    if not isinstance(battery_bytes, bytes):
        raise ValueError("unexpected device_battery_status shape")
    battery = decode_protobuf(battery_bytes)
    return DeviceInfo(
        firmware_version=_string_field(device, 1),
        equipment_number=_string_field(device, 2),
        mac=_string_field(device, 3),
        serial_number=_string_field(device, 4),
        battery_capacity=_int_field(battery, 1),
        battery_charge_status=_int_field(battery, 2),
    )


def _string_field(fields: dict[int, list[ProtobufValue]], number: int) -> str:
    raw = fields[number][0].raw
    if not isinstance(raw, bytes):
        raise ValueError(f"field {number} is not length-delimited")
    return raw.decode("utf-8")


def _int_field(fields: dict[int, list[ProtobufValue]], number: int) -> int:
    raw = fields[number][0].raw
    if not isinstance(raw, int):
        raise ValueError(f"field {number} is not a varint")
    return raw


def _optional_int(fields: dict[int, list[ProtobufValue]], number: int) -> int:
    """Varint field, or 0 when absent -- protobuf omits zero-valued scalars on the wire."""
    return _int_field(fields, number) if number in fields else 0


def _bytes_field(fields: dict[int, list[ProtobufValue]], number: int) -> bytes:
    raw = fields[number][0].raw
    if not isinstance(raw, bytes):
        raise ValueError(f"field {number} is not length-delimited")
    return raw


@dataclass(frozen=True)
class FitnessTypeEntry:
    """One entry from a GET_FITNESS_TYPE_ID_LIST (112) response: a (date, function_type) pair ready to fetch."""

    year: int
    month: int
    day: int
    function_type: int

    def time_bytes(self) -> bytes:
        return encode_time(self.year, self.month, self.day)


def parse_fitness_type_id_list(payload: bytes) -> list[FitnessTypeEntry]:
    """Parse a GET_FITNESS_TYPE_ID_LIST (112) response: the menu of currently-fetchable data buckets.

    Shape: `{1: 112, 9: {2: {1: [{1: time, 2: function_type}, ...repeated]}}}`
    -- two wrapper levels before the repeated field-1 list of entries.
    """
    fields = decode_protobuf(payload)
    wrapper = decode_protobuf(_bytes_field(fields, 9))
    list_container = decode_protobuf(_bytes_field(wrapper, 2))
    entries = []
    for value in list_container.get(1, []):
        if not isinstance(value.raw, bytes):
            continue
        entry_fields = decode_protobuf(value.raw)
        time_fields = decode_protobuf(_bytes_field(entry_fields, 1))
        entries.append(
            FitnessTypeEntry(
                year=_int_field(time_fields, 1),
                month=_int_field(time_fields, 2),
                day=_int_field(time_fields, 3),
                function_type=_int_field(entry_fields, 2),
            )
        )
    return entries


def _fitness_bean_bytes(payload: bytes, function_type: int) -> bytes:
    """Extract the raw bean bytes from a REQUEST_FITNESS_TYPE_ID (113) response, by function type."""
    field_number = _FITNESS_RESPONSE_FIELD.get(function_type)
    if field_number is None:
        raise ValueError(f"no known response field for function_type {function_type}")
    fields = decode_protobuf(payload)
    wrapper = decode_protobuf(_bytes_field(fields, 9))
    return _bytes_field(wrapper, field_number)


def unpack_buckets(raw: bytes) -> list[int]:
    """Decode a 2-bytes-per-bucket big-endian array (`DailyData`'s `*_raw` fields).

    Byte order verified against a non-zero capture (2026-08-31): a day with
    24 steps and 19 m of distance in the 01:00 hour encoded as
    `00 00 00 18 00 00...` and `00 00 00 13 00 00...` -- big-endian, unsigned.
    """
    return [int.from_bytes(raw[offset : offset + 2], "big") for offset in range(0, len(raw) - 1, 2)]


@dataclass(frozen=True)
class DailyData:
    """Steps/distance/calories, in `frequency`-minute buckets across the requested day.

    `steps`/`distance`/`calories` are the decoded buckets (the `*_raw`
    arrays are kept alongside them so nothing is lost to a decode bug):
    2 bytes per bucket, big-endian unsigned, `1440 / frequency` of them, so
    the usual 60-minute frequency gives 24 buckets starting at local
    midnight. `bucket_start_minutes` gives each bucket's offset into the day.

    Distance is metres (the app's own 3 km goal is stored as `3000`, and
    live pushes report ~0.8 m per step); the calorie unit is unconfirmed.
    """

    steps_frequency_minutes: int
    steps_raw: bytes
    distance_frequency_minutes: int
    distance_raw: bytes
    calorie_frequency_minutes: int
    calorie_raw: bytes
    steps: list[int]
    distance: list[int]
    calories: list[int]

    @property
    def total_steps(self) -> int:
        return sum(self.steps)

    @property
    def total_distance(self) -> int:
        return sum(self.distance)

    @property
    def total_calories(self) -> int:
        return sum(self.calories)

    def bucket_start_minutes(self, frequency_minutes: int | None = None) -> list[int]:
        """Minutes past local midnight at which each bucket starts."""
        frequency = frequency_minutes or self.steps_frequency_minutes
        return [index * frequency for index in range(len(self.steps))]


def parse_daily_data(payload: bytes) -> DailyData:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_DAILY))
    steps_raw = _bytes_field(bean, 3)
    distance_raw = _bytes_field(bean, 5)
    calorie_raw = _bytes_field(bean, 7)
    return DailyData(
        steps_frequency_minutes=_int_field(bean, 2),
        steps_raw=steps_raw,
        distance_frequency_minutes=_int_field(bean, 4),
        distance_raw=distance_raw,
        calorie_frequency_minutes=_int_field(bean, 6),
        calorie_raw=calorie_raw,
        steps=unpack_buckets(steps_raw),
        distance=unpack_buckets(distance_raw),
        calories=unpack_buckets(calorie_raw),
    )


@dataclass(frozen=True)
class SleepStage:
    """One stretch of a single stage within a night, from `SleepData.stages`."""

    start_timestamp: int
    duration_minutes: int
    stage: int

    @property
    def stage_name(self) -> str:
        return SLEEP_STAGE_NAMES.get(self.stage, f"unknown_{self.stage}")


@dataclass(frozen=True)
class SleepData:
    """One night's sleep: summary totals plus the stage timeline.

    All `*_time` values are minutes and all `*_percentage` values are whole
    percents of `duration_minutes`. Timestamps are Unix seconds in the
    watch's local time zone. The last `stages` entry is the wake-up marker:
    stage `awake` with `duration_minutes` 0 at `end_timestamp`.
    """

    start_timestamp: int
    end_timestamp: int
    duration_minutes: int
    score: int
    awake_time: int
    awake_time_percentage: int
    light_sleep_time: int
    light_sleep_time_percentage: int
    deep_sleep_time: int
    deep_sleep_time_percentage: int
    rem_time: int
    rem_time_percentage: int
    sleep_type: int
    stages: list[SleepStage]

    @property
    def is_night_sleep(self) -> bool:
        return self.sleep_type == SLEEP_TYPE_NIGHT


def parse_sleep_data(payload: bytes) -> SleepData:
    """Parse the `SESleepData` bean of a REQUEST_FITNESS_TYPE_ID (113) response.

    Field numbers are the app's own `FitnessProtos$SESleepData` constants,
    cross-checked against a live capture (see protocol.md).
    """
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_SLEEP))
    stages: list[SleepStage] = []
    if 14 in bean:
        container = decode_protobuf(_bytes_field(bean, 14))
        for value in container.get(1, []):
            if not isinstance(value.raw, bytes):
                continue
            entry = decode_protobuf(value.raw)
            stages.append(
                SleepStage(
                    start_timestamp=_optional_int(entry, 1),
                    duration_minutes=_optional_int(entry, 2),
                    stage=_optional_int(entry, 3),
                )
            )
    return SleepData(
        start_timestamp=_optional_int(bean, 2),
        end_timestamp=_optional_int(bean, 3),
        duration_minutes=_optional_int(bean, 4),
        score=_optional_int(bean, 5),
        awake_time=_optional_int(bean, 6),
        awake_time_percentage=_optional_int(bean, 7),
        light_sleep_time=_optional_int(bean, 8),
        light_sleep_time_percentage=_optional_int(bean, 9),
        deep_sleep_time=_optional_int(bean, 10),
        deep_sleep_time_percentage=_optional_int(bean, 11),
        rem_time=_optional_int(bean, 12),
        rem_time_percentage=_optional_int(bean, 13),
        sleep_type=_optional_int(bean, 15),
        stages=stages,
    )


@dataclass(frozen=True)
class ContinuousHeartRate:
    """Heart rate sampled every `frequency_minutes` across the requested day.

    Unlike `DailyData`, these arrays are 1 byte per bucket (a heart rate
    fits in a byte), confirmed against a non-zero capture: a day whose only
    reading was 60 bpm at 05:20 had byte 64 of a 288-byte array (5-minute
    frequency) set to 60, matching the app's own decode. A zero bucket means
    "no sample", not a measured zero.
    """

    frequency_minutes: int
    heart_rate_raw: bytes
    max_value: int
    min_value: int
    resting_value: int
    hour_max_raw: bytes
    hour_min_raw: bytes
    heart_rate: list[int]
    hour_max: list[int]
    hour_min: list[int]


def parse_continuous_heart_rate(payload: bytes) -> ContinuousHeartRate:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_CONTINUOUS_HEART_RATE))
    heart_rate_raw = _bytes_field(bean, 3)
    hour_max_raw = _bytes_field(bean, 7)
    hour_min_raw = _bytes_field(bean, 8)
    return ContinuousHeartRate(
        frequency_minutes=_int_field(bean, 2),
        heart_rate_raw=heart_rate_raw,
        max_value=_int_field(bean, 4),
        min_value=_int_field(bean, 5),
        resting_value=_int_field(bean, 6),
        hour_max_raw=hour_max_raw,
        hour_min_raw=hour_min_raw,
        heart_rate=list(heart_rate_raw),
        hour_max=list(hour_max_raw),
        hour_min=list(hour_min_raw),
    )


@dataclass(frozen=True)
class EffectiveStanding:
    """`SEEffectiveStandingData` (function_type 11): frequency plus one packed
    byte array, no further fields in the app's own protobuf class."""

    frequency_minutes: int
    raw: bytes


def parse_effective_standing(payload: bytes) -> EffectiveStanding:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_EFFECTIVE_STANDING))
    return EffectiveStanding(frequency_minutes=_int_field(bean, 2), raw=_bytes_field(bean, 3))


# `SEActivityDurationData` fields 6..31: (time, percentage) pairs per sport
# category, in the app's own declaration order.
_ACTIVITY_SPORT_CATEGORIES = (
    "running",
    "walking",
    "cycling",
    "swimming",
    "fitness_exercise",
    "outdoor",
    "ball_game",
    "yoga",
    "winter",
    "dance_movement",
    "aquatic",
    "leisure",
    "other",
)


@dataclass(frozen=True)
class ActivityDuration:
    """`SEActivityDurationData` (function_type 12).

    Unlike the other fitness beans, everything past the packed `raw` array is
    individual varint fields, so this is an exact decode with no byte-order
    guessing: `daily_time`/`daily_percentage` (fields 4/5) and then one
    (time, percentage) pair per sport category (fields 6..31)."""

    frequency_minutes: int
    raw: bytes
    daily_time: int
    daily_percentage: int
    sport_time: dict[str, int]
    sport_percentage: dict[str, int]


def parse_activity_duration(payload: bytes) -> ActivityDuration:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_ACTIVITY_DURATION))
    return ActivityDuration(
        frequency_minutes=_int_field(bean, 2),
        raw=_bytes_field(bean, 3),
        daily_time=_optional_int(bean, 4),
        daily_percentage=_optional_int(bean, 5),
        sport_time={name: _optional_int(bean, 6 + 2 * index) for index, name in enumerate(_ACTIVITY_SPORT_CATEGORIES)},
        sport_percentage={name: _optional_int(bean, 7 + 2 * index) for index, name in enumerate(_ACTIVITY_SPORT_CATEGORIES)},
    )


@dataclass(frozen=True)
class RealTimeData:
    """Parsed REPORT_BASIC_DATA (165) -- the watch's unsolicited push once
    real-time reporting is enabled (see `encode_real_time_data_switch_request`).

    Field numbers 8, 9, and 13 are known to exist (varints, always 0 in the
    only captures available) but their meaning wasn't identified -- likely
    among effectiveStandingHour/sleepDuration/HBAData/activityDuration per
    the app's own log field ordering, but not confirmed byte-for-byte like
    the fields below were.
    """

    steps: int
    calories: int
    distance: int
    heart_rate: int
    blood_oxygen: int
    effective_standing: int
    battery_capacity: int
    battery_charge_status: int
    steps_hourly_raw: bytes
    distance_hourly_raw: bytes
    calorie_hourly_raw: bytes


def parse_real_time_data(payload: bytes) -> RealTimeData:
    fields = decode_protobuf(payload)
    wrapper = decode_protobuf(_bytes_field(fields, 12))
    bean = decode_protobuf(_bytes_field(wrapper, 4))
    battery = decode_protobuf(_bytes_field(bean, 7))
    return RealTimeData(
        steps=_int_field(bean, 1),
        calories=_int_field(bean, 2),
        distance=_int_field(bean, 3),
        heart_rate=_int_field(bean, 4),
        blood_oxygen=_int_field(bean, 5),
        effective_standing=_int_field(bean, 6),
        battery_capacity=_int_field(battery, 1),
        battery_charge_status=_int_field(battery, 2),
        steps_hourly_raw=_bytes_field(bean, 16),
        distance_hourly_raw=_bytes_field(bean, 17),
        calorie_hourly_raw=_bytes_field(bean, 18),
    )


@dataclass(frozen=True)
class SportEntryId:
    """One 7-byte opaque(-ish) entry from a GET_FITNESS_SPORT_ID_LIST (117) response.

    Byte layout, decoded from a real workout sync capture (2026-08-29) via
    the app's own `sportparsing` debug log lines, which print exactly this
    breakdown for each id:
    `[0:4] timestamp (LE uint32, matches the workout's start time) [4] a
    constant byte (0x08 in the one capture available, meaning unconfirmed)
    [5] sport_type (varint-like single byte, 2 = observed for a walk/run)
    [6] flags byte, whose low 2 bits are the data type (0/1/2, see
    SPORT_DATA_* constants) and whose upper 6 bits are constant across all
    3 entries in the one capture (meaning unconfirmed)`.
    """

    raw: bytes  # exactly 7 bytes
    timestamp: int
    sport_type: int
    data_type: int


def parse_sport_id_list(payload: bytes) -> list[SportEntryId]:
    """Parse a GET_FITNESS_SPORT_ID_LIST (117) response into its 7-byte entries."""
    fields = decode_protobuf(payload)
    wrapper = decode_protobuf(_bytes_field(fields, 9))
    blob = _bytes_field(wrapper, 3)
    if len(blob) % 7 != 0:
        raise ValueError(f"sport id list length {len(blob)} is not a multiple of 7")
    entries = []
    for i in range(0, len(blob), 7):
        entry = blob[i : i + 7]
        entries.append(
            SportEntryId(
                raw=entry,
                timestamp=int.from_bytes(entry[0:4], "little"),
                sport_type=entry[5],
                data_type=entry[6] & 0x03,
            )
        )
    return entries


@dataclass(frozen=True)
class WorkoutReport:
    """Parsed SPORT_DATA_REPORT (dataType 1) summary blob.

    Byte layout found by searching a real 103-byte REPORT blob for this
    workout's already-known values (from the phone's own sqlite database,
    `sportmodleinfo`/`exerciseoutdoor` tables -- see android-observations.md)
    and confirming every offset below against them exactly:
    `[0:7] sport entry id [7] status byte (0 observed) [8:12] unknown
    [12:16] start_time (LE uint32, duplicates the entry id's timestamp)
    [16:20] end_time (LE uint32) [20:24] duration_seconds (LE uint32)
    [24:28] distance_meters (LE uint32) [28:30] calories (LE uint16)
    [30:...] unknown [42:44] steps (LE uint16) [44:48] unknown
    [48] avg_heart_rate (single byte) [49] max_heart_rate (single byte)
    [50] min_heart_rate (single byte) [51:] unknown/reserved, all zero in
    the one capture available except a non-zero tail (offset 84+) of
    unidentified meaning (possibly a checksum).`
    """

    start_time: int
    end_time: int
    duration_seconds: int
    distance_meters: int
    calories: int
    steps: int
    avg_heart_rate: int
    max_heart_rate: int
    min_heart_rate: int
    raw: bytes


def parse_workout_report(blob: bytes) -> WorkoutReport:
    if len(blob) < 51:
        raise ValueError(f"REPORT blob too short: {len(blob)} bytes")
    return WorkoutReport(
        start_time=int.from_bytes(blob[12:16], "little"),
        end_time=int.from_bytes(blob[16:20], "little"),
        duration_seconds=int.from_bytes(blob[20:24], "little"),
        distance_meters=int.from_bytes(blob[24:28], "little"),
        calories=int.from_bytes(blob[28:30], "little"),
        steps=int.from_bytes(blob[42:44], "little"),
        avg_heart_rate=blob[48],
        max_heart_rate=blob[49],
        min_heart_rate=blob[50],
        raw=blob,
    )


@dataclass(frozen=True)
class GpsPoint:
    timestamp: int
    longitude: float
    latitude: float


def parse_gps_track(blob: bytes) -> list[GpsPoint]:
    """Parse a SPORT_DATA_GPS (dataType 2) blob.

    `[0:7] sport entry id [7] status byte (0) [8] unknown (0xE0 observed)
    [9:] repeating 12-byte records: [+0:4] timestamp (LE uint32, absolute
    Unix seconds) [+4:8] longitude (LE float32) [+8:12] latitude (LE
    float32)`. Verified point-for-point against the app's own fully-parsed
    `DevSportInfoBean.map_data`/`recordGpsTime` log output for a real
    561-point workout track (2026-08-29) -- every timestamp and coordinate
    matched exactly. A 4-byte remainder after the last full record (bytes
    9 + 12*n .. end) is unaccounted for; likely a footer/checksum, not
    another partial point (too short to be one).
    """
    points = []
    offset = 9
    while offset + 12 <= len(blob):
        timestamp = int.from_bytes(blob[offset : offset + 4], "little")
        longitude = struct.unpack_from("<f", blob, offset + 4)[0]
        latitude = struct.unpack_from("<f", blob, offset + 8)[0]
        points.append(GpsPoint(timestamp=timestamp, longitude=longitude, latitude=latitude))
        offset += 12
    return points


@dataclass(frozen=True)
class WorkoutData:
    entries: list[SportEntryId]
    report: WorkoutReport | None
    gps_track: list[GpsPoint] | None
    point_data_raw: bytes | None  # SPORT_DATA_POINT -- not decoded, see protocol.md


def latest_workout_entries(entries: list[SportEntryId]) -> list[SportEntryId]:
    """Return just the entries for the most recent workout (max timestamp).

    The queue can hold entries for more than one past workout at once, each
    identified by its shared `timestamp`. Bundling every queued entry into
    one `REQUEST_FITNESS_SPORT_DATA` call means one unfetchable/already-
    consumed workout (e.g. a stale entry the watch stopped offering data
    for after a premature confirm, see `JOURNAL.md`'s 2026-08-29 entry)
    blocks every other workout too, since `split_sport_data_blobs` requires
    every requested entry to be found. Fetching one workout's entries at a
    time avoids that.
    """
    if not entries:
        return []
    latest_timestamp = max(entry.timestamp for entry in entries)
    return [entry for entry in entries if entry.timestamp == latest_timestamp]


def split_sport_data_blobs(combined: bytes, entries: list[SportEntryId]) -> dict[int, bytes]:
    """Split one concatenated activity-channel byte stream into per-entry blobs.

    The activity channel (`16186f03`) transfers each requested entry's data
    as one or more chunked-transport "rounds" back to back, with no
    wire-level marker for where one entry ends and the next begins -- but
    each entry's data does start with that entry's own 7-byte id (see
    `SportEntryId`), which we already know from the GET_FITNESS_SPORT_ID_LIST
    response. So: find where each known id occurs in the combined stream,
    and slice between consecutive offsets. Robust as long as a sport-entry
    id's bytes don't recur elsewhere in the data, which is expected (they
    encode a timestamp + flags, not a value likely to collide with GPS/
    sensor payload bytes).
    """
    offsets = []
    for entry in entries:
        index = combined.find(entry.raw)
        if index < 0:
            raise ValueError(f"sport entry id {entry.raw.hex()} not found in combined activity data")
        offsets.append((index, entry.data_type))
    offsets.sort()
    result = {}
    for i, (start, data_type) in enumerate(offsets):
        end = offsets[i + 1][0] if i + 1 < len(offsets) else len(combined)
        result[data_type] = combined[start:end]
    return result
