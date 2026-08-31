"""Command line interface for the read-only Zeblaze BLE collector."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import shutil
from pathlib import Path

from bleak.exc import BleakError

from . import protocol
from .client import inspect, listen, scan, serialize_advertisement
from .gatttool_transport import (
    enable_real_time_data_and_listen,
    request_current_heart_rate,
    request_device_info,
    request_fitness_data,
    request_workout_data,
    send_app_notification,
    send_notification,
)
from .linux_gatt import inspect as inspect_gatttool


def _jsonable(value: object) -> object:
    """Recursively convert dataclasses/bytes into JSON-serializable plain data (bytes -> hex string)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description="Zeblaze Beyond 3 Pro BLE tool")
    commands = command_parser.add_subparsers(dest="command", required=True)
    scan_command = commands.add_parser("scan", help="scan for the watch")
    scan_command.add_argument("--seconds", type=float, default=10)
    inspect_command = commands.add_parser("inspect", help="print the GATT table without writing")
    inspect_command.add_argument("address")
    inspect_command.add_argument("--backend", choices=("auto", "bleak", "gatttool"), default="auto")
    listen_command = commands.add_parser("listen", help="subscribe and save received notification packets")
    listen_command.add_argument("address")
    listen_command.add_argument("--seconds", type=float, default=60)
    listen_command.add_argument("--output", type=Path, default=Path("zeblaze-capture.jsonl"))
    battery_command = commands.add_parser(
        "battery",
        help="send GET_DEVICE_INFO and print battery status (performs a protocol write)",
    )
    battery_command.add_argument("address")
    battery_command.add_argument(
        "--i-understand-this-writes",
        action="store_true",
        required=True,
        help="required opt-in: this command writes a command to the watch, unlike every other subcommand",
    )
    fitness_command = commands.add_parser(
        "fitness",
        help="fetch every currently-available steps/heart-rate/activity/standing bucket (performs protocol writes)",
    )
    fitness_command.add_argument("address")
    fitness_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    realtime_command = commands.add_parser(
        "realtime",
        help="enable and listen to the watch's real-time data push (performs protocol writes)",
    )
    realtime_command.add_argument("address")
    realtime_command.add_argument("--seconds", type=float, default=20)
    realtime_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    heart_rate_command = commands.add_parser(
        "heartrate",
        help="return one current heart-rate reading (performs a protocol write)",
    )
    heart_rate_command.add_argument("address")
    heart_rate_command.add_argument(
        "--seconds",
        type=float,
        default=30,
        help="seconds to wait for each live report attempt (default: 30)",
    )
    heart_rate_command.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="number of transient BLE failures to retry (default: 3)",
    )
    heart_rate_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    notify_command = commands.add_parser(
        "notify",
        help="push a notification to the watch (performs a protocol write)",
    )
    notify_command.add_argument("address")
    notify_command.add_argument("--type", choices=("message", "call", "miss_call"), default="message")
    notify_command.add_argument("--sender", default="", help="contactsInfo: sender/caller name shown on the watch")
    notify_command.add_argument("--text", default="", help="messageText: body text (ignored for --type call)")
    notify_command.add_argument("--phone", default="", help="phoneNumber (relevant for --type call/miss_call)")
    notify_command.add_argument("--attempts", type=int, default=8, help="retries for flaky BLE (default: 8)")
    notify_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    app_notify_command = commands.add_parser(
        "app-notify",
        help="push an app-style notification (like WhatsApp/Slack) to the watch (performs a protocol write)",
    )
    app_notify_command.add_argument("address")
    app_notify_command.add_argument(
        "--app",
        default="Messages",
        help="appName: the source app's display label, as shown on the watch (default: Messages)",
    )
    app_notify_command.add_argument("--sender", default="", help="title: sender/title line shown on the watch")
    app_notify_command.add_argument("--text", default="", help="text: body text")
    app_notify_command.add_argument(
        "--ticker",
        default=None,
        help="tickerText: Android's short summary line for the notification (default: same as --sender)",
    )
    app_notify_command.add_argument(
        "--page",
        default="com.google.android.apps.messaging",
        help=(
            "pageName: the source app's Android package name, which the watch uses to "
            "identify where the notification came from. Must correspond to --app; the "
            "default pairs with --app's default. Never send it empty."
        ),
    )
    app_notify_command.add_argument("--attempts", type=int, default=8, help="retries for flaky BLE (default: 8)")
    app_notify_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    daily_command = commands.add_parser(
        "daily",
        help="fetch every currently-available day of steps/distance/calories (performs protocol writes)",
    )
    daily_command.add_argument("address")
    daily_command.add_argument(
        "--buckets",
        action="store_true",
        help="also print the per-bucket (hourly) arrays, not just the day totals",
    )
    daily_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    sleep_command = commands.add_parser(
        "sleep",
        help="fetch every currently-available night of sleep data (performs protocol writes)",
    )
    sleep_command.add_argument("address")
    sleep_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    workout_command = commands.add_parser(
        "workout",
        help="fetch queued workout data (summary, GPS track) from the watch (performs protocol writes)",
    )
    workout_command.add_argument("address")
    workout_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
    return command_parser


async def run(arguments: argparse.Namespace) -> int:
    if arguments.command == "scan":
        watches = await scan(arguments.seconds)
        for watch in watches:
            print(serialize_advertisement(watch))
        return 0 if watches else 1
    if arguments.command == "inspect":
        if arguments.backend == "gatttool" or (arguments.backend == "auto" and shutil.which("gatttool")):
            result = await inspect_gatttool(arguments.address)
        else:
            result = await inspect(arguments.address)
        print(json.dumps(result, indent=2))
        return 0

    if arguments.command == "battery":
        info = await request_device_info(arguments.address)
        print(
            json.dumps(
                {
                    "firmware_version": info.firmware_version,
                    "equipment_number": info.equipment_number,
                    "mac": info.mac,
                    "serial_number": info.serial_number,
                    "battery_capacity_percent": info.battery_capacity,
                    "battery_charge_status": info.battery_charge_status,
                },
                indent=2,
            )
        )
        return 0

    if arguments.command == "fitness":
        results = await request_fitness_data(arguments.address)
        print(
            json.dumps(
                [
                    {
                        "date": f"{entry.year:04d}-{entry.month:02d}-{entry.day:02d}",
                        "function_type": entry.function_type,
                        "data": _jsonable(data),
                    }
                    for entry, data in results
                ],
                indent=2,
            )
        )
        return 0

    if arguments.command == "daily":
        results = await request_fitness_data(arguments.address, (protocol.FITNESS_TYPE_DAILY,))
        days = []
        for entry, data in results:
            day: dict[str, object] = {"date": f"{entry.year:04d}-{entry.month:02d}-{entry.day:02d}"}
            if not isinstance(data, protocol.DailyData):
                day["data"] = _jsonable(data)
                days.append(day)
                continue
            day["total_steps"] = data.total_steps
            day["total_distance_metres"] = data.total_distance
            day["total_calories"] = data.total_calories
            day["bucket_minutes"] = data.steps_frequency_minutes
            if arguments.buckets:
                day["steps"] = data.steps
                day["distance"] = data.distance
                day["calories"] = data.calories
            days.append(day)
        print(json.dumps(days, indent=2))
        return 0

    if arguments.command == "sleep":
        results = await request_fitness_data(arguments.address, (protocol.FITNESS_TYPE_SLEEP,))
        print(
            json.dumps(
                [
                    {
                        "date": f"{entry.year:04d}-{entry.month:02d}-{entry.day:02d}",
                        "data": _jsonable(data),
                    }
                    for entry, data in results
                ],
                indent=2,
            )
        )
        return 0

    if arguments.command == "realtime":
        readings = await enable_real_time_data_and_listen(arguments.address, arguments.seconds)
        print(json.dumps([_jsonable(reading) for reading in readings], indent=2))
        return 0

    if arguments.command == "heartrate":
        heart_rate = await request_current_heart_rate(arguments.address, arguments.seconds, arguments.attempts)
        print(json.dumps({"heart_rate_bpm": heart_rate}))
        return 0

    if arguments.command == "notify":
        notification_type = {
            "call": protocol.NOTIFICATION_TYPE_CALL,
            "miss_call": protocol.NOTIFICATION_TYPE_MISS_CALL,
            "message": protocol.NOTIFICATION_TYPE_MESSAGE,
        }[arguments.type]
        response = await send_notification(
            arguments.address,
            notification_type,
            arguments.phone,
            arguments.sender,
            arguments.text,
            attempts=arguments.attempts,
        )
        print(json.dumps({"response_hex": response.hex()}, indent=2))
        return 0

    if arguments.command == "app-notify":
        # tickerText is Android's Notification.tickerText: a short summary
        # line, which in the one captured send the watch displayed correctly
        # was the sender name -- the same string as the title, not the body.
        # (An earlier "the watch draws its body from tickerText" theory was an
        # artifact of a stale banner and is retracted; see protocol.md.)
        ticker_text = arguments.ticker if arguments.ticker is not None else arguments.sender
        response = await send_app_notification(
            arguments.address,
            arguments.app,
            arguments.page,
            arguments.sender,
            arguments.text,
            ticker_text,
            attempts=arguments.attempts,
        )
        print(json.dumps({"response_hex": response.hex()}, indent=2))
        return 0

    if arguments.command == "workout":
        data = await request_workout_data(arguments.address)
        result = {
            "entries": [_jsonable(entry) for entry in data.entries],
            "report": _jsonable(data.report),
            "gps_track": _jsonable(data.gps_track) if data.gps_track is not None else None,
            "gps_point_count": len(data.gps_track) if data.gps_track is not None else 0,
            "point_data_raw_hex": data.point_data_raw.hex() if data.point_data_raw is not None else None,
        }
        print(json.dumps(result, indent=2))
        return 0

    def print_packet(packet: dict[str, object]) -> None:
        print(json.dumps(packet, sort_keys=True))

    count = await listen(arguments.address, arguments.seconds, arguments.output, print_packet)
    print(f"saved {count} packets to {arguments.output}")
    return 0


def main() -> None:
    arguments = parser().parse_args()
    try:
        raise SystemExit(asyncio.run(run(arguments)))
    except (BleakError, ConnectionError, RuntimeError, TimeoutError) as error:
        detail = str(error) or type(error).__name__
        raise SystemExit(f"Bluetooth operation failed: {detail}")


if __name__ == "__main__":
    main()
