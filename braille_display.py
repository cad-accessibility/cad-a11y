#!/usr/bin/env python3
"""Braille display communication (Monarch USB HID + DotPad BLE).

Public API (kept from the previous version):
- send_to_braille_display(array, ...)

This module auto-selects a supported device connected/available on the machine:
- Monarch (USB HID): VID/PID 0x1C71/0xD110 (existing implementation)
- DotPad (BLE): device name prefix "DotPad" + known service/characteristic UUIDs

DotPad supports two outputs:
- Graphic area (300 cells)
- Text area (20 cells) via optional parameter to send_to_braille_display

Dependencies:
- numpy
- hidapi ("hid" Python package)
- bleak (only required when talking to DotPad over BLE)
"""

from __future__ import annotations

import atexit
import asyncio
import concurrent.futures
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol, Tuple, runtime_checkable

import numpy as np

import hid


class BrailleDisplayError(Exception):
    """Exception raised for braille display communication errors."""


@runtime_checkable
class _Closable(Protocol):
    def close(self) -> None: ...


# --- Monarch (USB HID) constants (kept as the original implementation) ---

_MONARCH_VENDOR_ID = 0x1C71
_MONARCH_PRODUCT_ID = 0xD110
_MONARCH_BUFFER_SIZE = 481
_MONARCH_COMMAND_BYTE = 0x21
_MONARCH_LINES = 10
_MONARCH_COLS = 48


# --- DotPad (BLE) constants ---

_DOTPAD_NAME_PREFIX = "DotPad"
_DOTPAD_SERVICE_UUID = "49535343-fe7d-4ae5-8fa9-9fafd205e455"
_DOTPAD_CHARACTERISTIC_UUID = "49535343-1e4d-4bd9-ba61-23c647249616"

_DOTPAD_GRAPHIC_CELLS = 300
_DOTPAD_LINES = 10
_DOTPAD_COLS = 30

_DOTPAD_TEXT_CELLS = 20


@dataclass(frozen=True)
class _ConnectedDevice:
    kind: str  # "monarch" | "dotpad"
    handle: object


def send_to_braille_display(
    array: np.ndarray,
    *,
    dot_text_hex_data: str | None = None,
    scan_timeout: float = 6.0,
) -> int:
    """Send an array to whichever supported braille display is available.

    Args:
        array: 2D array (H, W) or 3D array (H, W, 1). Values > 0 are "raised".
        dot_text_hex_data: If provided and a DotPad is used, also write the DotPad
            text area (20 cells) using this hex-encoded braille data.
            If no DotPad is available, raises BrailleDisplayError.
        scan_timeout: BLE scan timeout (seconds) used when DotPad discovery is needed.

    Returns:
        Number of bytes written to the underlying transport.

    Raises:
        BrailleDisplayError: Device connection or data transmission failure.
        ValueError: If array shape is incorrect.
    """

    array_2d = _normalize_array(array)
    device = _connect(scan_timeout=scan_timeout, prefer_dotpad=dot_text_hex_data is not None)

    if device.kind == "monarch":
        if dot_text_hex_data is not None:
            raise BrailleDisplayError(
                "dot_text_hex_data was provided but the connected device is Monarch (no text area)"
            )
        return _send_to_monarch(device.handle, array_2d)

    if device.kind == "dotpad":
        # DotPad connection is persistent (kept open until program exit).
        return _send_to_dotpad(
            address=str(device.handle),
            array_2d=array_2d,
            dot_text_hex_data=dot_text_hex_data,
        )

    raise BrailleDisplayError(f"Unsupported device kind: {device.kind}")


def _normalize_array(array: np.ndarray) -> np.ndarray:
    if not isinstance(array, np.ndarray):
        raise ValueError("Input must be a numpy array")
    if array.ndim == 3 and array.shape[-1] == 1:
        return array.squeeze(-1)
    if array.ndim == 2:
        return array
    raise ValueError(f"Array must be 2D or (H, W, 1); got shape {array.shape}")


def _connect(*, scan_timeout: float, prefer_dotpad: bool) -> _ConnectedDevice:
    """Pick whichever supported device is available.

    Connection order:
    1) Try DotPad BLE (existing connection first, then scan)
    2) If no DotPad is present, try Monarch via HID enumeration filtering
       manufacturer == Humanware, then open using discovered VID/PID.
    """

    # 1) Try DotPad as usual.
    existing = _dotpad_manager_if_connected()
    if existing is not None:
        return _ConnectedDevice(kind="dotpad", handle=existing.address)

    address = _dotpad_best_address(scan_timeout=scan_timeout)
    if address is not None:
        return _ConnectedDevice(kind="dotpad", handle=address)

    # If DotPad-only features were requested and no DotPad was found, fail early.
    if prefer_dotpad:
        raise BrailleDisplayError(
            "dot_text_hex_data requested but no DotPad was found."
        )

    # 2) If no DotPad, find Monarch by HID manufacturer and connect with discovered IDs.
    monarch = _find_humanware_hid_device()
    if monarch is not None:
        vendor_id = monarch.get("vendor_id")
        product_id = monarch.get("product_id")
        if vendor_id is None or product_id is None:
            raise BrailleDisplayError(
                "Found Humanware HID device but vendor_id/product_id are missing"
            )
        h = hid.device()
        h.open(int(vendor_id), int(product_id))
        return _ConnectedDevice(kind="monarch", handle=h)

    raise BrailleDisplayError(
        "No supported braille display detected. Expected DotPad (BLE) or Humanware HID device."
    )


def _find_humanware_hid_device() -> dict | None:
    """Return the first HID device whose manufacturer string contains 'humanware'."""

    for dev in hid.enumerate():
        manufacturer = (dev.get("manufacturer_string") or "").strip().lower()
        if "humanware" in manufacturer:
            return dev
    return None


def _send_to_monarch(device: hid.device, array_2d: np.ndarray) -> int:
    buf = bytearray(_MONARCH_BUFFER_SIZE)
    buf[0] = _MONARCH_COMMAND_BYTE
    _convert_pixels_to_braille(buf, array_2d)

    try:
        bytes_written = device.write(bytes(buf))
        if bytes_written <= 0:
            raise BrailleDisplayError("Failed to write data to Monarch")
        return int(bytes_written)
    except Exception as exc:
        raise BrailleDisplayError(f"Monarch communication failed: {exc}")
    finally:
        try:
            device.close()
        except Exception:
            pass


def _send_to_dotpad(
    *,
    address: str,
    array_2d: np.ndarray,
    dot_text_hex_data: str | None,
) -> int:
    manager = _get_dotpad_manager(address)
    try:
        return manager.send(array_2d=array_2d, dot_text_hex_data=dot_text_hex_data)
    except BrailleDisplayError:
        raise
    except Exception as exc:
        raise BrailleDisplayError(f"DotPad communication failed: {exc}")


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise BrailleDisplayError(
        "send_to_braille_display() was called from within an existing asyncio event loop. "
        "Use the DotPad BLE SDK directly (dotpad/Python/1.0.0/dotpad_ble_sdk.py) in async contexts."
    )


def _dotpad_best_address(*, scan_timeout: float) -> str | None:
    try:
        return _run_async(_dotpad_best_address_async(scan_timeout=scan_timeout))
    except BrailleDisplayError:
        raise
    except Exception:
        return None


async def _dotpad_best_address_async(*, scan_timeout: float) -> str | None:
    try:
        from bleak import BleakScanner
    except ImportError as exc:
        raise BrailleDisplayError(
            "Missing dependency 'bleak' required for DotPad BLE. Install with: pip install bleak"
        ) from exc

    # Use advertisement callback so we can match on advertised service UUIDs.
    # This is more robust than relying solely on the BLE device name.
    candidates: dict[str, Tuple[int, str]] = {}

    def on_adv(device, adv_data):
        name = (device.name or adv_data.local_name or "")
        uuids = set((adv_data.service_uuids or []))
        has_service = _DOTPAD_SERVICE_UUID.lower() in {u.lower() for u in uuids}
        name_match = bool(name) and name.lower().startswith(_DOTPAD_NAME_PREFIX.lower())
        if not (has_service or name_match):
            return
        rssi = int(getattr(device, "rssi", -9999))
        candidates[device.address] = (rssi, name)

    scanner = BleakScanner(detection_callback=on_adv)
    await scanner.start()
    try:
        await asyncio.sleep(scan_timeout)
    finally:
        await scanner.stop()

    if not candidates:
        return None

    # Strongest RSSI wins.
    best_addr, _ = max(candidates.items(), key=lambda item: item[1][0])
    return best_addr




# --- Shared pixel->braille conversion ---


def _pixels_to_braille_cells(array_2d: np.ndarray, *, lines: int, cols: int) -> bytes:
    """Convert a pixel array into packed braille-cell bytes.

    Each cell is a 4x2 block mapped to 8-dot braille bits:
      (0,0)->bit0, (1,0)->bit1, (2,0)->bit2,
      (0,1)->bit3, (1,1)->bit4, (2,1)->bit5,
      (3,0)->bit6, (3,1)->bit7
    """

    out = bytearray(lines * cols)
    height, width = array_2d.shape

    for line in range(lines):
        pixel_row_start = line * 4
        if pixel_row_start + 3 >= height:
            break
        for col in range(cols):
            pixel_col_start = col * 2
            if pixel_col_start + 1 >= width:
                break

            b = 0
            if array_2d[pixel_row_start + 0, pixel_col_start + 0] > 0:
                b |= 1 << 0
            if array_2d[pixel_row_start + 1, pixel_col_start + 0] > 0:
                b |= 1 << 1
            if array_2d[pixel_row_start + 2, pixel_col_start + 0] > 0:
                b |= 1 << 2
            if array_2d[pixel_row_start + 0, pixel_col_start + 1] > 0:
                b |= 1 << 3
            if array_2d[pixel_row_start + 1, pixel_col_start + 1] > 0:
                b |= 1 << 4
            if array_2d[pixel_row_start + 2, pixel_col_start + 1] > 0:
                b |= 1 << 5
            if array_2d[pixel_row_start + 3, pixel_col_start + 0] > 0:
                b |= 1 << 6
            if array_2d[pixel_row_start + 3, pixel_col_start + 1] > 0:
                b |= 1 << 7

            out[line * cols + col] = b

    return bytes(out)


def _convert_pixels_to_braille(buf: bytearray | list[int], array_2d: np.ndarray) -> None:
    """Backwards-compatible converter for Monarch buffer format.

    This fills `buf` in-place starting at offset 1.
    """

    cells = _pixels_to_braille_cells(array_2d, lines=_MONARCH_LINES, cols=_MONARCH_COLS)
    for i, b in enumerate(cells, start=1):
        if i >= len(buf):
            break
        buf[i] = b


# --- DotPad BLE protocol (ported from Web SDK) ---


class _DotPadProtocol:
    @staticmethod
    def checksum(*parts: bytes) -> bytes:
        value = 0xA5
        for part in parts:
            for byte in part:
                value ^= int(byte)
        return bytes([value & 0xFF])


class _DotPadData:
    @staticmethod
    def request_frames(body: bytes, *, is_text_mode: bool) -> list[bytes]:
        frames: list[bytes] = []
        chunks = list(_DotPadData._chunk(body, 30))
        for dest_id, chunk in enumerate(chunks, start=1):
            frames.append(
                _DotPadData._frame(
                    dest_id=dest_id,
                    start_cell=0,
                    body=chunk,
                    is_text_mode=is_text_mode,
                )
            )
        return frames

    @staticmethod
    def request_line_frame(*, dest_id: int, start_cell: int, body: bytes, is_text_mode: bool) -> bytes:
        return _DotPadData._frame(dest_id=dest_id, start_cell=start_cell, body=body, is_text_mode=is_text_mode)

    @staticmethod
    def _chunk(data: bytes, size: int) -> Iterable[bytes]:
        for i in range(0, len(data), size):
            yield data[i : i + size]

    @staticmethod
    def _frame(*, dest_id: int, start_cell: int, body: bytes, is_text_mode: bool) -> bytes:
        sync = bytes([0xAA, 0x55])
        length = bytes([0x00, (len(body) + 6) & 0xFF])
        dest = bytes([dest_id & 0xFF])
        cmd = bytes([0x02, 0x00])
        mode = bytes([0x80 if is_text_mode else 0x00])
        start = bytes([start_cell & 0xFF])
        checksum = _DotPadProtocol.checksum(dest, cmd, mode, start, body)
        return sync + length + dest + cmd + mode + start + body + checksum


class _BrailleWordWrap:
    DOUBLE_ZERO = "00"

    def __init__(self, cell_count: int, braille_hex_data: str):
        self._cell_size_hex = 2 * cell_count
        self._data = braille_hex_data

    def to_wrapped_pages(self) -> list[str]:
        segments = self._process_segments()
        pages: list[str] = []
        cur: list[str] = []
        for seg in segments:
            if len("".join(cur)) + len(seg) > self._cell_size_hex:
                if cur:
                    pages.append(self._pad("".join(cur)))
                    cur.clear()
            cur.append(seg)
        if cur:
            pages.append(self._pad("".join(cur)))
        return pages

    def _process_segments(self) -> list[str]:
        raw = self._data
        ends = raw.strip().endswith(self.DOUBLE_ZERO)
        parts = raw.split(self.DOUBLE_ZERO)
        segs = ["".join(p.split()) + self.DOUBLE_ZERO for p in parts]
        if not ends and segs:
            segs[-1] = segs[-1][:-2]
        return [s for s in segs if s]

    def _pad(self, s: str) -> str:
        while len(s) < self._cell_size_hex:
            s += "0"
        return s


class _AsyncLoopThread:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise BrailleDisplayError("Failed to start background asyncio loop")

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        assert self._loop is not None
        return self._loop

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()

    def submit(self, coro, *, timeout: float | None = None):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)


class _DotPadBleConnection:
    def __init__(self, address: str):
        self.address = address
        self._client = None

        # ACK matching as per Web SDK.
        self._ack_re = re.compile(r"aa550006(..)0201(..)00")
        self._acked_ids: set[int] = set()
        self._ack_events: dict[int, asyncio.Event] = {}

    async def connect(self) -> None:
        if self._client is not None and getattr(self._client, "is_connected", False):
            return

        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise BrailleDisplayError(
                "Missing dependency 'bleak' required for DotPad BLE. Install with: pip install bleak"
            ) from exc

        self._client = BleakClient(self.address)
        await self._client.connect()
        # Bleak expects a normal (non-async) callback for notifications.
        await self._client.start_notify(_DOTPAD_CHARACTERISTIC_UUID, self._on_notify)

    async def disconnect(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.stop_notify(_DOTPAD_CHARACTERISTIC_UUID)
        except Exception:
            pass
        try:
            await self._client.disconnect()
        except Exception:
            pass
        self._client = None

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        hx = bytes(data).hex()
        m = self._ack_re.search(hx)
        if not m:
            return
        dest_id = int(m.group(1), 16)

        ev = self._ack_events.get(dest_id)
        if ev is not None:
            ev.set()
        else:
            self._acked_ids.add(dest_id)

    async def _wait_ack(self, dest_id: int, *, timeout: float) -> None:
        if dest_id in self._acked_ids:
            self._acked_ids.remove(dest_id)
            return

        ev = asyncio.Event()
        self._ack_events[dest_id] = ev
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        finally:
            self._ack_events.pop(dest_id, None)

    async def send(self, *, array_2d: np.ndarray, dot_text_hex_data: str | None) -> int:
        await self.connect()
        assert self._client is not None

        bytes_written = 0

        # Graphic area (10x30 cells = 300 bytes)
        graphic_cells = _pixels_to_braille_cells(array_2d, lines=_DOTPAD_LINES, cols=_DOTPAD_COLS)
        body = graphic_cells[:_DOTPAD_GRAPHIC_CELLS].ljust(_DOTPAD_GRAPHIC_CELLS, b"\x00")
        frames = _DotPadData.request_frames(body, is_text_mode=False)

        for dest_id, frame in enumerate(frames, start=1):
            await self._client.write_gatt_char(
                _DOTPAD_CHARACTERISTIC_UUID,
                frame,
                response=True,
            )
            bytes_written += len(frame)
            await self._wait_ack(dest_id, timeout=2.0)

        # Optional text area
        if dot_text_hex_data is not None:
            pages = _BrailleWordWrap(_DOTPAD_TEXT_CELLS, dot_text_hex_data).to_wrapped_pages()
            if not pages:
                raise BrailleDisplayError("dot_text_hex_data was provided but produced no pages")

            text_body = bytes.fromhex(pages[0])
            text_body = text_body[:_DOTPAD_TEXT_CELLS].ljust(_DOTPAD_TEXT_CELLS, b"\x00")
            text_frame = _DotPadData.request_line_frame(
                dest_id=0,
                start_cell=0,
                body=text_body,
                is_text_mode=True,
            )
            await self._client.write_gatt_char(
                _DOTPAD_CHARACTERISTIC_UUID,
                text_frame,
                response=True,
            )
            bytes_written += len(text_frame)

        return int(bytes_written)


class _DotPadManager:
    def __init__(self, address: str):
        self.address = address
        self._loop_thread = _AsyncLoopThread()
        self._conn = _DotPadBleConnection(address)
        self._closed = False
        self._send_lock = threading.Lock()

    def is_connected(self) -> bool:
        # Best-effort (not guaranteed accurate without crossing threads)
        return not self._closed

    def send(self, *, array_2d: np.ndarray, dot_text_hex_data: str | None) -> int:
        # Serialize sends from non-async callers.
        with self._send_lock:
            return int(
                self._loop_thread.submit(
                    self._conn.send(array_2d=array_2d, dot_text_hex_data=dot_text_hex_data),
                    timeout=30.0,
                )
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._loop_thread.submit(self._conn.disconnect(), timeout=5.0)
        except Exception:
            pass
        try:
            self._loop_thread.stop()
        except Exception:
            pass


_DOTPAD_MANAGER: _DotPadManager | None = None


def _dotpad_manager_if_connected() -> _DotPadManager | None:
    global _DOTPAD_MANAGER
    if _DOTPAD_MANAGER is None:
        return None
    if _DOTPAD_MANAGER.is_connected():
        return _DOTPAD_MANAGER
    return None


def _get_dotpad_manager(address: str) -> _DotPadManager:
    global _DOTPAD_MANAGER

    if _DOTPAD_MANAGER is not None:
        # If a manager exists for a different address, close it and replace.
        if _DOTPAD_MANAGER.address != address:
            _DOTPAD_MANAGER.close()
            _DOTPAD_MANAGER = None

    if _DOTPAD_MANAGER is None:
        _DOTPAD_MANAGER = _DotPadManager(address)
        atexit.register(_DOTPAD_MANAGER.close)

    return _DOTPAD_MANAGER


def _make_happy_face_pixels(*, height: int, width: int) -> np.ndarray:
    """Create a simple happy face as a binary pixel image (values 0/1).

    Pixel coordinates map to braille cells as 4x2 blocks.
    """

    img = np.zeros((height, width), dtype=np.uint8)
    cy = (height - 1) / 2.0
    cx = (width - 1) / 2.0
    r = max(3.0, min(height, width) * 0.45)

    yy, xx = np.ogrid[:height, :width]
    dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)

    # Face outline (thin ring)
    outline = (dist >= r - 1.2) & (dist <= r + 0.8)
    img[outline] = 1

    # Eyes (filled circles)
    eye_r = max(1.0, r * 0.08)
    left_eye = ((yy - (cy - r * 0.25)) ** 2 + (xx - (cx - r * 0.28)) ** 2) <= eye_r**2
    right_eye = ((yy - (cy - r * 0.25)) ** 2 + (xx - (cx + r * 0.28)) ** 2) <= eye_r**2
    img[left_eye | right_eye] = 1

    # Smile (arc of a smaller circle, lower half only)
    mouth_cy = cy + r * 0.18
    mouth_r = r * 0.55
    mouth_dist = np.sqrt((yy - mouth_cy) ** 2 + (xx - cx) ** 2)
    mouth = (mouth_dist >= mouth_r - 1.2) & (mouth_dist <= mouth_r + 0.8) & (yy > cy)
    img[mouth] = 1

    return img


def _device_pixel_dims(kind: str) -> Tuple[int, int]:
    """Return (height, width) in pixels for the target device."""

    if kind == "monarch":
        return _MONARCH_LINES * 4, _MONARCH_COLS * 2
    if kind == "dotpad":
        return _DOTPAD_LINES * 4, _DOTPAD_COLS * 2
    raise BrailleDisplayError(f"Unknown device kind: {kind}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Send a happy face to the connected braille display (Monarch or DotPad)."
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=6.0,
        help="BLE scan timeout (seconds) when looking for a DotPad",
    )
    args = parser.parse_args()

    # Detect which device we will use, so we can size the image appropriately.
    device = _connect(scan_timeout=args.scan_timeout, prefer_dotpad=False)
    height, width = _device_pixel_dims(device.kind)
    print(f"Detected device: {device.kind} (image {height}x{width} pixels)")

    # Close Monarch HID handle opened by _connect; send_to_braille_display will re-open.
    if device.kind == "monarch":
        handle = device.handle
        if isinstance(handle, _Closable):
            try:
                handle.close()
            except Exception:
                pass

    img = _make_happy_face_pixels(height=height, width=width)

    bytes_written = send_to_braille_display(img, scan_timeout=args.scan_timeout)
    print(f"Sent happy face (bytes_written={bytes_written})")

    if device.kind == "dotpad":
        print("DotPad is still connected. Press Ctrl+C to exit (disconnects on shutdown).")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
