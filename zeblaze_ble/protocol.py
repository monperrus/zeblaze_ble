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
DATA_UPLOAD = "16186f04-0000-1000-8000-00807f9b34fb"

NOTIFICATION_CHANNELS = (COMMAND_READ, COMMAND_WRITE, ACTIVITY_DATA, DATA_UPLOAD)

# Command ids observed in a live capture of the official app (BLE_2026-08-29.zh).
CMD_INQUIRY_BINDING_STATUS = 16
CMD_VERIFY_USER_NUMBER = 19
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

# Low 2 bits of a sport-entry id's last byte (see SportEntryId below).
SPORT_DATA_POINT = 0  # per-interval samples, structure not decoded (see protocol.md)
SPORT_DATA_REPORT = 1  # workout summary -> WorkoutReport
SPORT_DATA_GPS = 2  # GPS track -> list[GpsPoint]

# fitness_function_type values observed live (there is no known "GPS"/location
# type among these -- GPS data lives under the separate, unexplored
# GET_FITNESS_SPORT_ID_LIST (117) workout-session mechanism instead).
FITNESS_TYPE_DAILY = 0  # steps/distance/calories -> DailyData
FITNESS_TYPE_CONTINUOUS_HEART_RATE = 2  # -> ContinuousHeartRate
FITNESS_TYPE_ACTIVITY_DURATION = 11  # -> ActivityDuration
FITNESS_TYPE_EFFECTIVE_STANDING = 12  # -> EffectiveStanding

# Field number, inside a REQUEST_FITNESS_TYPE_ID response's field 9, that
# holds that function type's data bean. Not a formula -- an observed lookup
# table (protobuf field numbers follow .proto declaration order, not the
# function_type value).
_FITNESS_RESPONSE_FIELD = {
    FITNESS_TYPE_DAILY: 4,
    FITNESS_TYPE_CONTINUOUS_HEART_RATE: 6,
    FITNESS_TYPE_ACTIVITY_DURATION: 14,
    FITNESS_TYPE_EFFECTIVE_STANDING: 15,
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


def encode_field_bytes(field_number: int, data: bytes) -> bytes:
    return encode_varint((field_number << 3) | 2) + encode_varint(len(data)) + data


def encode_request(command_id: int) -> bytes:
    """Build the single-field protobuf payload `{1: command_id}` used for GET_* requests."""
    return encode_field_varint(1, command_id)


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

    Not reverse-engineered from a capture (no notification was ever sent in
    any session) -- instead the app's own real bytecode was decompiled
    (`com.zhapp.ble.a`, the "Apricot" protocol branch --
    `com.zhapp.ble.ControlBleTools.sendSystemNotification` -- see
    ../../android-observations.md) and invoked directly in a small JVM
    harness with the real protobuf runtime, so these bytes are exactly what
    the official app would send, not a guess: `{1: 178, 13: {1: {1: type,
    2: phoneNumber, 3: contactsInfo, 4: messageText}}}`. For
    `NOTIFICATION_TYPE_CALL`, the app itself always forces `messageText` to
    `""` regardless of what's passed -- replicated here for parity.
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


@dataclass(frozen=True)
class DailyData:
    """Steps/distance/calories, in `frequency`-minute buckets across the requested day.

    The three `*_raw` arrays are 2-bytes-per-bucket (bucket count matches
    1440/frequency, and 1 byte would cap a value at 255 which real step/
    calorie/distance counts routinely exceed) but the exact integer encoding
    (endianness, signedness) is UNVERIFIED -- only all-zero examples have
    been captured so far. Raw bytes are exposed as-is; do not trust a
    byte-order guess without a non-zero capture to check it against.
    """

    steps_frequency_minutes: int
    steps_raw: bytes
    distance_frequency_minutes: int
    distance_raw: bytes
    calorie_frequency_minutes: int
    calorie_raw: bytes


def parse_daily_data(payload: bytes) -> DailyData:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_DAILY))
    return DailyData(
        steps_frequency_minutes=_int_field(bean, 2),
        steps_raw=_bytes_field(bean, 3),
        distance_frequency_minutes=_int_field(bean, 4),
        distance_raw=_bytes_field(bean, 5),
        calorie_frequency_minutes=_int_field(bean, 6),
        calorie_raw=_bytes_field(bean, 7),
    )


@dataclass(frozen=True)
class ContinuousHeartRate:
    """Same raw-bytes caveat as `DailyData` applies to the *_raw fields here.

    `heart_rate_raw` is 1 byte per bucket though (a heart rate fits in a
    byte, unlike steps/distance/calories), so that one is very likely a
    plain unsigned byte array -- still unconfirmed against non-zero data.
    """

    frequency_minutes: int
    heart_rate_raw: bytes
    max_value: int
    min_value: int
    resting_value: int
    hour_max_raw: bytes
    hour_min_raw: bytes


def parse_continuous_heart_rate(payload: bytes) -> ContinuousHeartRate:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_CONTINUOUS_HEART_RATE))
    return ContinuousHeartRate(
        frequency_minutes=_int_field(bean, 2),
        heart_rate_raw=_bytes_field(bean, 3),
        max_value=_int_field(bean, 4),
        min_value=_int_field(bean, 5),
        resting_value=_int_field(bean, 6),
        hour_max_raw=_bytes_field(bean, 7),
        hour_min_raw=_bytes_field(bean, 8),
    )


@dataclass(frozen=True)
class ActivityDuration:
    frequency_minutes: int
    raw: bytes


def parse_activity_duration(payload: bytes) -> ActivityDuration:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_ACTIVITY_DURATION))
    return ActivityDuration(frequency_minutes=_int_field(bean, 2), raw=_bytes_field(bean, 3))


@dataclass(frozen=True)
class EffectiveStanding:
    """Unlike the other fitness beans, the hourly values here are individual
    varint fields (4..31, one per hour-ish bucket) rather than a packed byte
    array -- so `hourly` is an exact, unambiguous decode, no byte-order
    guessing involved."""

    frequency_minutes: int
    raw: bytes
    hourly: list[int]


def parse_effective_standing(payload: bytes) -> EffectiveStanding:
    bean = decode_protobuf(_fitness_bean_bytes(payload, FITNESS_TYPE_EFFECTIVE_STANDING))
    hourly = [_int_field(bean, n) for n in range(4, 32) if n in bean]
    return EffectiveStanding(frequency_minutes=_int_field(bean, 2), raw=_bytes_field(bean, 3), hourly=hourly)


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
