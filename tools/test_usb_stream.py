"""Bounded, RAM-only EP-2350 audio-transfer experiment (firmware 1.1.1)."""
import argparse
import json
import os
from pathlib import Path
import select
import struct
import termios
import time
import tty

from inspect_device import exchange


DEVICE_CODE = '''
import micropython, uctypes, struct, sys, time
@micropython.viper
def _fx_wait_half(previous: int, reg: int) -> int:
    r = ptr32(reg)
    count = 0
    while (int(r[0]) & 256) == previous and count < 200000:
        count += 1
    return int(r[0]) & 256
@micropython.viper
def _fx_copy16(source: int, dest):
    src = ptr32(source)
    dst = ptr16(dest)
    for i in range(32):
        dst[8 + i] = src[2 * i] >> 16
def _fx_stream_test(blocks, output):
    reg = 0x50000000 if output else 0x50000044
    base = 0x20008c00 if output else 0x20008a00
    mask = 0 if output else 256
    packet = bytearray(80)
    previous = struct.unpack('<I', uctypes.bytes_at(reg, 4))[0] & 256
    for seq in range(blocks):
        half = _fx_wait_half(previous, reg)
        stamp = time.ticks_us()
        _fx_copy16(base + (half ^ mask), packet)
        flags = 1 if half == previous else 0
        struct.pack_into('<4sIII', packet, 0, b'FXP1', seq, stamp, flags)
        sys.stdout.buffer.write(packet)
        previous = half
    print('FXMIC_STREAM_DONE')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('port')
    parser.add_argument('--blocks', type=int, default=1000)
    parser.add_argument('--output-audio', action='store_true')
    parser.add_argument('--trigger-sample', type=int, choices=range(4))
    parser.add_argument('--save', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.blocks <= 10000:
        raise ValueError('Bounded experiments accept 1 to 10000 blocks')
    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    old = termios.tcgetattr(fd)
    data = bytearray()
    try:
        tty.setraw(fd)
        print(exchange(fd, 'exec(' + repr(DEVICE_CODE) + ')'))
        start = time.monotonic()
        prefix = '' if args.trigger_sample is None else 'spl.trigger(-1, %d, True); ' % args.trigger_sample
        os.write(fd, (prefix + '_fx_stream_test(%d, %s)\r' % (args.blocks, args.output_audio)).encode())
        while time.monotonic() - start < 20:
            if select.select([fd], [], [], 0.05)[0]:
                data.extend(os.read(fd, 65536))
                if b'FXMIC_STREAM_DONE\r\n' in data and data.endswith(b'>>> '):
                    break
        elapsed = time.monotonic() - start
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        os.close(fd)
        args.save.write_bytes(data)
    records = []
    offset = data.find(b'FXP1')
    while offset >= 0 and offset + 80 <= len(data):
        magic, seq, stamp, flags = struct.unpack_from('<4sIII', data, offset)
        if magic != b'FXP1':
            break
        records.append((seq, stamp, flags))
        offset += 80
    gaps = [((b[1] - a[1]) % (1 << 30)) for a,b in zip(records, records[1:])]
    report = {
        'records': len(records), 'expected': args.blocks,
        'bytes': len(data), 'elapsed_seconds': elapsed,
        'sequence_ok': [r[0] for r in records] == list(range(args.blocks)),
        'wait_timeouts': sum(r[2] for r in records),
        'median_gap_us': sorted(gaps)[len(gaps)//2] if gaps else None,
        'max_gap_us': max(gaps) if gaps else None,
        'gaps_over_1000us': sum(g > 1000 for g in gaps),
        'tail': bytes(data[-80:]).decode('utf-8', 'replace'),
    }
    args.save.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
