"""Parser tests against real payloads captured from the official app's own traffic.

Both hex blobs below are complete, reassembled REQUEST_FITNESS_TYPE_ID (113)
responses lifted from the Zeblaze Fit app's BLE debug log on 2026-08-31, next
to the app's own decode of them -- so the expected values here are what the
vendor app itself displayed for the same bytes.
"""

from __future__ import annotations

from zeblaze_ble import protocol

# function_type 1, night of 2026-08-30.
SLEEP_PAYLOAD = bytes.fromhex(
    "08714afd012afa010a110a0d08ea0f1008181e200028003000100110f4c5d2d40618bc80d4d406"
    "208e03284f3000380040a702484b506758196000680072c0010a0a08f4c5d2d406101b18010a0a"
    "08c8d2d2d406101618020a0a08f0dcd2d406101c18010a0a0880ead2d406100818020a0a08e0ed"
    "d2d406104118010a0a089c8cd3d406102018020a0a089c9bd3d406102118010a0a08d8aad3d406"
    "100918020a0a08f4aed3d406102a18010a0a08ccc2d3d406100718020a0a08f0c5d3d406102618"
    "010a0a08d8d7d3d406100818020a0a08b8dbd3d406101c18010a0a08c8e8d3d406101118020a0a"
    "08c4f0d3d406102218010a0a08bc80d4d406100018007801"
)

# function_type 0, day of 2026-08-30.
DAILY_PAYLOAD = bytes.fromhex(
    "08714ab20122af010a110a0d08ea0f1008181e2000280030001000103c1a300000000000000000"
    "00000000000000000000000000000000000000000000000000000000000000000000001d000000"
    "5f203c2a3000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000170000004d303c3a30000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000002"
)


# function_type 2, day of 2026-08-31: a single 60 bpm reading at 05:20.
HEART_RATE_PAYLOAD = bytes.fromhex(
    "08714af50232f2020a110a0d08ea0f1008181f200028003000100210051aa00200000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000003c0000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "000000000000000000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000203c283c30003a180000000000000000000000000000000000000000000000"
    "004218000000000000000000000000000000000000000000000000"
)


def test_parse_sleep_data_matches_the_app_decode() -> None:
    sleep = protocol.parse_sleep_data(SLEEP_PAYLOAD)
    assert sleep.start_timestamp == 1788125940
    assert sleep.end_timestamp == 1788149820
    assert sleep.duration_minutes == 398
    assert sleep.score == 79
    assert (sleep.light_sleep_time, sleep.light_sleep_time_percentage) == (295, 75)
    assert (sleep.deep_sleep_time, sleep.deep_sleep_time_percentage) == (103, 25)
    assert (sleep.awake_time, sleep.rem_time) == (0, 0)
    assert sleep.is_night_sleep


def test_parse_sleep_data_stage_timeline() -> None:
    stages = protocol.parse_sleep_data(SLEEP_PAYLOAD).stages
    assert len(stages) == 16
    assert (stages[0].start_timestamp, stages[0].duration_minutes, stages[0].stage_name) == (1788125940, 27, "light")
    assert (stages[1].duration_minutes, stages[1].stage_name) == (22, "deep")
    # The timeline ends with a zero-length awake marker at the wake-up time.
    assert (stages[-1].start_timestamp, stages[-1].duration_minutes, stages[-1].stage_name) == (1788149820, 0, "awake")
    light = sum(stage.duration_minutes for stage in stages if stage.stage == protocol.SLEEP_STAGE_LIGHT)
    deep = sum(stage.duration_minutes for stage in stages if stage.stage == protocol.SLEEP_STAGE_DEEP)
    assert (light, deep) == (295, 103)


def test_parse_daily_data_buckets_are_big_endian() -> None:
    daily = protocol.parse_daily_data(DAILY_PAYLOAD)
    assert daily.steps_frequency_minutes == 60
    # Exactly what the app logged for this payload: stepsData=[.., 29, 0, 95].
    assert daily.steps == [0] * 21 + [29, 0, 95]
    assert daily.distance == [0] * 21 + [23, 0, 77]
    assert daily.calories == [0] * 23 + [2]


def test_parse_daily_data_totals_and_bucket_times() -> None:
    daily = protocol.parse_daily_data(DAILY_PAYLOAD)
    assert (daily.total_steps, daily.total_distance, daily.total_calories) == (124, 100, 2)
    assert len(daily.steps) == 24
    assert daily.bucket_start_minutes()[21] == 21 * 60


def test_parse_continuous_heart_rate_is_one_byte_per_bucket() -> None:
    heart_rate = protocol.parse_continuous_heart_rate(HEART_RATE_PAYLOAD)
    assert heart_rate.frequency_minutes == 5
    assert len(heart_rate.heart_rate) == 288  # 1440 / 5
    # The app logged heartRateData[64] == 60 for these same bytes: 05:20.
    assert [(index, value) for index, value in enumerate(heart_rate.heart_rate) if value] == [(64, 60)]
    assert (heart_rate.max_value, heart_rate.min_value) == (60, 60)
    assert (len(heart_rate.hour_max), len(heart_rate.hour_min)) == (24, 24)
