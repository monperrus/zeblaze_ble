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
    request_device_info,
    request_fitness_data,
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
    notify_command = commands.add_parser(
        "notify",
        help="push a notification to the watch (performs a protocol write)",
    )
    notify_command.add_argument("address")
    notify_command.add_argument("--type", choices=("message", "call", "miss_call"), default="message")
    notify_command.add_argument("--sender", default="", help="contactsInfo: sender/caller name shown on the watch")
    notify_command.add_argument("--text", default="", help="messageText: body text (ignored for --type call)")
    notify_command.add_argument("--phone", default="", help="phoneNumber (relevant for --type call/miss_call)")
    notify_command.add_argument("--i-understand-this-writes", action="store_true", required=True)
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

    if arguments.command == "realtime":
        readings = await enable_real_time_data_and_listen(arguments.address, arguments.seconds)
        print(json.dumps([_jsonable(reading) for reading in readings], indent=2))
        return 0

    if arguments.command == "notify":
        notification_type = {
            "call": protocol.NOTIFICATION_TYPE_CALL,
            "miss_call": protocol.NOTIFICATION_TYPE_MISS_CALL,
            "message": protocol.NOTIFICATION_TYPE_MESSAGE,
        }[arguments.type]
        response = await send_notification(
            arguments.address, notification_type, arguments.phone, arguments.sender, arguments.text
        )
        print(json.dumps({"response_hex": response.hex()}, indent=2))
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
    except BleakError as error:
        raise SystemExit(f"Bluetooth operation failed: {error}")


if __name__ == "__main__":
    main()
