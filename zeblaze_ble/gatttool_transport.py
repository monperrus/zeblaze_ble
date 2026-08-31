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
import datetime as dt
import re
from collections import defaultdict, deque
from dataclasses import dataclass

from . import protocol

# Fixed ATT handles for this watch's firmware (from `zeblaze-ble inspect --backend gatttool`).
_READ_VALUE_HANDLE = 0x0021  # 16186f01 characteristic value
_READ_CCCD_HANDLE = 0x0022
_WRITE_VALUE_HANDLE = 0x0024  # 16186f02 characteristic value
_WRITE_CCCD_HANDLE = 0x0025
_ACTIVITY_VALUE_HANDLE = 0x0027  # 16186f03 characteristic value -- bulk data (e.g. GPS tracks), see protocol.md
_ACTIVITY_CCCD_HANDLE = 0x0028
_DATA_UPLOAD_VALUE_HANDLE = 0x002A  # 16186f04 characteristic value
_DATA_UPLOAD_CCCD_HANDLE = 0x002B
_CHANNEL_6F05_VALUE_HANDLE = 0x002D
_CHANNEL_6F05_CCCD_HANDLE = 0x002E

_NOTIFICATION_RE = re.compile(r"Notification handle = 0x([0-9a-fA-F]+) value: ([0-9a-fA-F ]+)")
_PROMPT_TIMEOUT_SECONDS = 15.0
_CONNECTION_TIMEOUT_SECONDS = 40.0


class HeartRateUnavailableError(RuntimeError):
    """Raised when the watch did not provide a usable live heart-rate value."""


class GattOperationError(RuntimeError):
    """Raised when gatttool reports an explicit `Error: ...` ATT response."""


@dataclass(frozen=True)
class BindOutcome:
    """Responses from a completed minimal application bind."""

    mtu: int
    binding_check_response: bytes
    binding_result_response: bytes
    binding_status_response: bytes
    bound: bool
    timestamp: int
    utc_offset_eighth_hours: int
    time_response: bytes
    watch_messages: tuple[bytes, ...]


async def _disconnect_local_bluez(address: str) -> None:
    """Release this host's stale BlueZ ACL connection, if one exists.

    `gatttool` occasionally exits before bluetoothd has dropped the link. A
    subsequent session is then refused as busy even though no gatttool process
    remains. This only affects the local BlueZ adapter; it cannot disconnect a
    phone or another BLE central.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            "disconnect",
            address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


class GatttoolSession:
    """Own one interactive ``gatttool -I`` process and notification stream.

    Binding this watch requires both ``att_mtu=247`` here and the separate
    :func:`protocol.encode_mtu_request_change` command after connection. A
    2026-08-31 controlled trial used the same five CCCD subscriptions and
    bind frames at MTU 23 and MTU 247: command 0 echoed the active MTU, and
    only 247 caused command 27 followed by bound/verified status. Merely
    keeping outbound chunks below 23 bytes is not equivalent.
    """

    def __init__(self, address: str, security_level: str = "low", att_mtu: int | None = None) -> None:
        if security_level not in {"low", "medium", "high"}:
            raise ValueError("security_level must be low, medium, or high")
        if att_mtu is not None and not 23 <= att_mtu <= 517:
            raise ValueError("att_mtu must be between 23 and 517")
        self._address = address
        self._security_level = security_level
        self._att_mtu = att_mtu
        self._process: asyncio.subprocess.Process | None = None
        self._notifications: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._pending_notifications: dict[int, deque[bytes]] = defaultdict(deque)
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "GatttoolSession":
        await _disconnect_local_bluez(self._address)
        self._process = await asyncio.create_subprocess_exec(
            "stdbuf", "-oL", "-eL", "gatttool", "-I", "-b", self._address,
            "-l", self._security_level,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            # gatttool needs a moment after spawning before its interactive
            # prompt/readline loop is actually ready to process stdin -- a
            # "connect" sent immediately after exec can be dropped or
            # mishandled (observed live 2026-08-29: gatttool would print its
            # prompt and then hang indefinitely, with a
            # "GLib-CRITICAL: Source ID 1 was not found" warning, never
            # attempting the connection at all; a 1s delay before the first
            # command reliably avoided this in manual testing).
            await asyncio.sleep(1.0)
            await self._send_line("connect")
            await self._wait_for_connection()
            if self._att_mtu is not None:
                await self._send_line(f"mtu {self._att_mtu}")
                await self._wait_for_success_or_error(
                    f"MTU was exchanged successfully: {self._att_mtu}"
                )
            await self._enable_notifications(_READ_CCCD_HANDLE)
            await self._enable_notifications(_WRITE_CCCD_HANDLE)
            await self._enable_notifications(_ACTIVITY_CCCD_HANDLE)
            await self._enable_notifications(_DATA_UPLOAD_CCCD_HANDLE)
            await self._enable_notifications(_CHANNEL_6F05_CCCD_HANDLE)
            if self._security_level != "low":
                # Passing `-l medium` on gatttool's command line did not
                # secure the connection it subsequently opened.  A live HCI
                # capture on 2026-08-31 showed all application writes going
                # out in the clear; only BlueZ's automatic reconnect *after*
                # gatttool exited reused the LTK and enabled AES-CCM.
                #
                # Changing the interactive socket's level after `connect`
                # is effective: LE Start Encryption and Encryption Change
                # completed before the first protocol write in the follow-up
                # capture.  Keep `-l` above as the requested initial policy,
                # but repeat it here because this is the placement verified
                # against the controller.
                await self._send_line(f"sec-level {self._security_level}")
                await self._wait_for(f"sec-level {self._security_level}")
                # gatttool does not print a separate completion message for
                # this command.  Allow the controller/key exchange to finish
                # before returning the session to its caller.  In the live
                # trace AES-CCM completed about 0.40 s after the command.
                await asyncio.sleep(0.75)
        except TimeoutError as error:
            # Bare TimeoutError renders as an empty CLI error, which hides
            # the actionable problem from callers.
            await self.__aexit__(None, None, None)
            raise ConnectionError(f"Timed out connecting to or configuring {self._address}") from error
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
                # `exit` alone can leave BlueZ holding the ACL connection for
                # a while. Explicitly release it so the next command can
                # connect without a manual `bluetoothctl disconnect`.
                await self._send_line("disconnect")
                await self._wait_for_any(("Disconnected", "Connection terminated"), timeout_seconds=2)
            except (TimeoutError, asyncio.TimeoutError):
                pass
            try:
                await self._send_line("exit")
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                self._process.kill()
        if self._reader_task is not None:
            self._reader_task.cancel()
        await _disconnect_local_bluez(self._address)

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

    async def _wait_for_success_or_error(self, needle: str, timeout_seconds: float = _PROMPT_TIMEOUT_SECONDS) -> None:
        """Like `_wait_for`, but raises immediately on a gatttool `Error:` line
        instead of waiting out the full timeout for one that will never come."""
        async with asyncio.timeout(timeout_seconds):
            while True:
                line = await self._lines.get()
                if needle in line:
                    return
                if "Error:" in line:
                    raise GattOperationError(line)

    async def _wait_for_any(self, needles: tuple[str, ...], timeout_seconds: float) -> str:
        async with asyncio.timeout(timeout_seconds):
            while True:
                line = await self._lines.get()
                if any(needle in line for needle in needles):
                    return line

    async def _wait_for_connection(self) -> None:
        async with asyncio.timeout(_CONNECTION_TIMEOUT_SECONDS):
            while True:
                line = await self._lines.get()
                if "Connection successful" in line:
                    return
                if "Error: connect" in line or "Connection refused" in line:
                    raise ConnectionError(line)

    async def _send_line(self, command: str) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write((command + "\n").encode())
        await self._process.stdin.drain()

    async def _enable_notifications(self, cccd_handle: int) -> None:
        await self._send_line(f"char-write-req 0x{cccd_handle:04x} 0100")
        try:
            await self._wait_for_success_or_error("Characteristic value was written successfully")
        except GattOperationError:
            # BLE "Robust Caching": after the watch's GATT database changes
            # (e.g. a factory reset), the *first* ATT request from a
            # previously-bonded client -- any request, not specifically
            # this one -- gets rejected with "Database Out of Sync" (ATT
            # error 0x12, gatttool renders it as a generic "Unexpected
            # error code"). That rejection is what clears the server's
            # per-client "change-unaware" flag; every request after it
            # succeeds normally. Live-tested 2026-08-29 after a watch
            # factory reset: confirmed the immediate retry of the exact
            # same write succeeds. Retry once; a second failure is real.
            await self._send_line(f"char-write-req 0x{cccd_handle:04x} 0100")
            await self._wait_for("Characteristic value was written successfully")

    async def _write(self, value_handle: int, payload: bytes) -> None:
        await self._send_line(f"char-write-cmd 0x{value_handle:04x} {payload.hex()}")

    async def _next_notification(self, expect_handle: int, timeout: float = _PROMPT_TIMEOUT_SECONDS) -> bytes:
        pending = self._pending_notifications[expect_handle]
        if pending:
            return pending.popleft()
        while True:
            handle, value = await asyncio.wait_for(self._notifications.get(), timeout=timeout)
            if handle == expect_handle:
                return value
            self._pending_notifications[handle].append(value)

    async def _next_notification_any(
        self, expect_handles: frozenset[int], timeout: float = _PROMPT_TIMEOUT_SECONDS
    ) -> tuple[int, bytes]:
        for handle in expect_handles:
            pending = self._pending_notifications[handle]
            if pending:
                return handle, pending.popleft()
        while True:
            handle, value = await asyncio.wait_for(self._notifications.get(), timeout=timeout)
            if handle in expect_handles:
                return handle, value
            self._pending_notifications[handle].append(value)

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

    async def receive_message(self, value_handle: int = _READ_VALUE_HANDLE, header_timeout: float = _PROMPT_TIMEOUT_SECONDS) -> bytes:
        header = await self._next_notification(value_handle, timeout=header_timeout)
        if not protocol.is_header_frame(header):
            raise RuntimeError(f"expected header frame, got {header.hex()}")
        count = protocol.chunk_count_from_header(header)
        await self._write(value_handle, protocol.ACK_READY)
        chunks: dict[int, bytes] = {}
        for _ in range(count):
            frame = await self._next_notification(value_handle)
            index, chunk = protocol.split_data_chunk(frame)
            chunks[index] = chunk
        await self._write(value_handle, protocol.ACK_COMPLETE)
        return b"".join(chunks[index] for index in sorted(chunks))

    async def receive_pending_messages(
        self,
        value_handle: int = _READ_VALUE_HANDLE,
        grace_seconds: float = 1.0,
    ) -> list[bytes]:
        """Acknowledge complete watch-originated messages until the channel is idle."""
        messages: list[bytes] = []
        while True:
            try:
                messages.append(
                    await self.receive_message(value_handle, header_timeout=grace_seconds)
                )
            except (TimeoutError, asyncio.TimeoutError):
                return messages

    async def receive_all_activity_data(
        self, grace_seconds: float = 3.0, first_round_timeout: float = _PROMPT_TIMEOUT_SECONDS
    ) -> bytes:
        """Receive every bulk-transfer "round" until none *starts* within
        `grace_seconds`, concatenating them into one buffer.

        A bulk transfer (e.g. a workout's GPS/point/report data) spans
        multiple chunked-transport rounds with no wire-level marker for
        where it ends -- see `protocol.split_sport_data_blobs` for how the
        caller is expected to split the result back into per-entry blobs
        afterwards. Only the wait for each *new* round's header frame is
        bounded by `grace_seconds` -- except the very first round, which
        gets `first_round_timeout` (a live transfer can take noticeably
        longer than `grace_seconds` for the watch to start sending it).

        Each round's header is accepted on *either* `_ACTIVITY_VALUE_HANDLE`
        (6f03) or `_READ_VALUE_HANDLE` (6f01): live-tested 2026-08-29 against
        a real (non-replayed) workout, the first round of a
        REQUEST_FITNESS_SPORT_DATA reply arrived on 6f01, the normal
        command-response channel, not 6f03 as the offline-capture-derived
        assumption in protocol.md had it. Polling only 6f03 silently
        discarded that header notification and never sent back the
        ready-ack the watch was waiting for, stalling the whole transfer.
        Each round is completed on whichever handle its header arrived on.
        """
        handles = frozenset({_ACTIVITY_VALUE_HANDLE, _READ_VALUE_HANDLE})
        buffer = bytearray()
        header_timeout = first_round_timeout
        while True:
            try:
                value_handle, header = await self._next_notification_any(handles, timeout=header_timeout)
            except (TimeoutError, asyncio.TimeoutError):
                break
            if not protocol.is_header_frame(header):
                raise RuntimeError(f"expected header frame, got {header.hex()} on handle 0x{value_handle:04x}")
            count = protocol.chunk_count_from_header(header)
            await self._write(value_handle, protocol.ACK_READY)
            chunks: dict[int, bytes] = {}
            for _ in range(count):
                frame = await self._next_notification(value_handle)
                index, chunk = protocol.split_data_chunk(frame)
                chunks[index] = chunk
            await self._write(value_handle, protocol.ACK_COMPLETE)
            buffer.extend(b"".join(chunks[index] for index in sorted(chunks)))
            header_timeout = grace_seconds
        return bytes(buffer)


async def request_device_info(address: str) -> protocol.DeviceInfo:
    """Connect via gatttool, send GET_DEVICE_INFO (id 32), and return the parsed response."""
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_request(protocol.CMD_GET_DEVICE_INFO))
        payload = await session.receive_message()
    return protocol.parse_device_info(payload)


_FITNESS_PARSERS = {
    protocol.FITNESS_TYPE_DAILY: protocol.parse_daily_data,
    protocol.FITNESS_TYPE_SLEEP: protocol.parse_sleep_data,
    protocol.FITNESS_TYPE_CONTINUOUS_HEART_RATE: protocol.parse_continuous_heart_rate,
    protocol.FITNESS_TYPE_ACTIVITY_DURATION: protocol.parse_activity_duration,
    protocol.FITNESS_TYPE_EFFECTIVE_STANDING: protocol.parse_effective_standing,
}


async def request_fitness_data(
    address: str, function_types: tuple[int, ...] | None = None
) -> list[tuple[protocol.FitnessTypeEntry, object]]:
    """Connect, enumerate every currently-available fitness data bucket, and fetch+parse each.

    Follows the exact GET_FITNESS_TYPE_ID_LIST(112) -> [REQUEST_FITNESS_TYPE_ID(113) ->
    CONFIRM_FITNESS_TYPE_ID(115)]* sequence observed in the live capture. Entries whose
    function_type has no known parser (see protocol.py's "what isn't known yet") are
    returned with the raw payload bytes instead of a parsed dataclass.

    `function_types` restricts the fetch to those `FITNESS_TYPE_*` buckets;
    the default of None fetches every bucket the watch offers.
    """
    results: list[tuple[protocol.FitnessTypeEntry, object]] = []
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_request(protocol.CMD_GET_FITNESS_TYPE_ID_LIST))
        list_payload = await session.receive_message()
        entries = protocol.parse_fitness_type_id_list(list_payload)
        if function_types is not None:
            entries = [entry for entry in entries if entry.function_type in function_types]

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


def is_real_time_data_report(payload: bytes) -> bool:
    """Return whether a payload is a REPORT_BASIC_DATA (165) message."""
    fields = protocol.decode_protobuf(payload)
    command = fields.get(1, [None])[0]
    return (
        command is not None
        and command.wire_type == 0
        and command.raw == protocol.CMD_REPORT_BASIC_DATA
        and 12 in fields
    )


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
            if not is_real_time_data_report(payload):
                continue
            readings.append(protocol.parse_real_time_data(payload))
    return readings


async def request_current_heart_rate(address: str, seconds: float = 30, attempts: int = 3) -> int:
    """Return one current non-zero BPM value, retrying transient BLE failures.

    The live stream has a generic command acknowledgment before its data
    reports and the watch occasionally rejects a connection while waking up.
    Both cases are handled here so callers can use one command instead of
    manually disconnecting, waiting, and retrying.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if seconds <= 0:
        raise ValueError("seconds must be positive")

    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            readings = await enable_real_time_data_and_listen(address, seconds)
        except (ConnectionError, RuntimeError, TimeoutError, asyncio.TimeoutError) as error:
            last_error = error
            continue
        for reading in reversed(readings):
            if reading.heart_rate > 0:
                return reading.heart_rate
        last_error = HeartRateUnavailableError("the watch sent no non-zero heart-rate measurement")

    raise HeartRateUnavailableError(
        f"No current heart-rate reading after {attempts} attempt(s). "
        "Keep the watch awake and firmly on your wrist, then retry."
    ) from last_error


async def bind_watch(
    address: str,
    user_id: str,
    phone_type: int = protocol.PHONE_TYPE_ANDROID,
) -> BindOutcome:
    """Perform the verified minimal application-binding sequence.

    This deliberately sends no time, language, clock-format, real-time-data,
    Classic Bluetooth, or HFP setup. Binding is accepted only when command 0
    confirms MTU 247, command 18 succeeds, and command 16 reports bound.
    """
    if not user_id:
        raise ValueError("user_id must not be empty")

    async with GatttoolSession(address, att_mtu=247) as session:
        await session.send_message(protocol.encode_mtu_request_change())
        mtu_response = await session.receive_message()
        mtu = protocol.parse_mtu_response(mtu_response)
        if mtu != 247:
            raise RuntimeError(f"watch confirmed ATT MTU {mtu}, expected 247")

        await session.send_message(protocol.encode_binding_check_request())
        binding_check_response = await session.receive_message()

        await session.send_message(protocol.encode_binding_result_request(user_id, phone_type))
        binding_result_response = await session.receive_message()
        result_status = protocol.parse_generic_response_status(
            binding_result_response, protocol.CMD_BINDING_RESULT
        )
        if result_status != 0:
            raise RuntimeError(f"binding result failed with status {result_status}")

        watch_messages = await session.receive_pending_messages(grace_seconds=1.0)

        await session.send_message(protocol.encode_request(protocol.CMD_INQUIRY_BINDING_STATUS))
        binding_status_response = await session.receive_message()
        bound = protocol.parse_binding_status_response(binding_status_response)
        if not bound:
            raise RuntimeError("watch accepted command 18 but still reports unbound")

        watch_messages.extend(await session.receive_pending_messages(grace_seconds=1.0))
        timestamp, utc_offset_eighth_hours = _local_time_parameters()
        await session.send_message(
            protocol.encode_set_system_time_request(timestamp, utc_offset_eighth_hours)
        )
        time_response = await session.receive_message()
        time_status = protocol.parse_generic_response_status(
            time_response, protocol.CMD_SET_SYSTEM_TIME
        )
        if time_status != 0:
            raise RuntimeError(f"setting system time failed with status {time_status}")

    return BindOutcome(
        mtu=mtu,
        binding_check_response=binding_check_response,
        binding_result_response=binding_result_response,
        binding_status_response=binding_status_response,
        bound=bound,
        timestamp=timestamp,
        utc_offset_eighth_hours=utc_offset_eighth_hours,
        time_response=time_response,
        watch_messages=tuple(watch_messages),
    )


def _local_time_parameters(now: dt.datetime | None = None) -> tuple[int, int]:
    """Return Unix seconds and this firmware's UTC-offset units (eight/hour)."""
    local_now = (now or dt.datetime.now().astimezone()).astimezone()
    offset = local_now.utcoffset() or dt.timedelta()
    return int(local_now.timestamp()), int(offset.total_seconds() / 450)


async def set_watch_time(address: str) -> tuple[int, int, bytes, tuple[bytes, ...]]:
    """Negotiate MTU 247 and synchronize the watch with this host's clock."""
    timestamp, utc_offset_eighth_hours = _local_time_parameters()
    async with GatttoolSession(address, att_mtu=247) as session:
        watch_messages = await session.receive_pending_messages(grace_seconds=2.0)
        await session.send_message(protocol.encode_mtu_request_change())
        mtu_response = await session.receive_message()
        mtu = protocol.parse_mtu_response(mtu_response)
        if mtu != 247:
            raise RuntimeError(f"watch confirmed ATT MTU {mtu}, expected 247")

        await session.send_message(
            protocol.encode_set_system_time_request(timestamp, utc_offset_eighth_hours)
        )
        response = await session.receive_message()
        status = protocol.parse_generic_response_status(response, protocol.CMD_SET_SYSTEM_TIME)
        if status != 0:
            raise RuntimeError(f"setting system time failed with status {status}")
    return timestamp, utc_offset_eighth_hours, response, tuple(watch_messages)


async def send_notification(
    address: str,
    notification_type: int,
    phone_number: str = "",
    contacts_info: str = "",
    message_text: str = "",
    attempts: int = 8,
) -> bytes:
    """Connect and push a SEND_SYSTEM_NOTIFICATION (178) to the watch.

    A valid application binding is required for display. Type `message`
    renders `contacts_info` and `message_text` as a transient system
    notification. Returns the generic response payload
    `{1: 178, 100: <code>}`.

    Retries transient `TimeoutError`/`ConnectionError` up to `attempts`
    times, 5s apart.
    """
    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            async with GatttoolSession(address, att_mtu=247) as session:
                await session.send_message(
                    protocol.encode_system_notification_request(
                        notification_type, phone_number, contacts_info, message_text
                    )
                )
                return await session.receive_message()
        except (ConnectionError, RuntimeError, TimeoutError, asyncio.TimeoutError) as error:
            last_error = error
            await asyncio.sleep(5.0)
    assert last_error is not None
    raise last_error


async def send_app_notification(
    address: str,
    app_name: str = "",
    page_name: str = "",
    title: str = "",
    text: str = "",
    ticker_text: str = "",
    attempts: int = 8,
) -> bytes:
    """Connect and push a SEND_APP_NOTIFICATION (179) to the watch.

    The app-notification sibling of `send_notification` (178): the path the
    official app itself uses for every third-party notification (WhatsApp,
    Slack, ...), via MyNotificationsService -> ControlBleTools.sendAppNotification
    -> com.zhapp.ble.a.a(179, ...). Live-tested 2026-08-30, BLE-only: acked
    `{1: 179, 100: 0}` and displayed on the watch (user-confirmed), with no
    precondition commands needed -- none are sent, matching the removal of
    `send_notification`'s `warmup` the same day.

    Retries transient `TimeoutError`/`ConnectionError` (the flakiness
    documented for every other write command, see TODO.md's "BLE flakiness"
    entry) up to `attempts` times, 5s apart.
    """
    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            async with GatttoolSession(address, att_mtu=247) as session:
                await session.send_message(
                    protocol.encode_app_notification_request(app_name, page_name, title, text, ticker_text)
                )
                return await session.receive_message()
        except (ConnectionError, RuntimeError, TimeoutError, asyncio.TimeoutError) as error:
            last_error = error
            await asyncio.sleep(5.0)
    assert last_error is not None
    raise last_error


async def request_workout_data(address: str) -> protocol.WorkoutData:
    """Connect and fetch the watch's currently-queued workout data (steps/GPS/etc).

    Follows the real sequence observed in a live workout-sync capture
    (2026-08-29, GPS-tracked walk, see protocol.md's "Workout data" section):
    GET_FITNESS_SPORT_ID_LIST (117) -> REQUEST_FITNESS_SPORT_DATA (119) ->
    drain the reply's rounds (each round's header can land on either the
    activity channel 6f03 or the normal command-response channel 6f01 --
    live-tested 2026-08-29, see `receive_all_activity_data`) until quiet ->
    CONFIRM_FITNESS_SPORT_ID_LIST (121, acked normally). Returns
    `WorkoutData(entries=[], ...)` with everything else `None` if there's
    currently nothing queued (nothing to sync).

    CONFIRM is only sent once `split_sport_data_blobs` has actually located
    every requested entry in the received bytes: live-tested 2026-08-29, an
    earlier version of this function sent CONFIRM unconditionally whenever
    the bulk transfer's own ack was flaky, even when `combined` turned out
    to be empty or incomplete -- telling the watch the data was delivered
    when it wasn't, after which the watch stopped offering the real payload
    for that entry (a `{1: 27, ...}` status reply carrying nothing but its
    own MAC address came back instead, repeated verbatim on retry). Never
    confirm receipt of data that wasn't actually verified as received.

    Only the **most recent** queued workout's entries are requested (see
    `protocol.latest_workout_entries`): the queue can hold more than one
    past workout at once, and bundling every queued entry into one request
    means a single unfetchable one (e.g. a previously stale-confirmed
    workout) blocks every other workout's data too, since a bundled
    transfer must contain every requested entry to be split successfully.
    """
    async with GatttoolSession(address) as session:
        await session.send_message(protocol.encode_request(protocol.CMD_GET_FITNESS_SPORT_ID_LIST))
        list_payload = await session.receive_message()
        all_entries = protocol.parse_sport_id_list(list_payload)
        if not all_entries:
            return protocol.WorkoutData(entries=[], report=None, gps_track=None, point_data_raw=None)
        entries = protocol.latest_workout_entries(all_entries)

        ids_blob = b"".join(entry.raw for entry in entries)
        await session.send_message(
            protocol.encode_fitness_sport_id_list_request(protocol.CMD_REQUEST_FITNESS_SPORT_DATA, ids_blob)
        )
        combined = await session.receive_all_activity_data()
        try:
            blobs = protocol.split_sport_data_blobs(combined, entries)
        except ValueError as error:
            raise RuntimeError(
                f"incomplete sport data for entries {entries!r} ({len(combined)} bytes received); "
                f"not confirming, so the watch keeps offering it: {error}"
            ) from error

        try:
            # Best-effort: `blobs` is already parsed and safe by this point,
            # so a flaky ack on the confirm itself (the same transport-wide
            # issue documented in TODO.md, not specific to this command)
            # shouldn't discard it. We still try to confirm so the watch can
            # dequeue the entry, but don't let a failure here lose data we
            # already have.
            await session.send_message(
                protocol.encode_fitness_sport_id_list_request(protocol.CMD_CONFIRM_FITNESS_SPORT_ID_LIST, ids_blob)
            )
            await session.receive_message()  # generic ack -- not checked
        except (TimeoutError, asyncio.TimeoutError, RuntimeError):
            pass

    report = protocol.parse_workout_report(blobs[protocol.SPORT_DATA_REPORT]) if protocol.SPORT_DATA_REPORT in blobs else None
    gps_track = protocol.parse_gps_track(blobs[protocol.SPORT_DATA_GPS]) if protocol.SPORT_DATA_GPS in blobs else None
    return protocol.WorkoutData(
        entries=entries,
        report=report,
        gps_track=gps_track,
        point_data_raw=blobs.get(protocol.SPORT_DATA_POINT),
    )
