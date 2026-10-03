import micropython, uctypes, struct, sys, time, rp2
# Temporary USB reader for EP-2350 FX-MIC firmware 1.1.1. Lives in RAM only.
#
# The factory DSP owns DMA0, which plays a 512-byte ring (two 32-frame halves of stereo
# 32-bit words) into the codec. Its IRQ steals ~260 us of every 569 us period and
# sometimes ~930 us, so a CPU copy loop loses blocks. Instead three spare DMA channels
# mirror every half in hardware: DMA0 completes a half -> C loads the next slot address
# into D -> D copies the half DMA0 now plays into that slot -> E stamps it with the
# hardware microsecond timer. The Python loop only drains slots (32 = 18 ms of slack).
#
# Budget: with the jack in, the factory firmware uses ~70% of the CPU, leaving ~150 us per
# 569 us block for copying, USB and this loop. A Python-level call costs 20-40 us here, so
# one native call drains every ready slot (up to 8) into one packet per USB write.
# Packet: 'FXP4', stamp of block 0 (u32), first block seq (u16), block count (u8), 0.
# Block (66 bytes): stamp low 16 bits, then 32 samples (top 16 bits of the left channel).
# A slot rewritten during its copy is dropped; the Mac sees the gap in the stamps.
@micropython.viper
def _fxu_r(a: int) -> int:
    return int(ptr32(a)[0])
@micropython.viper
def _fxu_w(a: int, v: int):
    ptr32(a)[0] = v
@micropython.viper
def _fxu_arm(dma: int, c: int, d: int, e: int, tab: int, stamps: int) -> int:
    # Align on a fresh half switch so the whole setup lands inside one period.
    r = ptr32(dma)
    prev = int(r[0]) & 256
    n = 0
    while (int(r[0]) & 256) == prev and n < 100000:
        n += 1
    cur = int(r[0])
    ptr32(dma + 0x40*d)[0] = ((cur >> 9) << 9) + ((cur & 256) ^ 256)
    ptr32(dma + 0x40*c)[0] = tab
    ptr32(dma + 4 + 0x40*e)[0] = stamps
    ctrl = int(ptr32(dma + 0xc)[0])
    ptr32(dma + 0x10)[0] = (ctrl & (~(15 << 13))) | (c << 13)
    return n
@micropython.viper
def _fxu_drain(pkt, st) -> int:
    # st: nxt, seq, slot table, stamp table, E write-address register, index mask
    s = ptr32(st)
    nxt = s[0]
    mask = s[5]
    tab = ptr32(s[2])
    stamps = ptr32(s[3])
    done = ((int(ptr32(s[4])[0]) - s[3]) >> 2) & mask
    out = ptr8(pkt)
    o16 = ptr16(pkt)
    o = 12
    j = 0
    while nxt != done and j < 8:
        s0 = int(stamps[nxt])
        src = ptr32(tab[nxt])
        q = (o + 2) >> 1
        for i in range(32):
            o16[q+i] = src[2*i] >> 16
        if int(stamps[nxt]) == s0:
            out[o] = s0
            out[o+1] = s0 >> 8
            if j == 0:
                out[4] = s0
                out[5] = s0 >> 8
                out[6] = s0 >> 16
                out[7] = s0 >> 24
            o += 66
            j += 1
        nxt = (nxt + 1) & mask
    s[0] = nxt
    if j:
        seq = s[1]
        out[8] = seq
        out[9] = seq >> 8
        out[10] = j
        s[1] = (seq + j) & 0xffff
    return j
def _fxu_layout():
    info = struct.unpack('<4I', uctypes.bytes_at(0x50000000, 16))
    if info[1] != 0x50200010 or info[3] & 0x1f01 != 0x901:
        raise ValueError('Unsupported audio layout')
    base = info[0] & ~511
    if base < 0x20000000 or base + 512 > 0x20082000:
        raise ValueError('Invalid audio buffer')
    return base
def _fxu_unchain():
    _fxu_w(0x50000010, _fxu_r(0x5000000c) & ~(15 << 13))
def _fxu_open(chans, base):
    C, D, E = chans
    if min(C.channel, D.channel, E.channel) < 2:
        raise ValueError('DMA channel conflict')
    # Slots are separate 256-byte blocks (the mic's heap is fragmented); C's address
    # table makes them look like one ring to the DMA.
    count = 32
    try:
        slots = [bytearray(256) for _ in range(count)]
    except MemoryError:
        count = 16
        slots = [bytearray(256) for _ in range(count)]
    ring = 7 if count == 32 else 6
    traw = bytearray(256)
    sraw = bytearray(256)
    global _fxu_keep
    _fxu_keep = (chans, slots, traw, sraw)
    tab = (uctypes.addressof(traw) + 4*count - 1) & ~(4*count - 1)
    stamps = (uctypes.addressof(sraw) + 4*count - 1) & ~(4*count - 1)
    for i in range(count):
        _fxu_w(tab + 4*i, uctypes.addressof(slots[i]))
    D.config(read=base, write=uctypes.addressof(slots[0]), count=64, ctrl=D.pack_ctrl(size=2, inc_read=1, inc_write=1, ring_sel=0, ring_size=9, treq_sel=0x3f, chain_to=E.channel, irq_quiet=1), trigger=False)
    C.config(read=tab, write=0x5000002c + 0x40*D.channel, count=1, ctrl=C.pack_ctrl(size=2, inc_read=1, inc_write=0, ring_sel=0, ring_size=ring, treq_sel=0x3f, chain_to=C.channel, irq_quiet=1), trigger=False)
    E.config(read=0x400b0028, write=stamps, count=1, ctrl=E.pack_ctrl(size=2, inc_read=0, inc_write=1, ring_sel=1, ring_size=ring, treq_sel=0x3f, chain_to=E.channel, irq_quiet=1), trigger=False)
    return tab, stamps, count - 1
def _fxu_wrap(orig, btn, block):
    # Records button presses from the factory callback, then runs the factory code unless
    # the Mac has given that button another job (bit set in block[0]). Uses no globals, so
    # it stays harmless if our names are removed.
    def cb(m):
        skip = False
        try:
            t = m >> 16
            v = m & 0xffff
            if v < 3 and (t == 1 or t == 2):
                if t == 1:
                    btn[0] |= 1 << v
                else:
                    btn[0] &= ~(1 << v)
                skip = block[0] & (1 << v) != 0
        except Exception:
            pass
        if not skip:
            orig(m)
    return cb
def _fxu_status(sp, btn):
    # 'FXS1', effect (-1 clean, 0-3), sample (0-3), buttons (1 play/bottom, 2 sample select/middle, 4 effect/orange),
    # handle 0-255, flags (1 effect change pending, 2 sample change pending), 0,
    # accelerometer x, y, z, 0.
    g = globals()
    a = ui.acc()
    h = int(ui.handle() * 255)
    f = (1 if g.get('fx_primed', 0) else 0) | (2 if g.get('sam_primed', 0) else 0)
    struct.pack_into('<bbBBBBhhh', sp, 4, g.get('fx_pos', -1), g.get('sam_pos', 0), btn[0], max(0, min(255, h)), f, 0, a[0], a[1], a[2])
def _fxu_cmd(c):
    # The Mac sends Ctrl-C (which interrupts the loop and, in MicroPython, flushes stdin),
    # then the command in a separate write: 'e' + '0'-'4' effect (clean, 1-4),
    # 's' + '0'-'3' sample, 'p' play down, 'q' play up, 'm' + '0'-'7' buttons whose mic
    # action is off (1 play, 2 sample select, 4 effect). Anything else (CR) stops.
    g = globals()
    if c == 109:
        g['_fxu_mask'][0] = sys.stdin.buffer.read(1)[0] - 48
        return True
    if c == 101:
        n = sys.stdin.buffer.read(1)[0] - 49
        g['fx_pos'] = n
        fx.load_preset(n)
    elif c == 115:
        g['sam_pos'] = sys.stdin.buffer.read(1)[0] - 48
    elif c == 112 or c == 113:
        spl.trigger(-1, g.get('sam_pos', 0), c == 112)
        return True
    else:
        return False
    ui.leds(g.get('fx_pos', -1), g.get('sam_pos', 0))
    return True
def _fxu_run():
    _fxu_unchain()
    base = _fxu_layout()
    chans = []
    orig = globals().get('python_callback')
    try:
        for _ in range(3):
            chans.append(rp2.DMA())
        tab, stamps, mask = _fxu_open(chans, base)
        C, D, E = chans
        pkt = bytearray(12 + 66*8)
        pkt[0:4] = b'FXP4'
        views = [memoryview(pkt)[:12 + 66*i] for i in range(9)]
        st = bytearray(24)
        sp = bytearray(16)
        sp[0:4] = b'FXS1'
        btn = [0]
        global _fxu_mask
        _fxu_mask = [0]
        if orig:
            ui.callback(_fxu_wrap(orig, btn, _fxu_mask))
        write = sys.stdout.buffer.write
        drain = _fxu_drain
        status = _fxu_status
        ticks = time.ticks_ms
        diff = time.ticks_diff
        _fxu_arm(0x50000000, C.channel, D.channel, E.channel, tab, stamps)
        struct.pack_into('<6I', st, 0, 0, 0, tab, stamps, 0x50000004 + 0x40*E.channel, mask)
        last = told = ticks()
        sent = 0
        idle = 0
        # Bytecode loop on purpose: every pass runs the VM's pending check, which keeps the
        # mic's USB task and button callbacks serviced.
        while True:
            try:
                while True:
                    j = drain(pkt, st)
                    if j:
                        write(views[j])
                        idle = 0
                        continue
                    idle += 1
                    if idle == 1:
                        last = ticks()
                        # Every 50 ms, and at once when a button changes.
                        if diff(last, told) >= 50 or btn[0] != sent:
                            told = last
                            sent = btn[0]
                            status(sp, btn)
                            write(sp)
                    elif idle & 63 == 0 and diff(ticks(), last) > 100:
                        # No block for 100 ms: the factory firmware reset DMA0. Re-arm.
                        _fxu_unchain()
                        _fxu_layout()
                        _fxu_arm(0x50000000, C.channel, D.channel, E.channel, tab, stamps)
                        struct.pack_into('<I', st, 0, 0)
                        idle = 0
            except KeyboardInterrupt:
                try:
                    if not _fxu_cmd(sys.stdin.buffer.read(1)[0]):
                        break
                except KeyboardInterrupt:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        _fxu_unchain()
        if orig:
            ui.callback(orig)
        for ch in chans:
            _fxu_w(0x50000010 + 0x40*ch.channel, 0)
            ch.close()
        globals().pop('_fxu_keep', None)
    print('FXU_STOPPED')
