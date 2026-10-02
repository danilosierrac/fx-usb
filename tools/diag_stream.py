"""Compare on-device reader variants: lost DMA halves, torn copies, host arrival gaps.

Runs one bounded capture (RAM only on the mic, nothing written to its flash), stops the
reader with Ctrl-C, stops any test sample, and removes the helper globals.
"""
import argparse
import json
import os
from pathlib import Path
import select
import struct
import termios
import time
import tty
import wave

import numpy as np

from inspect_device import exchange

PERIOD_US = 32e6 / 56250  # one 32-frame DMA half at 56,250 Hz

COMMON = '''
import micropython, uctypes, struct, sys, time
@micropython.viper
def _fxd_wait(previous: int) -> int:
    r = ptr32(0x50000000)
    count = 0
    while (int(r[0]) & 256) == previous and count < 200000:
        count += 1
    return int(r[0]) & 256
@micropython.viper
def _fxd_copy(source: int, dest):
    src = ptr32(source)
    dst = ptr16(dest)
    for i in range(32):
        dst[8+i] = src[2*i] >> 16
def _fxd_info():
    info = struct.unpack('<4I', uctypes.bytes_at(0x50000000,16))
    if info[1] != 0x50200010 or ((info[3] >> 8) & 15) != 9:
        raise ValueError('Unsupported audio layout')
    return info[0] & ~511, info[0] & 256
'''

# A: the shipped app loop (native emitter, one write per half).
VARIANT_A = '''
@micropython.native
def _fxd_run(blocks):
    base, previous = _fxd_info()
    packet = bytearray(80)
    seq = 0
    while seq < blocks:
        half = _fxd_wait(previous)
        stamp = time.ticks_us()
        _fxd_copy(base+half, packet)
        struct.pack_into('<4sIII', packet, 0, b'FXP1', seq, stamp, int(half == previous))
        sys.stdout.buffer.write(packet)
        previous = half
        seq += 1
    print('FXD_DONE')
'''

# B: bytecode outer loop (services USB + button callbacks every pass); one viper call does
# wait, hardware timestamp, copy, header and a torn-copy check. K halves per USB write.
VARIANT_B = '''
@micropython.viper
def _fxd_fill(buf, k: int, previous: int, seq: int) -> int:
    r = ptr32(0x50000000)
    t = ptr32(0x400b0028)
    out32 = ptr32(buf)
    out16 = ptr16(buf)
    base = (int(r[0]) >> 9) << 9
    for j in range(k):
        count = 0
        while (int(r[0]) & 256) == previous and count < 200000:
            count += 1
        half = int(r[0]) & 256
        stamp = int(t[0]) & 0x3fffffff
        src = ptr32(base + half)
        o = j*20
        for i in range(32):
            out16[2*o+8+i] = src[2*i] >> 16
        flags = 0
        if (int(r[0]) & 256) != half:
            flags = 2
        if half == previous:
            flags |= 1
        out32[o] = 0x31505846
        out32[o+1] = seq + j
        out32[o+2] = stamp
        out32[o+3] = flags
        previous = half
    return previous
def _fxd_run(blocks, k=1):
    _fxd_info()
    buf = bytearray(80*k)
    previous = ptr = 0
    previous = struct.unpack('<I', uctypes.bytes_at(0x50000000,4))[0] & 256
    write = sys.stdout.buffer.write
    seq = 0
    while seq < blocks:
        previous = _fxd_fill(buf, k, previous, seq)
        write(buf)
        seq += k
    print('FXD_DONE')
'''

# C: the wait itself runs in bytecode, so every spin passes the VM's pending check and the
# mic's USB task (and button callbacks) run promptly. Viper only stamps and copies.
VARIANT_C = '''
@micropython.viper
def _fxd_half() -> int:
    return int(ptr32(0x50000000)[0]) & 256
@micropython.viper
def _fxd_grab(buf, j: int, seq: int, previous: int) -> int:
    r = ptr32(0x50000000)
    half = int(r[0]) & 256
    stamp = int(ptr32(0x400b0028)[0]) & 0x3fffffff
    src = ptr32(((int(r[0]) >> 9) << 9) + half)
    out32 = ptr32(buf)
    out16 = ptr16(buf)
    o = j*20
    for i in range(32):
        out16[2*o+8+i] = src[2*i] >> 16
    flags = 0
    if (int(r[0]) & 256) != half:
        flags = 2
    out32[o] = 0x31505846
    out32[o+1] = seq
    out32[o+2] = stamp
    out32[o+3] = flags
    return half
def _fxd_run(blocks, k=1):
    _fxd_info()
    buf = bytearray(80*k)
    write = sys.stdout.buffer.write
    half = _fxd_half
    grab = _fxd_grab
    previous = half()
    seq = 0
    j = 0
    while seq < blocks:
        while half() == previous:
            pass
        previous = grab(buf, j, seq, previous)
        seq += 1
        j += 1
        if j == k:
            write(buf)
            j = 0
    print('FXD_DONE')
'''

VARIANTS = {'A': VARIANT_A, 'B': VARIANT_B, 'C': VARIANT_C, 'APP': None}
READER = Path(__file__).resolve().parent.parent / 'Sources' / 'fxmic_reader.py'


def program_chunks(source):
    """Split a device program into top-level statements without comments, so the mic's
    small, fragmented heap only ever compiles one function at a time."""
    chunks, current = [], []
    for line in source.splitlines():
        if not line.strip() or line.strip().startswith('#'):
            continue
        indent = len(line) - len(line.lstrip(' '))
        line = ' ' * (indent // 4) + line.lstrip(' ')
        top = indent == 0
        if top and current and not current[-1].startswith('@'):
            chunks.append('\n'.join(current) + '\n')
            current = []
        current.append(line)
    if current:
        chunks.append('\n'.join(current) + '\n')
    return chunks


def capture(port, variant, blocks, k, sample, settle):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    data = bytearray()
    arrivals = []
    try:
        tty.setraw(fd)
        os.write(fd, b'\x03\r')
        time.sleep(0.1)
        while select.select([fd], [], [], 0)[0]:
            os.read(fd, 65536)
        program = READER.read_text() if variant == 'APP' else COMMON + VARIANTS[variant]
        for chunk in program_chunks(program):
            exchange(fd, 'exec(' + repr(chunk) + ')')
        if sample is not None:
            exchange(fd, 'spl.trigger(-1, %d, True)' % sample)
            time.sleep(settle)
        call = {'A': '_fxd_run(%d)' % blocks, 'APP': '_fxu_run()'}.get(variant, '_fxd_run(%d, %d)' % (blocks, k))
        os.write(fd, (call + '\r').encode())
        start = time.monotonic()
        while time.monotonic() - start < blocks * PERIOD_US / 1e6 * 1.5 + 5:
            if select.select([fd], [], [], 0.05)[0]:
                chunk = os.read(fd, 65536)
                arrivals.append((time.monotonic() - start, len(data), len(chunk)))
                data.extend(chunk)
                if b'FXD_DONE\r\n' in data[-200:] and data.endswith(b'>>> '):
                    break
            if variant == 'APP' and time.monotonic() - start > blocks * PERIOD_US / 1e6:
                os.write(fd, b'\x03')
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not data.endswith(b'>>> '):
                    if select.select([fd], [], [], 0.05)[0]:
                        data.extend(os.read(fd, 65536))
                break
    finally:
        try:
            os.write(fd, b'\x03\r')
            time.sleep(0.1)
            if sample is not None:
                exchange(fd, 'spl.trigger(-1, %d, False)' % sample)
            exchange(fd, "exec(\"for _n in [n for n in globals() if n.startswith('_fxd') or n.startswith('_fxu')]:\\n    globals().pop(_n)\")")
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
            os.close(fd)
    return bytes(data), arrivals


def parse_fxp3(data):
    records = []
    offset = data.find(b'FXP3')
    while 0 <= offset and offset + 12 <= len(data):
        if data[offset:offset+4] != b'FXP3' or not 1 <= data[offset+10] <= 8:
            offset = data.find(b'FXP3', offset + 1)
            continue
        count = data[offset+10]
        if offset + 12 + 51*count > len(data):
            break
        stamp0, seq0 = struct.unpack_from('<IH', data, offset + 4)
        for b in range(count):
            o = offset + 12 + 51*b
            head = data[o]
            low = data[o+1] | data[o+2] << 8
            stamp = (stamp0 + ((low - stamp0) & 0xffff ^ 0x8000) - 0x8000) & 0xffffffff
            raw = np.frombuffer(data, np.uint8, 48, o + 3).reshape(16, 3).astype(np.int32)
            a = raw[:, 0] | (raw[:, 1] & 15) << 8
            c = raw[:, 1] >> 4 | raw[:, 2] << 4
            pair = np.stack([a, c], 1).reshape(-1)
            pair = np.where(pair & 0x800, pair - 4096, pair) << (head & 31)
            records.append(((seq0 + b) & 0xffff, stamp, 1 if head & 128 else 0, (pair >> 8).astype(np.int16)))
        offset += 12 + 51*count
    return records


def parse_fxp4(data):
    records = []
    offset = data.find(b'FXP4')
    while 0 <= offset and offset + 12 <= len(data):
        if data[offset:offset+4] != b'FXP4' or not 1 <= data[offset+10] <= 8:
            offset = data.find(b'FXP4', offset + 1)
            continue
        count = data[offset+10]
        if offset + 12 + 66*count > len(data):
            break
        stamp0, seq0 = struct.unpack_from('<IH', data, offset + 4)
        for b in range(count):
            o = offset + 12 + 66*b
            low = data[o] | data[o+1] << 8
            stamp = (stamp0 + ((low - stamp0) & 0xffff ^ 0x8000) - 0x8000) & 0xffffffff
            records.append(((seq0 + b) & 0xffff, stamp, 0, np.frombuffer(data, '<i2', 32, o + 2)))
        offset += 12 + 66*count
    return records


def parse(data):
    if b'FXP4' in data[:4000]:
        return parse_fxp4(data)
    if b'FXP3' in data[:4000]:
        return parse_fxp3(data)
    records = []
    magic0 = b'FXP2' if b'FXP2' in data[:4000] else b'FXP1'
    offset = data.find(magic0)
    while 0 <= offset and offset + 80 <= len(data):
        magic, seq, stamp, flags = struct.unpack_from('<4sIII', data, offset)
        if magic != magic0:
            nxt = data.find(magic0, offset + 1)
            if nxt < 0:
                break
            offset = nxt
            continue
        records.append((seq, stamp, flags, np.frombuffer(data, '<i2', 32, offset + 16)))
        offset += 80
    return records


def analyse(records, arrivals, hardware_stamps=False):
    stamps = np.array([r[1] for r in records], dtype=np.int64)
    if hardware_stamps:
        # FXP2: DMA-written completion stamps (full 32-bit) sit on an exact period grid.
        elapsed = (np.diff(stamps) + (1 << 31)) % (1 << 32) - (1 << 31)
        periods = np.round(elapsed / PERIOD_US).astype(int)
        grid_error = np.abs(elapsed - periods * PERIOD_US)
    else:
        elapsed = np.diff(stamps) % (1 << 30)
        # The CPU readers only return when the active half toggles, so the number of DMA
        # periods between two packets is always odd: lost halves come in pairs.
        periods = 2 * np.round((elapsed / PERIOD_US - 1) / 2).astype(int) + 1
        periods = np.maximum(periods, 1)
        grid_error = np.zeros(1)
    lost = np.maximum(periods - 1, 0)
    flags = np.array([r[2] for r in records])
    host_gaps = np.diff([a[0] for a in arrivals]) * 1000 if len(arrivals) > 1 else np.array([0])
    span = elapsed.sum() / 1e6
    return {
        'packets': len(records),
        'device_seconds': round(float(span), 4),
        'lost_halves': int(lost.sum()),
        'loss_events': int((lost > 0).sum()),
        'loss_percent': round(100 * float(lost.sum()) / (len(records) + float(lost.sum())), 3),
        'torn_copies': int((flags & (1 if hardware_stamps else 2) > 0).sum()),
        'wait_timeouts': 0 if hardware_stamps else int((flags & 1 > 0).sum()),
        'stamp_grid_error_us_max': round(float(grid_error.max()), 1),
        'seq_breaks': int(sum(1 for a, b in zip(records, records[1:]) if b[0] != (a[0] + 1) & (0xffff if hardware_stamps else 0xffffffff))),
        'out_of_order': int((periods < 1).sum()),
        'host_read_gap_ms_p99': round(float(np.percentile(host_gaps, 99)), 2),
        'host_read_gap_ms_max': round(float(host_gaps.max()), 2),
        'host_bytes_per_s': round(sum(a[2] for a in arrivals) / max(1e-9, arrivals[-1][0] - arrivals[0][0])) if arrivals else 0,
    }, periods


def tone_report(records, periods):
    """Splice the stream onto a device-time grid (lost halves as gaps) and measure
    discontinuities of the 1 kHz factory sample against a clean sine fit per block."""
    pcm = np.concatenate([r[3] for r in records]).astype(float)
    if pcm.std() < 100:
        return {'tone': 'no tone present'}
    residual_peaks = 0
    blocks = len(records)
    # A torn or misordered half shows up as a large sample-to-sample jump relative to the
    # tone's normal slope. Normal max |diff| for a sine of amplitude A at f: 2*pi*f/fs*A.
    amp = np.percentile(np.abs(pcm), 99.5)
    limit = 2 * np.pi * 1000 / 56250 * amp * 1.6
    d = np.abs(np.diff(pcm))
    inside = d.reshape(-1)[np.arange(len(d)) % 32 != 31]
    boundary = d[31::32][:blocks - 1]
    clean_boundaries = boundary[periods[:len(boundary)] == 1]
    return {
        'tone_amplitude': round(float(amp)),
        'jumps_inside_blocks': int((inside > limit).sum()),
        'jumps_at_block_joins_without_loss': int((clean_boundaries > limit).sum()),
        'block_joins_without_loss': int(len(clean_boundaries)),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default=None)
    parser.add_argument('--variant', choices=VARIANTS, default='B')
    parser.add_argument('--blocks', type=int, default=10000)
    parser.add_argument('-k', type=int, default=1)
    parser.add_argument('--sample', type=int, choices=range(4))
    parser.add_argument('--settle', type=float, default=0.3)
    parser.add_argument('--save', type=Path)
    args = parser.parse_args()
    if not 1 <= args.blocks <= 40000:
        raise SystemExit('bounded: 1..40000 blocks')
    port = args.port or '/dev/' + next(p for p in os.listdir('/dev') if p.startswith('cu.usbmodemEP'))
    data, arrivals = capture(port, args.variant, args.blocks, args.k, args.sample, args.settle)
    records = parse(data)
    report, periods = analyse(records, arrivals, args.variant == 'APP')
    report.update(variant=args.variant, k=args.k, sample=args.sample)
    report.update(tone_report(records, periods))
    print(json.dumps(report, indent=2))
    if args.save:
        args.save.with_suffix('.bin').write_bytes(data)
        pcm = np.concatenate([r[3] for r in records])
        with wave.open(str(args.save.with_suffix('.wav')), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(56250); w.writeframes(pcm.tobytes())
        args.save.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
