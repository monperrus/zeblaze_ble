#!/usr/bin/env python3
"""Print everything a Zeblaze watch sends, on every channel, with its meaning.

Opens one GATT connection, which subscribes to all five notifying
characteristics (6f01 command-response, 6f02 write-ack, 6f03 activity/bulk
data, 6f04 large-file, 6f05 voice), and then just listens: every frame is
printed as it arrives, and every complete chunked message is decoded as far as
this package knows how -- command id and name, a parsed structure when a
parser exists, and a generic protobuf field dump when one does not.

Unlike the CLI commands, this sends no requests of its own except the optional
real-time-data switch (command 164, with --realtime), so what it shows is what
the watch pushes on its own. It does answer the chunked transport's
ready/complete acks, because a watch whose message is never acknowledged stops
sending.

Stop it with Ctrl-C.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import json

from zeblaze_ble import protocol
from zeblaze_ble.gatttool_transport import CHANNEL_NAMES, READ_VALUE_HANDLE, GatttoolSession

# Payload parsers for the watch-originated messages this package understands.
# Everything else falls back to a generic protobuf dump.
_MESSAGE_PARSERS = {
    protocol.CMD_REPORT_BASIC_DATA: protocol.parse_real_time_data,
    protocol.CMD_GET_DEVICE_INFO: protocol.parse_device_info,
    protocol.CMD_GET_FITNESS_TYPE_ID_LIST: protocol.parse_fitness_type_id_list,
    protocol.CMD_GET_FITNESS_SPORT_ID_LIST: protocol.parse_sport_id_list,
}


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        description="Listen on every Zeblaze BLE channel and decode what arrives"
    )
    command_parser.add_argument("address", help="known BLE address of the watch")
    command_parser.add_argument(
        "--seconds",
        type=float,
        default=0,
        help="stop after this many seconds (default: 0, run until Ctrl-C)",
    )
    command_parser.add_argument(
        "--realtime",
        action="store_true",
        help=(
            "also enable the watch's real-time data push (command 164) so live "
            "steps/heart-rate reports arrive; this is a protocol write"
        ),
    )
    command_parser.add_argument(
        "--frames",
        action="store_true",
        help="also print every individual transport frame, not just decoded messages",
    )
    return command_parser


def _jsonable(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _generic_fields(payload: bytes) -> dict[str, object]:
    """Describe an unparsed message as field number -> value, one level deep."""
    described: dict[str, object] = {}
    for number, values in protocol.decode_protobuf(payload).items():
        rendered = []
        for value in values:
            if isinstance(value.raw, int):
                rendered.append(value.raw)
            else:
                # Length-delimited: show the bytes, plus a nested decode when
                # the content parses as a submessage (it usually does here).
                nested: object
                try:
                    nested = {str(key): len(items) for key, items in protocol.decode_protobuf(value.raw).items()}
                except (ValueError, IndexError):
                    nested = None
                rendered.append({"hex": value.raw.hex(), "nested_field_counts": nested})
        described[f"field_{number}"] = rendered[0] if len(rendered) == 1 else rendered
    return described


def _say(text: str) -> None:
    print(f"{dt.datetime.now().strftime('%H:%M:%S')} {text}", flush=True)


def _describe_message(channel: str, payload: bytes) -> None:
    fields = protocol.decode_protobuf(payload)
    command_value = fields.get(1, [None])[0]
    command_id = command_value.raw if command_value is not None and isinstance(command_value.raw, int) else None
    name = protocol.COMMAND_NAMES.get(command_id, "unknown command") if command_id is not None else "no command id"
    _say(f"[{channel}] message: {name} ({command_id}), {len(payload)} bytes")

    payload_parser = _MESSAGE_PARSERS.get(command_id) if command_id is not None else None
    decoded: object
    if payload_parser is not None:
        try:
            decoded = _jsonable(payload_parser(payload))
        except (ValueError, KeyError, IndexError) as error:
            decoded = {"parse_error": str(error), "fields": _generic_fields(payload)}
    elif command_id in (protocol.CMD_GET_HEART_RATE_MONITOR, protocol.CMD_SET_HEART_RATE_MONITOR):
        decoded = _jsonable(protocol.parse_heart_rate_monitor_response(payload, command_id))
    else:
        decoded = _generic_fields(payload)
    print(json.dumps({"hex": payload.hex(), "decoded": decoded}, indent=2), flush=True)


async def monitor(address: str, seconds: float, realtime: bool, show_frames: bool) -> None:
    # Chunks in flight, per channel: value handle -> (expected count, index -> bytes).
    assembling: dict[int, tuple[int, dict[int, bytes]]] = {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds if seconds > 0 else None

    async with GatttoolSession(address) as session:
        _say(f"connected to {address}; listening on {', '.join(CHANNEL_NAMES.values())}")
        if realtime:
            # The watch sends its own message (command 27) right after
            # connecting and waits for it to be acknowledged; a command
            # written before that ack goes unanswered and times out.
            for payload in await session.receive_pending_messages(grace_seconds=2.0):
                _describe_message(CHANNEL_NAMES[READ_VALUE_HANDLE], payload)
            await session.send_message(protocol.encode_real_time_data_switch_request(True))
            _say("enabled real-time data push (command 164)")

        while True:
            timeout = None if deadline is None else max(deadline - loop.time(), 0.1)
            try:
                handle, frame = await session.next_raw_notification(timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                if deadline is not None and loop.time() >= deadline:
                    return
                continue

            channel = CHANNEL_NAMES.get(handle, f"unknown handle 0x{handle:04x}")
            if show_frames:
                _say(f"[{channel}] frame {frame.hex()}")

            if protocol.is_header_frame(frame):
                count = protocol.chunk_count_from_header(frame)
                assembling[handle] = (count, {})
                await session.acknowledge(handle, protocol.ACK_READY)
                if show_frames:
                    _say(f"[{channel}] header: {count} chunk(s) incoming, sent ready-ack")
                continue
            if protocol.is_ready_ack(frame):
                if show_frames:
                    _say(f"[{channel}] watch is ready for our chunks")
                continue
            if protocol.is_complete_ack(frame):
                if show_frames:
                    _say(f"[{channel}] watch acknowledged our message")
                continue

            pending = assembling.get(handle)
            if pending is None:
                _say(f"[{channel}] data chunk with no preceding header: {frame.hex()}")
                continue
            count, chunks = pending
            index, chunk = protocol.split_data_chunk(frame)
            chunks[index] = chunk
            if len(chunks) < count:
                continue

            del assembling[handle]
            await session.acknowledge(handle, protocol.ACK_COMPLETE)
            payload = b"".join(chunks[key] for key in sorted(chunks))
            try:
                _describe_message(channel, payload)
            except (ValueError, IndexError) as error:
                _say(f"[{channel}] undecodable message {payload.hex()}: {error}")


def main() -> None:
    arguments = parser().parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(
            monitor(arguments.address, arguments.seconds, arguments.realtime, arguments.frames)
        )


if __name__ == "__main__":
    main()
