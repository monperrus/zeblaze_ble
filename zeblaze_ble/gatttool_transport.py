"""Chunked request/response transport driven through interactive gatttool.

bleak's BlueZ D-Bus service resolution has proven unreliable for this watch
on Linux (empty or partial `client.services` after connect, independent of
protocol correctness -- see `linux_gatt.py`, built for the same reason).
Raw `gatttool -I` reliably sees the full GATT table and can write/subscribe
by handle, so this module reimplements the protocol.py framing over that
instead of bleak, using the fixed handles observed for this watch's firmware.
"""

from __future__ import annotations

import asyncio
import re

from . import protocol

# Fixed ATT handles for this watch's firmware (from `zeblaze-ble inspect --backend gatttool`).
_READ_VALUE_HANDLE = 0x0021  # 16186f01 characteristic value
_READ_CCCD_HANDLE = 0x0022
_WRITE_VALUE_HANDLE = 0x0024  # 16186f02 characteristic value
_WRITE_CCCD_HANDLE = 0x0025

_NOTIFICATION_RE = re.compile(r"Notification handle = 0x([0-9a-fA-F]+) value: ([0-9a-fA-F ]+)")
_PROMPT_TIMEOUT_SECONDS = 15.0


class GatttoolSession:
    """Owns one interactive `gatttool -I` subprocess and its notification stream."""

    def __init__(self, address: str) -> None:
        self._address = address
        self._process: asyncio.subprocess.Process | None = None
        self._notifications: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "GatttoolSession":
        self._process = await asyncio.create_subprocess_exec(
            "stdbuf", "-oL", "-eL", "gatttool", "-I", "-b", self._address,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self._send_line("connect")
            await self._wait_for("Connection successful", timeout_seconds=40)
            await self._enable_notifications(_READ_CCCD_HANDLE)
            await self._enable_notifications(_WRITE_CCCD_HANDLE)
        except BaseException:
            # __aexit__ is never called if __aenter__ raises: clean up the
            # subprocess ourselves or it leaks and holds the LE connection,
            # leaving the watch "busy" for every subsequent attempt.
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                await self._send_line("exit")
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                self._process.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()

    async def _read_loop(self) -> None:
        """Single reader for the subprocess's stdout: fan lines out to both consumers."""
        assert self._process is not None and self._process.stdout is not None
        while True:
            line_bytes = await self._process.stdout.readline()
            if not line_bytes:
                return
            line = line_bytes.decode(errors="replace").strip()
            match = _NOTIFICATION_RE.search(line)
            if match:
                handle = int(match.group(1), 16)
                value = bytes.fromhex(match.group(2).replace(" ", ""))
                self._notifications.put_nowait((handle, value))
            self._lines.put_nowait(line)

    async def _wait_for(self, needle: str, timeout_seconds: float = _PROMPT_TIMEOUT_SECONDS) -> None:
        async with asyncio.timeout(timeout_seconds):
            while True:
                line = await self._lines.get()
                if needle in line:
                    return

    async def _send_line(self, command: str) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write((command + "\n").encode())
        await self._process.stdin.drain()

    async def _enable_notifications(self, cccd_handle: int) -> None:
        await self._send_line(f"char-write-req 0x{cccd_handle:04x} 0100")
        await self._wait_for("Characteristic value was written successfully")

    async def _write(self, value_handle: int, payload: bytes) -> None:
        await self._send_line(f"char-write-cmd 0x{value_handle:04x} {payload.hex()}")

    async def _next_notification(self, expect_handle: int) -> bytes:
        while True:
            handle, value = await asyncio.wait_for(self._notifications.get(), timeout=_PROMPT_TIMEOUT_SECONDS)
            if handle == expect_handle:
                return value

    async def send_message(self, payload: bytes, mtu_chunk_size: int = 180) -> None:
        chunks = [payload[i : i + mtu_chunk_size] for i in range(0, len(payload), mtu_chunk_size)] or [b""]
        await self._write(_WRITE_VALUE_HANDLE, protocol.header_frame(len(chunks)))
        ack = await self._next_notification(_WRITE_VALUE_HANDLE)
        if not protocol.is_ready_ack(ack):
            raise RuntimeError(f"expected ready-ack, got {ack.hex()}")
        for index, chunk in enumerate(chunks, start=1):
            await self._write(_WRITE_VALUE_HANDLE, protocol.data_chunk(index, chunk))
        ack = await self._next_notification(_WRITE_VALUE_HANDLE)
        if not protocol.is_complete_ack(ack):
            raise RuntimeError(f"expected complete-ack, got {ack.hex()}")

    async def receive_message(self) -> bytes:
        header = await self._next_notification(_READ_VALUE_HANDLE)
        if not protocol.is_header_frame(header):
            raise RuntimeError(f"expected header frame, got {header.hex()}")
        count = protocol.chunk_count_from_header(header)
        await self._write(_READ_VALUE_HANDLE, protocol.ACK_READY)
        chunks: dict[int, bytes] = {}
        for _ in range(count):
            frame = await self._next_notification(_READ_VALUE_HANDLE)
            index, chunk = protocol.split_data_chunk(frame)
            chunks[index] = chunk
        await self._write(_READ_VALUE_HANDLE, protocol.ACK_COMPLETE)
        return b"".join(chunks[index] for index in sorted(chunks))


async def request_device_info(address: str) -> protocol.DeviceInfo:
    """Connect via gatttool, send GET_DEVICE_INFO (id 32), and return the parsed response."""
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_request(protocol.CMD_GET_DEVICE_INFO))
        payload = await session.receive_message()
    return protocol.parse_device_info(payload)


_FITNESS_PARSERS = {
    protocol.FITNESS_TYPE_DAILY: protocol.parse_daily_data,
    protocol.FITNESS_TYPE_CONTINUOUS_HEART_RATE: protocol.parse_continuous_heart_rate,
    protocol.FITNESS_TYPE_ACTIVITY_DURATION: protocol.parse_activity_duration,
    protocol.FITNESS_TYPE_EFFECTIVE_STANDING: protocol.parse_effective_standing,
}


async def request_fitness_data(address: str) -> list[tuple[protocol.FitnessTypeEntry, object]]:
    """Connect, enumerate every currently-available fitness data bucket, and fetch+parse each.

    Follows the exact GET_FITNESS_TYPE_ID_LIST(112) -> [REQUEST_FITNESS_TYPE_ID(113) ->
    CONFIRM_FITNESS_TYPE_ID(115)]* sequence observed in the live capture. Entries whose
    function_type has no known parser (see protocol.py's "what isn't known yet") are
    returned with the raw payload bytes instead of a parsed dataclass.
    """
    results: list[tuple[protocol.FitnessTypeEntry, object]] = []
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_request(protocol.CMD_GET_FITNESS_TYPE_ID_LIST))
        list_payload = await session.receive_message()
        entries = protocol.parse_fitness_type_id_list(list_payload)

        for entry in entries:
            await session.send_message(
                protocol.encode_fitness_type_id_request(
                    protocol.CMD_REQUEST_FITNESS_TYPE_ID, entry.function_type, entry.time_bytes()
                )
            )
            data_payload = await session.receive_message()
            await session.send_message(
                protocol.encode_fitness_type_id_request(
                    protocol.CMD_CONFIRM_FITNESS_TYPE_ID, entry.function_type, entry.time_bytes()
                )
            )
            await session.receive_message()  # CONFIRM's own trivial ack-style reply; nothing to extract

            parser = _FITNESS_PARSERS.get(entry.function_type)
            if parser is None:
                results.append((entry, data_payload))
                continue
            try:
                results.append((entry, parser(data_payload)))
            except Exception as error:
                # Live data doesn't always match the one reference capture's exact
                # shape (e.g. genuinely no data yet for a bucket) -- surface the raw
                # payload and the error instead of losing every other entry's results.
                results.append((entry, {"parse_error": str(error), "raw_hex": data_payload.hex()}))
    return results


async def enable_real_time_data_and_listen(address: str, seconds: float) -> list[protocol.RealTimeData]:
    """Connect, enable real-time reporting (command 164), and collect REPORT_BASIC_DATA (165) pushes.

    The watch pushes unsolicited REPORT_BASIC_DATA messages on the same
    notify channel used for solicited responses. Some firmware also sends a
    generic acknowledgment for the enable command first, so ignore every
    non-REPORT_BASIC_DATA payload while collecting the live pushes.
    """
    readings: list[protocol.RealTimeData] = []
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_real_time_data_switch_request(True))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + seconds
        while loop.time() < deadline:
            try:
                payload = await asyncio.wait_for(session.receive_message(), timeout=max(deadline - loop.time(), 0.1))
            except (TimeoutError, asyncio.TimeoutError):
                break
            fields = protocol.decode_protobuf(payload)
            command = fields.get(1, [None])[0]
            if (
                command is None
                or command.wire_type != 0
                or command.raw != protocol.CMD_REPORT_BASIC_DATA
                or 12 not in fields
            ):
                continue
            readings.append(protocol.parse_real_time_data(payload))
    return readings


async def send_notification(
    address: str, notification_type: int, phone_number: str = "", contacts_info: str = "", message_text: str = ""
) -> bytes:
    """Connect and push a SEND_SYSTEM_NOTIFICATION (178) to the watch.

    Returns the raw response payload (the generic ack/error-code shape
    documented in protocol.md, `{1: 178, 100: <code>}`) for the caller to
    inspect/log -- this command was never seen live, only reconstructed
    from the app's own bytecode, so nothing about its response is assumed.
    """
    async with GatttoolSession(address) as session:
        await session.send_message(
            protocol.encode_system_notification_request(notification_type, phone_number, contacts_info, message_text)
        )
        return await session.receive_message()
