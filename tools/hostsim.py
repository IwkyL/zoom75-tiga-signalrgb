"""Run the host-side tools against the emulated patch. No hardware involved.

  hostsim.py hidtest [srgb_hidtest args]   runs tools/srgb_hidtest.py with a fake `hid`
                                           module whose device is the real patch code
                                           from out/patched.hex in Unicorn (tools/emutest.py)
  hostsim.py stock [srgb_hidtest args]     same, but the fake device behaves like the stock
                                           firmware (VIA answers 0xFF to every 0x21-0x28)
  hostsim.py plugin <packets.json>         replays the packets that tools/plugintest.mjs
                                           recorded from the SignalRGB plugin, checks the
                                           emulated replies match what the mock answered and
                                           that a rendered frame reaches set_color unchanged.
                                           USB file: patch v1; 2.4G file (tests/fixtures/
                                           plugin_packets_rx.json): patch v2 behind the fake
                                           receiver
  hostsim.py plugin2g                      the 2.4G plugin itself (node tools/plugin_bridge.mjs)
                                           in real time against patch v2 behind the fake
                                           receiver: pacing at the keyboard, the board = the
                                           scene, leaving 2.4G mode and Fn+V, a radio state
                                           reset, Shutdown; again with 25 % random loss and
                                           with the "busy" link model
  hostsim.py radio                         runs srgb_hidtest.py against a fake 2.4G receiver
                                           (6D66:8888) in fixed scenarios: the keyboard's real
                                           v1 radio path (+DATA: handler 0x080140AC and VIA in
                                           Unicorn), a receiver that drops unknown IDs, wrong
                                           report lengths, no caps, two matches, argument
                                           refusals, --led and --marker-probe on v1; plus
                                           --via-probe on the emulated USB patch
  hostsim.py radio2                        the same fake receiver, keyboard side = patch v2
                                           (out_v2/patched_v2.hex): read-only, --via-probe,
                                           --marker-probe, --led (quiet off and --quiet), with
                                           the radio stream rendered through hook B
  hostsim.py hidtest2 [srgb_hidtest args]  hidtest against the emulated patch v2 on USB
  hostsim.py pace                          the pace step and --packet-gap against the fake
                                           receiver with patch v2 and a lossy link model
                                           (RadioKeyboard2 loss: drop / busy / uart merge into
                                           148-byte chunks parsed by the real +DATA: handler);
                                           the step's table is compared with what reached
                                           hook C
  hostsim.py soak                          the soak step against the fake receiver with patch
                                           v2: quiet off/on, damage injected on the way to the
                                           keyboard or back (each reply must be classified as
                                           the firmware's rules dictate), a lossy link, refusals
  hostsim.py partial                       the partial step (2.4G pacing planned for the
                                           plugin): frame buffer = scene at each phase end,
                                           >= 80 ms spacing, no timeout fall-back; again with
                                           25 % random loss (the refresh must repair it)
  hostsim.py idle                          the idle step (step 7, power): lead-in stream then
                                           the 2 s timeout fall-back with no 0x26, a silence
                                           in which nothing reaches the keyboard, 0x22 probes
                                           only; a "sleeping keyboard" that ignores probes
                                           (lost probes reported, not a FAIL); refusals

The fake device also renders: after every complete frame and every idle read it runs
the patched rgb_matrix_task hook and records what the keyboard would show
("stock effect" or the streamed colours). The run is then checked against the
sequence the test script announces.

usage: python -I tools/hostsim.py hidtest --led
"""
import collections
import importlib.util
import json
import random
import subprocess
import sys
import time
import types
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent


def _load(name):
    """Load tools/<name>.py by path. tools/ is not put on sys.path, because
    tools/dis.py would shadow the standard library's dis module."""
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


emutest = _load("emutest")

V1_HEX = ROOT / "out" / "patched.hex"
V2_HEX = ROOT / "out_v2" / "patched_v2.hex"
FAKE_PATH = b"\\\\?\\hid#vid_1ea7&pid_cedd&mi_01#fake"
RX_FAKE_PATH = b"\\\\?\\hid#vid_6d66&pid_8888&mi_01#fake"
N_LEDS = 86
VIA_01_REPLY = bytes([0x01, 0x00, 0x0B]) + bytes(29)

# HidP caps of a QMK raw HID collection (32-byte input/output, no report ID), as
# srgb_hidtest.hid_caps reports them.
CAPS_32 = dict(usage=0x61, usage_page=0xFF60, input_len=33, output_len=33, feature_len=0,
               fields=[dict(type="input", kind="value", report_id=0, bits=8, count=32, usage=0x62),
                       dict(type="output", kind="value", report_id=0, bits=8, count=32, usage=0x63)])


def summarize(view):
    if view is None:
        return "stock"
    if len(set(view)) == 1:
        return f"solid {view[0][0]:02x}{view[0][1]:02x}{view[0][2]:02x}"
    lit = [i for i, px in enumerate(view) if px != (0, 0, 0)]
    if len(lit) == 1:
        px = view[lit[0]]
        return f"led {lit[0]} {px[0]:02x}{px[1]:02x}{px[2]:02x}"
    return "pattern"


class Keyboard:
    """Unicorn board + what the LEDs would show."""

    def __init__(self, stock=False, hexpath=V1_HEX):
        self.stock = stock
        self.board = None if stock else emutest.Board(hexpath)
        self.t0 = time.perf_counter()
        self.inbox = collections.deque()
        self.sent = []                       # every 32-byte payload the host sent
        self.t_sent = []                     # arrival time (ms) of each, same order
        self.buf = [(0, 0, 0)] * N_LEDS      # firmware frame buffer (set_color only)
        self.view = None                     # None = stock effect on screen
        self.timeline = []                   # (ms, summary) on every change
        self.frame_sent = {}                 # summary of a completed frame -> last send time

    def now_ms(self):
        return (time.perf_counter() - self.t0) * 1000.0

    def _collect(self):
        for name, args in self.board.calls:
            if name == "raw_hid_send":
                self.inbox.append(args[4])
            elif name == "set_color":
                self.buf[args[0]] = tuple(args[1:4])
            elif name == "set_color_all":
                self.buf = [tuple(args[:3])] * N_LEDS

    def packet(self, payload):
        self.sent.append(payload)
        self.t_sent.append(self.now_ms())
        if self.stock:
            if 0x21 <= payload[0] <= 0x28:   # VIA id_unhandled reply
                self.inbox.append(bytes([0xFF]) + payload[1:])
            elif payload == bytes([0x01]) + bytes(31):
                self.inbox.append(VIA_01_REPLY)
            return
        self.board.now = 1000 + int(self.now_ms())
        handled = self.board.hid(payload)
        self._collect()
        if not handled and payload == bytes([0x01]) + bytes(31):
            # Model of what follows hook A on USB for VIA 0x01 (vendor handler, then VIA
            # replies in place via raw_hid_send). Not emulated; verified on hardware by VIA.
            self.inbox.append(VIA_01_REPLY)
        if payload[0] == 0x24 and payload[1] + payload[2] == N_LEDS:
            self.frame_sent[summarize(self.buf)] = self.now_ms()
        # Render after a full frame or a command. Not right after 0x25: until the
        # first frame arrives (next packet) the real keyboard shows the last stock
        # frame frozen, which this model cannot know.
        if payload[0] != 0x25 and (payload[0] != 0x24 or payload[1] + payload[2] >= N_LEDS):
            self.tick()

    def tick(self):
        if self.stock:
            return
        self.board.now = 1000 + int(self.now_ms())
        self.board.render(5)
        self._collect()
        stock = "render" in self.board.names()
        self.view = None if stock else list(self.buf)
        s = summarize(self.view)
        if not self.timeline or self.timeline[-1][1] != s:
            self.timeline.append((self.now_ms(), s))


class FakeDevice:
    def __init__(self, kb, path=FAKE_PATH):
        self.kb = kb
        self.path = path
        self.opened = False

    def open_path(self, path):
        assert path == self.path, path
        self.opened = True

    def write(self, data):
        data = bytes(data)
        assert self.opened and len(data) == 33 and data[0] == 0, data.hex()
        self.kb.packet(data[1:])
        return 33

    def read(self, max_length, timeout_ms=0):
        assert timeout_ms > 0, "read without timeout would block forever"
        if not self.kb.inbox:
            time.sleep(timeout_ms / 1000.0)
            self.kb.tick()
        return list(self.kb.inbox.popleft()) if self.kb.inbox else []

    def close(self):
        self.opened = False


def fake_hid(kb):
    m = types.ModuleType("hid")
    m.version_str = lambda: "fake"
    m.device = lambda: FakeDevice(kb)

    def enumerate_(vid=0, pid=0):
        base = dict(vendor_id=0x1EA7, product_id=0xCEDD, serial_number="", release_number=0x0100,
                    manufacturer_string="Meletrix", product_string="Zoom75 Tiga")
        return [dict(base, path=b"if0", usage_page=0x01, usage=0x06, interface_number=0),
                dict(base, path=FAKE_PATH, usage_page=0xFF60, usage=0x61, interface_number=1),
                dict(base, path=b"if2", usage_page=0x0C, usage=0x01, interface_number=2)]
    m.enumerate = enumerate_
    return m


def load_hidtest(kb, hid_module=None, caps=None):
    sys.modules["hid"] = hid_module or fake_hid(kb)
    mod = _load("srgb_hidtest")
    mod.LOG_DIR = ROOT / "logs" / "hostsim"
    fake_caps = caps if caps is not None else {FAKE_PATH: CAPS_32, RX_FAKE_PATH: CAPS_32}
    mod.hid_caps = lambda path: fake_caps.get(path, {"error": "fake: no caps for this path"})
    return mod


# ---- Fake 2.4G receiver ----------------------------------------------------------

RADIO_SITE, RADIO_RET = 0x0801528A, 0x0801528E   # bl 0x080140AC in line handler 0x08015214
DEV_WRITE = 0x08008050                            # device write; stubbed, records the line
RADIO_BUF = 0x20004100                            # fake UART line buffer (firmware ZI)


class RadioKeyboard:
    """The keyboard behind the receiver, running the REAL v1 bytes of the radio path:
    0x080140AC (+DATA: handler) -> VIA 0x0800F620 (send = 0) -> reply line via
    0x08008050. Only 0x08008050 is stubbed. The receiver's own behaviour is a model:
    it wraps each 32-byte host report as "+DATA:32\\r\\n<payload>" and returns the
    payload of the "HID=" reply line as one input report. `drop` makes it drop
    0x21-0x28 (outcome b)."""

    def __init__(self, drop=False):
        self.drop = drop
        self.board = emutest.Board(ROOT / "out" / "patched.hex")
        self.board.w8(emutest.MODE, 2)                   # 2.4G
        self.board.w32(emutest.FLAGS, 0)                 # on battery: no VBUS
        self.board.uc.hook_add(emutest.UC_HOOK_CODE, self._dev_write, begin=DEV_WRITE,
                               end=DEV_WRITE + 1)
        self.inbox = collections.deque()
        self.sent = []
        self.lines = []                                  # (device, bytes, timeout)
        self.bad_calls = []                              # stubs that must not run here

    def _dev_write(self, uc, addr, size, _):
        r = [uc.reg_read(x) for x in (emutest.UC_ARM_REG_R0, emutest.UC_ARM_REG_R1,
                                      emutest.UC_ARM_REG_R2, emutest.UC_ARM_REG_R3)]
        self.lines.append((r[0], bytes(uc.mem_read(r[1], r[2])), r[3]))
        uc.reg_write(emutest.UC_ARM_REG_R0, 0)
        uc.reg_write(emutest.UC_ARM_REG_PC, uc.reg_read(emutest.UC_ARM_REG_LR) | 1)

    def packet(self, payload):
        self.sent.append(payload)
        if self.drop and 0x21 <= payload[0] <= 0x28:
            return
        line = b"+DATA:%d\r\n" % len(payload) + payload + b"\r\n"
        self.board.uc.mem_write(RADIO_BUF, line + bytes(96 - len(line)))
        n = len(self.lines)
        self.board._run(RADIO_SITE, RADIO_RET, {emutest.UC_ARM_REG_R0: RADIO_BUF,
                                                emutest.UC_ARM_REG_R1: 0,
                                                emutest.UC_ARM_REG_R2: 1})
        self.bad_calls += self.board.names()             # no set_color / raw_hid_send / render
        ret = self.board.uc.reg_read(emutest.UC_ARM_REG_R0)
        if ret != 0 or len(self.lines) != n + 1:
            self.bad_calls.append(f"handler returned {ret:#x}, {len(self.lines) - n} line(s)")
            return
        dev, out, timeout = self.lines[-1]
        k = out[4] - 1 if len(out) > 4 else -1
        if (dev, timeout) != (2, 500) or out[:4] != b"HID=" or k != len(payload) \
                or out[-2:] != b"\r\n" or len(out) != k + 8:
            self.bad_calls.append(f"bad reply line {out.hex(' ')}")
            return
        self.inbox.append(out[6:6 + k])

    def tick(self):
        pass


def fake_receiver_hid(kb, n_raw=1):
    m = types.ModuleType("hid")
    m.version_str = lambda: "fake"
    m.device = lambda: FakeDevice(kb, RX_FAKE_PATH)

    def enumerate_(vid=0, pid=0):
        if (vid, pid) != (0x6D66, 0x8888):
            return []
        base = dict(vendor_id=0x6D66, product_id=0x8888, serial_number="", release_number=0x0100,
                    manufacturer_string="", product_string="USB Composite Device")
        devs = [dict(base, path=b"r0", usage_page=0x01, usage=0x06, interface_number=0),
                dict(base, path=RX_FAKE_PATH, usage_page=0xFF60, usage=0x61, interface_number=1)]
        devs += [dict(base, path=b"r2c%d" % i, usage_page=up, usage=u, interface_number=2)
                 for i, (up, u) in enumerate(((0x0C, 0x01), (0x01, 0x80), (0x01, 0x06), (0x01, 0x02)))]
        for itf in (3, 4):
            devs += [dict(base, path=b"r%dc%d" % (itf, i), usage_page=up, usage=u, interface_number=itf)
                     for i, (up, u) in enumerate(((0x01, 0x06), (0x01, 0x06), (0x0C, 0x01),
                                                  (0x01, 0x80), (0x01, 0x02)))]
        if n_raw == 2:
            devs.append(dict(base, path=b"second", usage_page=0xFF60, usage=0x61, interface_number=1))
        return devs
    m.enumerate = enumerate_
    return m


HOOK_C_SITE = 0x080140E2                          # bl srgb_rx_radio (r0 payload, r1 n & 0xFF)
UART_CHUNK = 148                                  # USART2 RX DMA buffer (PATCH_V2_DESIGN 2.1)
RADIO_BUF_LEN = 0xD0                              # fake buffer: chunk, its 0, slack for n bytes


class RadioKeyboard2(Keyboard):
    """The keyboard behind the receiver running patch v2 (real bytes): each host report
    goes through the real +DATA: handler 0x080140AC -> hook C srgb_rx_radio (-> VIA) ->
    hook D srgb_radio_send -> dev_write 0x08008050 (stubbed, records the line). The
    queued HID= line, if any, becomes one input report (none when quiet suppresses it).
    Renders through hook B like Keyboard, so the LED timeline can be checked.

    loss (a model of the 2.4G burst loss seen on 2026-10-21, for testing the pace step;
    none of them is verified hardware behaviour):
      None          every packet arrives as its own UART chunk
      ("drop", X)   a packet that arrives less than X ms after the previous one (kept or
                    not) is lost
      ("busy", X)   a packet that arrives less than X ms after the last one kept is lost
      ("uart", X)   packets less than X ms apart leave the keyboard's radio module as one
                    burst of back-to-back "+DATA:32\\r\\n<32 B>\\r\\n" lines. The burst is cut
                    into 148-byte chunks (DMA buffer full) and each chunk goes through the
                    real +DATA: handler after 0x08015214's own steps (leading \\r \\n \\0
                    skipped, a 0 written after the chunk; V dec). Which lines get handled
                    is then decided by the real firmware code (strstr).
      ("random", P) each 0x24 is lost with probability P (seeded); queries and 0x25 / 0x26
                    always arrive, so the read-only checks and the prelude pass
      ("func", F)   packet i is lost iff F(i, payload, t_ms) is true (e.g. a keyboard that
                    sleeps and ignores everything for a while)
    damage(i, payload) -> None or ("down" | "up", {byte: xor}): XOR bytes of packet i on
      the way to the keyboard (it handles the damaged packet) or of its reply on the way
      back (for testing the soak step's classifier; not a model of the real damage).
    accepted: indices (into sent) of the packets that reached hook C, the ground truth."""

    def __init__(self, hexpath=V2_HEX, loss=None, damage=None, seed=1):
        super().__init__(hexpath=hexpath)
        self.board.w8(emutest.MODE, 2)                   # 2.4G
        self.board.w32(emutest.FLAGS, 0)                 # on battery: no VBUS
        self.board.w8(emutest.USBST, 0)                  # cable unplugged
        self.board.uc.hook_add(emutest.UC_HOOK_CODE, self._dev_write, begin=DEV_WRITE,
                               end=DEV_WRITE + 1)
        self.board.uc.hook_add(emutest.UC_HOOK_CODE, self._hook_c, begin=HOOK_C_SITE,
                               end=HOOK_C_SITE + 1)
        self.lines = []
        self.bad_calls = []
        self.no_line = []                                # payloads that produced no line
        self.loss = loss
        self.t_prev = self.t_kept = None
        self.burst = []                                  # uart: [(index into sent, payload)]
        self.at_hook_c = []                              # payloads seen at hook C, this run
        self.accepted = []
        self.chunks = 0
        self.damage = damage
        self.damaged = []                                # (index, way, {byte: xor}) applied
        self.rng = random.Random(seed)
        self._up = None                                  # XORs for the next reply line

    _dev_write = RadioKeyboard._dev_write

    def _hook_c(self, uc, addr, size, _):
        r0, r1 = uc.reg_read(emutest.UC_ARM_REG_R0), uc.reg_read(emutest.UC_ARM_REG_R1)
        self.at_hook_c.append(bytes(uc.mem_read(r0, r1 & 0xFF)))

    def packet(self, payload):
        t = self.now_ms()
        self.sent.append(payload)
        self.t_sent.append(t)
        i = len(self.sent) - 1
        if self.loss is not None:
            kind, x = self.loss
            if kind == "uart":
                if self.burst and t - self.t_prev >= x:
                    self._flush()
                self.burst.append((i, payload))
                self.t_prev = t
                return
            if kind == "drop":
                lost = self.t_prev is not None and t - self.t_prev < x
                self.t_prev = t
            elif kind == "random":
                lost = payload[0] == 0x24 and self.rng.random() < x
            elif kind == "func":
                lost = bool(x(i, payload, t))
            else:
                lost = self.t_kept is not None and t - self.t_kept < x
            if lost:
                return
            self.t_kept = t
        self._up = None
        dmg = self.damage(i, payload) if self.damage is not None else None
        if dmg is not None:
            way, xors = dmg
            self.damaged.append((i, way, dict(xors)))
            if way == "down":
                b = bytearray(payload)
                for k, v in xors.items():
                    b[k] ^= v
                payload = bytes(b)
            else:
                self._up = dict(xors)
        self._chunk(b"+DATA:%d\r\n" % len(payload) + payload + b"\r\n", [(i, payload)])

    def tick(self):
        if self.loss is not None and self.loss[0] == "uart" and self.burst \
                and self.now_ms() - self.t_prev >= self.loss[1]:
            self._flush()
        super().tick()

    def _flush(self):
        burst, self.burst = self.burst, []
        stream = b"".join(b"+DATA:%d\r\n" % len(p) + p + b"\r\n" for _, p in burst)
        for at in range(0, len(stream), UART_CHUNK):
            raw = stream[at:at + UART_CHUNK]
            chunk = raw.lstrip(b"\r\n\0")            # 0x08015214: length < 2 or nothing
            if len(raw) >= 2 and chunk:              # left after the skip -> return
                self._chunk(chunk, burst)

    def _chunk(self, chunk, carried):
        """One UART chunk through the real handler. carried: the (index, payload) packets
        the chunk's bytes came from; with one packet the handler must take exactly it."""
        self.chunks += 1
        self.board.now = 1000 + int(self.now_ms())
        assert len(chunk) <= RADIO_BUF_LEN - 48
        self.board.uc.mem_write(RADIO_BUF, chunk + bytes(RADIO_BUF_LEN - len(chunk)))
        n = len(self.lines)
        self.at_hook_c = []
        self.board._run(RADIO_SITE, RADIO_RET, {emutest.UC_ARM_REG_R0: RADIO_BUF,
                                                emutest.UC_ARM_REG_R1: 0,
                                                emutest.UC_ARM_REG_R2: 1})
        names = self.board.names()
        label = carried[0][1][:3].hex(" ") if len(carried) == 1 else f"chunk of {len(carried)} packets"
        if "raw_hid_send" in names or "render" in names or "set_color_all" in names:
            self.bad_calls.append(f"radio packet {label} called {names}")
        for name, args in self.board.calls:
            if name == "set_color":
                self.buf[args[0]] = tuple(args[1:4])
        ret = self.board.uc.reg_read(emutest.UC_ARM_REG_R0)
        skip = self.board.r8(0x20007E1A) if self.board.r32(0x20007E10) == 0x53524752 else 0
        handled = self.at_hook_c
        if len(carried) == 1 and handled != [carried[0][1]]:
            self.bad_calls.append(f"hook C saw {[h.hex(' ') for h in handled]} for {label}")
        # ret 0 = a +DATA: line was handled; otherwise the line handler would go on to its
        # other prefixes (+BATSTATE:, +EVENT:, ...), which no chunk here contains.
        if (ret != 0) != (not handled) or len(handled) > 1 or len(self.lines) > n + 1 or skip:
            self.bad_calls.append(f"handler returned {ret:#x}, {len(self.lines) - n} line(s), "
                                  f"hook C {len(handled)}x, skip {skip}")
        payload = handled[0] if handled else None
        if payload is not None:
            # The first packet still unaccounted for with these bytes. A payload that
            # matches none was cut at the chunk end (garbage); it stays out of accepted.
            used = set(self.accepted)
            idx = next((i for i, p in carried if p == payload and i not in used), None)
            if idx is not None:
                self.accepted.append(idx)
        if len(self.lines) == n + 1:
            dev, out, timeout = self.lines[-1]
            k = out[4] - 1 if len(out) > 4 else -1
            if (dev, timeout) != (2, 500) or out[:4] != b"HID=" or payload is None \
                    or k != len(payload) or out[-2:] != b"\r\n" or len(out) != k + 8 or out[5] != 0x94:
                self.bad_calls.append(f"bad reply line {out.hex(' ')}")
            else:
                reply = bytearray(out[6:6 + k])
                for kk, v in (self._up or {}).items():
                    reply[kk] ^= v
                self._up = None
                self.inbox.append(bytes(reply))
        elif payload is not None:
            self.no_line.append(payload)
        if payload is None:
            return
        if payload[0] == 0x24 and payload[1] + payload[2] == N_LEDS:
            self.frame_sent[summarize(self.buf)] = self.now_ms()
        if payload[0] != 0x25 and (payload[0] != 0x24 or payload[1] + payload[2] >= N_LEDS):
            Keyboard.tick(self)


fails = 0


def check(cond, msg):
    global fails
    print(("  ok    " if cond else "  FAIL  ") + msg)
    fails += 0 if cond else 1


def guard_checks(mod):
    """The script's Link must refuse everything outside its allowed set."""
    class Dead:
        def write(self, data):
            raise AssertionError("guard let a packet through")

    class Quiet:
        def packet(self, *a):
            pass

        def now_ms(self):
            return 0.0

    ro = mod.Link(Dead(), Quiet(), mod.READ_ONLY_CMDS)
    led = mod.Link(Dead(), Quiet(), mod.READ_ONLY_CMDS | mod.LED_CMDS)
    refused = []
    for link, cmd in [(ro, 0x13), (ro, 0x24), (ro, 0x25), (ro, 0x26), (led, 0x13), (led, 0x00),
                      (led, 0x01), (led, 0x07), (led, 0x11), (led, 0x1C), (led, 0x20), (led, 0x29),
                      (led, 0xFF)]:
        try:
            link.send(mod.cmd_packet(cmd))
        except mod.SafetyError:
            refused.append(cmd)
    check(len(refused) == 13, "Link refuses 0x13, VIA/vendor IDs, 0x20, 0x29, and LED cmds in read-only mode")
    try:
        led.send(bytes([0x24]) + bytes(40))
        check(False, "Link refuses a payload that is not 32 bytes")
    except mod.SafetyError:
        check(True, "Link refuses a payload that is not 32 bytes")
    try:
        mod.Link(Dead(), Quiet(), frozenset({0x13}))
        check(False, "Link refuses an allowed set outside 0x21-0x28")
    except mod.SafetyError:
        check(True, "Link refuses an allowed set outside 0x21-0x28")
    for bad in ({0x02}, {0x07}, {0x01, 0x13}):
        try:
            mod.Link(Dead(), Quiet(), frozenset(bad))
            check(False, f"Link refuses allowed set {sorted(bad)}")
        except mod.SafetyError:
            pass
    probe = mod.Link(Dead(), Quiet(), mod.VIA_PROBE_CMDS)
    refused = []
    for cmd in (0x00, 0x02, 0x03, 0x05, 0x07, 0x08, 0x09, 0x11, 0x13, 0x1C, 0x23, 0x24, 0x25,
                0x26, 0xFF):
        try:
            probe.send(mod.cmd_packet(cmd))
        except mod.SafetyError:
            refused.append(cmd)
    check(len(refused) == 15, "probe Link allows only 0x01 0x21 0x22 0x27 0x28")
    for what, pkt in (("0x01 with a non-zero body", mod.cmd_packet(0x01, 0, 0x0B)),
                      ("0x01 with a non-zero last byte", bytes([0x01]) + bytes(30) + b"\x01")):
        try:
            probe.send(pkt)
            check(False, f"probe Link refuses {what}")
        except mod.SafetyError:
            check(True, f"probe Link refuses {what}")
    # A 0x21 query whose bytes 6-7 hold a valid vendor CRC must be refused.
    p = bytearray(mod.cmd_packet(0x21))
    c = mod.vendor_crc(bytes(p))
    p[6], p[7] = c & 0xFF, c >> 8
    try:
        probe.send(bytes(p))
        check(False, "Link refuses a packet that is a valid vendor CRC frame")
    except mod.SafetyError:
        check(True, "Link refuses a packet that is a valid vendor CRC frame")
    # vendor_crc must equal the receiver updater's own GetCrc_16 (CRC16.cs, run literally).
    import random
    rng = random.Random(1)
    frames = [bytes.fromhex("1302000000000000") + bytes(24)]       # its EnterDFU frame
    frames += [bytes(rng.randrange(256) for _ in range(32)) for _ in range(200)]
    check(all(mod.vendor_crc(f) == _updater_crc(f) for f in frames),
          "vendor_crc equals the receiver updater's GetCrc_16 (EnterDFU + 200 random frames)")
    check(all(not mod.is_vendor_frame(mod.cmd_packet(c)) for c in mod.VIA_PROBE_CMDS | mod.READ_ONLY_CMDS),
          "no probe/query packet is a valid vendor CRC frame")


def _updater_crc(frame):
    """CRC16.GetCrc_16 from the decompiled receiver updater, transcribed literally:
    table parsed from CRC16.cs, num not masked, bytes 6-7 zeroed by the caller
    (AnalyticalData: frames[0:12] + "0000" + frames[16:]), result = low | high << 8."""
    global _UPDATER_TABLE
    if "_UPDATER_TABLE" not in globals():
        import re
        src = (ROOT / "analysis" / "wuque_dfu_v11_decomp" / "UBEST_DFU_TOOL" / "CRC16.cs").read_text()
        body = src[src.index("new int[256]"):]
        body = body[body.index("{") + 1:body.index("}")]
        _UPDATER_TABLE = [int(v) for v in re.findall(r"\d+", body)]
        assert len(_UPDATER_TABLE) == 256
    data = frame[:6] + b"\0\0" + frame[8:]
    num = 65535
    for b in data[:32]:
        num = (num << 8) ^ _UPDATER_TABLE[((num >> 8) ^ b) & 0xFF]
    return (num & 0xFF) | ((num >> 8) & 0xFF) << 8


def run_hidtest(args, stock, hexpath=V1_HEX):
    kb = Keyboard(stock=stock, hexpath=hexpath)
    mod = load_hidtest(kb)
    sys.argv = ["srgb_hidtest.py"] + args
    rc = mod.main()
    print("\n== hostsim checks ==")
    guard_checks(mod)
    cmds = [p[0] for p in kb.sent]
    check(all(0x21 <= c <= 0x28 for c in cmds), "every packet sent starts with 0x21-0x28")
    check(0x13 not in cmds, "0x13 never sent")
    check(all(len(p) == 32 for p in kb.sent), "every payload is 32 bytes")
    led_mode = "--led" in args
    if not led_mode or stock:
        check(set(cmds) <= {0x21, 0x22, 0x23, 0x27, 0x28}, "only read-only queries sent")
    if stock:
        check(rc != 0, "stock firmware: script reports FAIL")
        return
    check(emutest.failed == 0, f"emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")
    check(rc == 0, "script reports PASS")
    if not led_mode:
        check(kb.timeline in ([], [(kb.timeline[0][0], "stock")]) if kb.timeline else True,
              "LEDs never left the stock effect")
        return
    check_leds(kb, args)


def check_leds(kb, args):
    """The LED timeline must match the sequence the test script announces."""
    seq = [s for _, s in kb.timeline]
    print("  timeline: " + " -> ".join(_compress(seq)))
    steps = _steps(args)
    expect = ["stock"]
    if "colors" in steps:
        expect += ["solid ff0000", "solid 00ff00", "solid 0000ff"]
    if "bench" in steps:
        expect += ["pattern"]
    if "walk" in steps:
        expect += [f"led {i} ffffff" for i in _walk(args)]
    if "timeout" in steps:
        expect += ["solid ff00ff", "stock", "solid 00ffff"]
    if "hold" in steps:
        expect += ["solid ff5000", "solid 8c00ff"]
    expect += ["stock"]
    got = _dedupe_bench(seq)
    if "hold" in steps:
        got = _dedupe_hold(got)
    check(got == expect, "LED sequence matches the announced steps")
    if got != expect:
        for i, (a, b) in enumerate(zip(got, expect)):
            if a != b:
                print(f"    first difference at {i}: got {a!r}, want {b!r}")
                break
        print(f"    got {len(got)} states, want {len(expect)}")
    if "timeout" in steps:
        mag = [i for i, (_, s) in enumerate(kb.timeline) if s == "solid ff00ff"]
        check(bool(mag) and mag[-1] + 1 < len(kb.timeline), "magenta shown, then something else")
        if mag and mag[-1] + 1 < len(kb.timeline):
            t_stock = kb.timeline[mag[-1] + 1][0]
            dly = t_stock - kb.frame_sent["solid ff00ff"]
            check(1990 <= dly <= 2300, f"fallback {dly:.0f} ms after the last magenta frame (want ~2000)")


def _compress(seq):
    """Collapse runs of single-LED states: 'led 0 ffffff', ... -> 'led 0..85 (86)'."""
    out, run = [], []
    for s in seq + [None]:
        if s is not None and s.startswith("led "):
            run.append(int(s.split()[1]))
            continue
        if run:
            out.append(f"led {run[0]}..{run[-1]} ({len(run)})")
            run = []
        if s is not None:
            out.append(s)
    return out


def _dedupe_bench(seq):
    out = []
    for s in seq:
        if s == "pattern" and out and out[-1] == "pattern":
            continue
        if s.startswith("solid ") and out and out[-1] == "pattern" and s not in (
                "solid ff0000", "solid 00ff00", "solid 0000ff", "solid ff00ff", "solid 00ffff",
                "solid ff5000", "solid 8c00ff", "solid 000000"):
            continue                          # a rainbow frame can never be solid; defensive
        out.append(s)
    return out


def _dedupe_hold(seq):
    out = []
    for s in seq:
        if s in ("solid ff5000", "solid 8c00ff") and s in out[-2:]:
            continue
        out.append(s)
    return out


def _steps(args):
    if "--steps" in args:
        return args[args.index("--steps") + 1].split(",")
    return ["errors", "predrop", "colors", "bench", "walk", "timeout"]


def _walk(args):
    spec = args[args.index("--walk-leds") + 1] if "--walk-leds" in args else "0-85"
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (int(v) for v in part.split("-"))
            out += range(a, b + 1)
        else:
            out.append(int(part))
    return out


RX_ARGS = ["--vid", "0x6D66", "--pid", "0x8888"]
PROBE_SEQ = lambda n: [0x01] * n + [c for q in (0x21, 0x22, 0x27, 0x28) for c in (0x01, q, 0x01)]  # noqa: E731


def _run_script(name, kb, args, hid_module, caps=None, setup=None):
    print(f"\n######## {name}: srgb_hidtest.py {' '.join(args)}")
    mod = load_hidtest(kb, hid_module, caps)
    if setup is not None:
        setup(mod)
    captured = {}
    for step, attr in (("step_soak", "soak_result"), ("step_partial", "partial_result"),
                       ("step_idle", "idle_result")):
        def wrapped(link, *a, _orig=getattr(mod, step), _attr=attr, **k):
            r = _orig(link, *a, **k)
            captured[_attr.split("_")[0]] = getattr(link, _attr, None)
            return r
        setattr(mod, step, wrapped)
    orig = mod.via_probe

    def via_probe(*a, **k):
        r = orig(*a, **k)
        captured["outcomes"] = r[1]
        return r
    mod.via_probe = via_probe
    orig_marker = mod.marker_probe

    def marker_probe(link, log):
        r = orig_marker(link, log)
        captured["verdict"] = getattr(link, "verdict", None)
        return r
    mod.marker_probe = marker_probe
    orig_pace = mod.step_pace

    def step_pace(link, *a, **k):
        r = orig_pace(link, *a, **k)
        captured["pace"] = getattr(link, "pace_result", None)
        return r
    mod.step_pace = step_pace
    _run_script.captured = captured
    sys.argv = ["srgb_hidtest.py"] + args
    try:
        rc = mod.main()
    except SystemExit as e:             # argparse refusal
        rc = ("exit", e.code)
    return mod, rc, captured.get("outcomes")


def run_radio():
    # 1. --list on the receiver: matches interface 1 by usage page/usage, sends nothing.
    kb = RadioKeyboard()
    _, rc, _ = _run_script("list", kb, RX_ARGS + ["--list"], fake_receiver_hid(kb))
    check(rc == 0 and not kb.sent, "receiver --list: exit 0, nothing sent")

    # 2. --via-probe through the receiver, keyboard = real v1 radio path -> outcome (a).
    kb = RadioKeyboard()
    mod, rc, outcomes = _run_script("v1 radio path, --via-probe", kb,
                                    RX_ARGS + ["--via-probe", "--via-count", "5"], fake_receiver_hid(kb))
    print("\n== hostsim checks (receiver) ==")
    guard_checks(mod)
    cmds = [p[0] for p in kb.sent]
    check(rc == 0, "v1 probe: 0x01 baseline passes")
    check(cmds == PROBE_SEQ(5), "v1 probe: sent exactly 5x 0x01, then 0x01/q/0x01 for 0x21 0x22 0x27 0x28")
    check(all(p == bytes([p[0]]) + bytes(31) for p in kb.sent), "v1 probe: every request body is zero")
    check(outcomes == {0x21: "unhandled", 0x22: "unhandled", 0x27: "unhandled", 0x28: "unhandled"},
          "v1 probe: every SignalRGB ID -> VIA 'unhandled' (0xFF + echo), verdict (a)")
    check(not kb.bad_calls, "real 0x080140AC: no raw_hid_send / set_color / render; one HID= line "
                            "(device 2, 500 ms, len+8) per packet")
    check({out[5] for _, out, _ in kb.lines} == {0x94},
          "reply flag byte 0x94 (mode 2, no VBUS), as read from 0x0801410A-0x08014124")

    # 3. Default read-only run through the receiver on v1: FAIL, radio hint, 5 queries only.
    kb = RadioKeyboard()
    mod, rc, _ = _run_script("v1 radio path, read-only", kb, RX_ARGS, fake_receiver_hid(kb))
    check(rc == 1 and [p[0] for p in kb.sent] == [0x21, 0x22, 0x23, 0x27, 0x28],
          "v1 read-only via receiver: FAIL after exactly the 5 read-only queries")
    check(not kb.bad_calls, "v1 read-only via receiver: radio path clean")

    # 4. A receiver that drops unknown IDs -> outcome (b).
    kb = RadioKeyboard(drop=True)
    mod, rc, outcomes = _run_script("receiver drops 0x21-0x28, --via-probe", kb,
                                    RX_ARGS + ["--via-probe", "--via-count", "3"], fake_receiver_hid(kb))
    check(rc == 0 and outcomes == {0x21: "none", 0x22: "none", 0x27: "none", 0x28: "none"},
          "drop: every SignalRGB ID -> no reply while 0x01 around it works, verdict (b)")

    # 5-7. Refusals before anything is sent.
    for name, caps, n_raw in (
            ("65-byte reports", {RX_FAKE_PATH: dict(CAPS_32, input_len=65, output_len=65)}, 1),
            ("report ID 5", {RX_FAKE_PATH: dict(CAPS_32, fields=[dict(f, report_id=5) for f in CAPS_32["fields"]])}, 1),
            ("caps unavailable", {}, 1),
            ("two raw HID collections", None, 2)):
        kb = RadioKeyboard()
        _, rc, _ = _run_script(f"refuse: {name}", kb, RX_ARGS + ["--via-probe"],
                               fake_receiver_hid(kb, n_raw), caps)
        check(rc == 1 and not kb.sent, f"receiver with {name}: refused, nothing sent")

    # 8. --led on the receiver with v1: read-only fails first, so no LED command goes out.
    kb = RadioKeyboard()
    _, rc, _ = _run_script("v1 radio path, --led", kb, RX_ARGS + ["--led"], fake_receiver_hid(kb))
    check(rc == 1 and [p[0] for p in kb.sent] == [0x21, 0x22, 0x23, 0x27, 0x28],
          "v1 --led via receiver: read-only FAIL, LED tests skipped, only the 5 queries sent")

    # 9. --marker-probe on v1: the 0x21 echo carries byte 31 -> n = 32 in this model.
    kb = RadioKeyboard()
    _, rc, _ = _run_script("v1 radio path, --marker-probe", kb, RX_ARGS + ["--marker-probe"],
                           fake_receiver_hid(kb))
    verdict = _run_script.captured.get("verdict") or ""
    check(rc == 0 and [p[0] for p in kb.sent] == [0x01, 0x21, 0x01] and kb.sent[1][31] == 0x5A
          and not any(kb.sent[1][1:31]) and not any(kb.sent[0][1:]) and not any(kb.sent[2][1:]),
          "v1 marker probe: exactly 0x01, 0x21 (byte 31 = 0x5A, rest zero), 0x01")
    check(verdict.startswith("n = 32"), f"v1 marker probe verdict: {verdict[:60]}")
    check(not kb.bad_calls, "v1 marker probe: radio path clean")

    # 10-15. Argument refusals.
    for name, args in (("unknown VID/PID", ["--vid", "0x6D66", "--pid", "0x5850", "--via-probe"]),
                       ("non-vendor usage page", RX_ARGS + ["--usage-page", "0x0001", "--usage", "0x06"]),
                       ("--via-probe with --led", ["--via-probe", "--led"]),
                       ("--marker-probe with --via-probe", RX_ARGS + ["--marker-probe", "--via-probe"]),
                       ("--marker-probe with --led", RX_ARGS + ["--marker-probe", "--led"]),
                       ("--quiet without --led", RX_ARGS + ["--quiet"])):
        kb = RadioKeyboard()
        _, rc, _ = _run_script(f"refuse: {name}", kb, args, fake_receiver_hid(kb))
        check(rc == ("exit", 2) and not kb.sent, f"{name}: argument error, nothing sent")

    # 12. --via-probe over USB against the emulated patch: SignalRGB answers.
    kb = Keyboard()
    _, rc, outcomes = _run_script("USB, emulated patch, --via-probe", kb, ["--via-probe", "--via-count", "3"],
                                  fake_hid(kb))
    check(rc == 0 and outcomes == {0x21: "srgb", 0x22: "srgb", 0x27: "srgb", 0x28: "srgb"},
          "USB probe: the patch answers all four SignalRGB IDs")
    check([p[0] for p in kb.sent] == PROBE_SEQ(3), "USB probe: same packet sequence")
    check(all(s == "stock" for _, s in kb.timeline), "USB probe: LEDs never left the stock effect")
    check(emutest.failed == 0, f"emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")


def _radio2_common(kb, mod, label):
    guard_checks(mod)
    cmds = [p[0] for p in kb.sent]
    check(all(0x21 <= c <= 0x28 or c == 0x01 for c in cmds) and 0x13 not in cmds,
          f"{label}: only 0x01 / 0x21-0x28 sent, never 0x13")
    check(all(len(p) == 32 for p in kb.sent), f"{label}: every payload is 32 bytes")
    check(not kb.bad_calls, f"{label}: real radio path clean (no raw_hid_send / set_color_all / "
                            f"render on it; at most one well-formed HID= line; skip_reply 0)"
          + ("" if not kb.bad_calls else f" {kb.bad_calls[:2]}"))
    check(emutest.failed == 0, f"{label}: emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")


def run_radio2():
    # 1. read-only through the receiver: PASS with exactly one reply per query.
    kb = RadioKeyboard2()
    mod, rc, _ = _run_script("v2, read-only", kb, RX_ARGS, fake_receiver_hid(kb))
    print("\n== hostsim checks (receiver, patch v2) ==")
    check(rc == 0 and [p[0] for p in kb.sent] == [0x21, 0x22, 0x23, 0x27, 0x28],
          "v2 read-only via receiver: PASS after exactly the 5 read-only queries")
    check(not kb.no_line, "v2 read-only: every query got its reply line")
    check(all(s == "stock" for _, s in kb.timeline), "v2 read-only: LEDs never left the stock effect")
    _radio2_common(kb, mod, "v2 read-only")

    # 2. --via-probe: SignalRGB answers all four IDs; 0x01 still reaches VIA.
    kb = RadioKeyboard2()
    mod, rc, outcomes = _run_script("v2, --via-probe", kb, RX_ARGS + ["--via-probe", "--via-count", "5"],
                                    fake_receiver_hid(kb))
    check(rc == 0 and outcomes == {0x21: "srgb", 0x22: "srgb", 0x27: "srgb", 0x28: "srgb"},
          "v2 probe: 0x01 baseline passes (real VIA), the patch answers all four SignalRGB IDs")
    check([p[0] for p in kb.sent] == PROBE_SEQ(5), "v2 probe: same packet sequence as on v1")
    _radio2_common(kb, mod, "v2 probe")

    # 3. --marker-probe on v2: handled, zero-filled -> the verdict says so.
    kb = RadioKeyboard2()
    _, rc, _ = _run_script("v2, --marker-probe", kb, RX_ARGS + ["--marker-probe"], fake_receiver_hid(kb))
    verdict = _run_script.captured.get("verdict") or ""
    check(rc == 0 and "patch v2 handled it" in verdict, f"v2 marker probe verdict: {verdict[:70]}")
    check(not kb.bad_calls, "v2 marker probe: radio path clean")

    # 4-6. LED tests through the receiver, quiet off and on.
    walk = ["--walk-leds", "0-3,29,82-85", "--walk-delay", "0.2"]
    for name, extra in (("--led --steps colors", ["--steps", "colors"]),
                        ("--led (all default steps)", walk),
                        ("--led --quiet (all default steps)", ["--quiet"] + walk)):
        kb = RadioKeyboard2()
        args = RX_ARGS + ["--led"] + extra
        mod, rc, _ = _run_script(f"v2, {name}", kb, args, fake_receiver_hid(kb))
        print(f"\n== hostsim checks (receiver, patch v2, {name}) ==")
        check(rc == 0, f"{name}: script reports PASS")
        quiet = "--quiet" in extra
        mode_on = [p for p in kb.sent if p[0] == 0x25]
        check(mode_on and all(p[1] == (1 if quiet else 0) for p in mode_on),
              f"{name}: every 0x25 is 25 {'01' if quiet else '00'}")
        if quiet:
            silent = [p for p in kb.no_line if not (p[0] == 0x24 and _stream_valid(p) or p[0] in (0x25, 0x26))]
            check(not silent, "quiet on: only successful 0x24 / 0x25 / 0x26 went without a reply line")
            check(any(p[0] == 0x24 for p in kb.no_line), "quiet on: stream packets got no reply line")
        else:
            check(not kb.no_line, "quiet off: every packet got its reply line (echo or answer)")
        _radio2_common(kb, mod, name)
        check_leds(kb, args)


def _stream_valid(p):
    return p[1] + p[2] <= N_LEDS and p[2] <= 9 and 3 + 3 * p[2] <= 32


# ---- pace step against the lossy fake receiver -----------------------------------

PACE_FAST = ["--led", "--steps", "pace", "--pace-frames", "3", "--pace-settle", "150"]


def _pace_truth(kb, frames, quiet):
    """Ground truth per pace row: frames in which packet k reached hook C (quiet off), or
    in which the 0x27 that replaced slot k did (quiet on). Rows come from the tag in
    bytes 30-31 (the script numbers frames 1, 2, ... in row order) or, for the 0x27
    markers, from their order."""
    acc = set(kb.accepted)
    first_25 = next(i for i, p in enumerate(kb.sent) if p[0] == 0x25)     # after the read-only 0x27
    truth, markers = collections.defaultdict(lambda: [0] * 10), 0
    for i, p in enumerate(kb.sent):
        if p[0] == 0x24 and p[30] & 0xC0 == 0xC0 and p[31] & 0x80:
            tag = (p[30] & 0x1F) << 7 | (p[31] & 0x7F)
            row, k = (tag - 1) // frames, p[1] // 9
            if not quiet and i in acc:
                truth[row][k] += 1
        elif p[0] == 0x27 and quiet and i > first_25:
            row, f = divmod(markers, frames)
            markers += 1
            truth[row][f % 10] += i in acc
    return [truth[r] for r in sorted(truth)]


def _pace_run(name, loss, extra, quiet=False, frames=3):
    kb = RadioKeyboard2(loss=loss)
    args = RX_ARGS + PACE_FAST[:4] + [str(frames)] + PACE_FAST[5:] + (["--quiet"] if quiet else []) + extra
    mod, rc, _ = _run_script(f"pace, {name}", kb, args, fake_receiver_hid(kb))
    res = _run_script.captured.get("pace")
    print(f"\n== hostsim checks (pace, {name}) ==")
    _radio2_common(kb, mod, name)
    check(res is not None, f"{name}: the pace step ran and left its result")
    if res is None:
        return kb, rc, None
    truth = _pace_truth(kb, frames, quiet)
    table = [st["arrived"] for st in res["rows"]]
    check(table == truth, f"{name}: the table matches what reached hook C (ground truth)"
          + ("" if table == truth else f": table {table}, truth {truth}"))
    sent = [p for p in kb.sent if p[0] == 0x24 and p[30] & 0xC0 == 0xC0]
    check(all(not mod.is_vendor_frame(p) and _stream_valid(p) for p in sent),
          f"{name}: every pace packet is a valid 0x24 and never a vendor CRC frame")
    return kb, rc, res


def _min_spacing(kb, idx):
    """Smallest arrival spacing at the fake between consecutive sends in idx."""
    return min((kb.t_sent[b] - kb.t_sent[a] for a, b in zip(idx, idx[1:]) if b == a + 1),
               default=float("inf"))


def _spacing_ok(kb, gap_ms, idx):
    """Arrival spacing >= gap, with 1 ms slack: the fake stamps a packet a little after the
    host's own timestamp, and a busy PC can delay one stamp."""
    return _min_spacing(kb, idx) >= gap_ms - 1.0


def run_pace(mod_checks=True):
    # 0. pace_packets itself.
    kb = RadioKeyboard2()
    mod = load_hidtest(kb, fake_receiver_hid(kb))
    pk = [mod.pace_packets(rgb, t, nf) for t, (rgb, nf) in enumerate(
        [((255, 0, 0), False), ((255, 255, 255), True)] * 2000 + [((0, 0, 255), False)] * 95, start=1)]
    flat = [p for frame in pk for p in frame]
    check(len(set(flat)) == len(flat), f"pace_packets: {len(flat)} packets, all distinct (tags 1..4095)")
    check(all(not mod.is_vendor_frame(p) and _stream_valid(p) for p in flat),
          "pace_packets: all valid 0x24, none a vendor CRC frame")
    plain = list(mod.stream_packets(mod.solid((255, 0, 0))))
    check(all(a[:30] == b[:30] for a, b in zip(pk[0], plain)),
          "pace_packets: bytes 0-29 = the plain stream packets (only padding bytes 30-31 differ)")
    white = list(mod.stream_packets(mod.solid((255, 255, 255))))
    check(all(a[:3 + 3 * a[2]] == b[:3 + 3 * b[2]] for a, b in zip(pk[1], white)),
          "pace_packets (no-00 row): the colour bytes the firmware reads are unchanged")
    check(all(b"\0" not in (p[2:] if p[1] == 0 else p) for p in pk[1]),
          "pace_packets (no-00 row): no 00 byte except packet 1's start byte")
    flips = sum(1 for p in flat if p[30] & 0x20)
    print(f"  ({flips} of {len(flat)} packets needed the CRC-avoiding bit flip)")

    # 1. Ideal receiver: everything arrives.
    kb, rc, res = _pace_run("ideal receiver", None, ["--pace-gaps", "1,5"])
    check(rc == 0, "ideal: script reports PASS")
    if res:
        check(all(st["loss_free"] for st in res["rows"]) and res["best_gap"] == 1.0,
              "ideal: every row loss-free, smallest loss-free gap 1 ms")
        check(res["diag"] == "none", f"ideal: diagnostic says it cannot tell (got {res['diag']})")
        check(abs(res["fps"] - 1000.0 / (10 * max(1.0, res["rows"][0]["spacing"]))) < 1e-6,
              f"ideal: fps from the measured spacing ({res['fps']:.1f})")
    seq = [s for _, s in kb.timeline]
    check(["solid ff0000", "solid 00ff00", "solid ffffff"] == [s for s in seq if s.startswith("solid")],
          "ideal: the board showed RED, GREEN, then the diagnostic WHITE: " + " -> ".join(_compress(seq)))
    i25 = [i for i, p in enumerate(kb.sent) if p[0] in (0x25, 0x26)]
    after = min(kb.t_sent[i + 1] - kb.t_sent[i] for i in i25 if i + 1 < len(kb.sent))
    check(after >= 99.0, f"every packet after a 0x25 / 0x26 arrived >= 100 ms later (radio prelude "
                         f"gap; min {after:.1f} ms)")
    for gap, rgb in ((1, (255, 0, 0)), (5, (0, 255, 0))):
        idx = [i for i, p in enumerate(kb.sent) if p[0] == 0x24 and p[3:6] == bytes(rgb)]
        check(_spacing_ok(kb, gap, idx), f"ideal: the {gap} ms row's packets arrived >= {gap} ms apart "
                                         f"(min {_min_spacing(kb, idx):.2f})")

    # 2. "drop": anything < 10 ms after the previous packet is lost (the brief's model).
    kb, rc, res = _pace_run("drop < 10 ms", ("drop", 10.0), ["--pace-gaps", "1,3,20,30"])
    check(rc == 0, "drop: script reports PASS (lost packets are the measurement, not a failure)")
    if res:
        check([st["arrived"] for st in res["rows"][:2]] == [[3] + [0] * 9] * 2,
              "drop: at 1 and 3 ms only packet 1 of each frame arrives (the hardware pattern)")
        check([st["loss_free"] for st in res["rows"]] == [False, False, True, True, False],
              "drop: 20 and 30 ms loss-free, the diagnostic row is not")
        check(res["best_gap"] == 20.0 and abs(res["fps"] - 1000.0 / (10 * res["rows"][2]["spacing"])) < 1e-6,
              f"drop: smallest loss-free gap 20 ms, {res['fps']:.2f} frames/s")
        check(res["diag"] == "upstream", f"drop: diagnostic says upstream (got {res['diag']})")
    seq = [s for _, s in kb.timeline]
    solids = [s for s in seq if s.startswith("solid")]
    check(solids == ["solid 0000ff", "solid ffff00"],
          "drop: only the loss-free rows (BLUE, YELLOW) ever showed a whole board: " + " -> ".join(_compress(seq)))

    # 3. "busy": < 10 ms after the last packet kept -> a regular pattern, not just packet 1.
    kb, rc, res = _pace_run("busy < 10 ms", ("busy", 10.0), ["--pace-gaps", "3", "--pace-diag-gap", "0"])
    check(rc == 0, "busy: script reports PASS")
    if res:
        a = res["rows"][0]["arrived"]
        check(a[0] == 3 and 0 < sum(a[1:]) < 27, f"busy: packet 1 plus some later packets arrive: {a}")

    # 4. "uart": bursts merge into 148-byte chunks, parsed by the real +DATA: handler.
    kb, rc, res = _pace_run("uart merge < 15 ms", ("uart", 15.0), ["--pace-gaps", "1,30"])
    check(rc == 0, "uart: script reports PASS")
    if res:
        check(res["rows"][0]["arrived"] == [3] + [0] * 9,
              "uart: with 00 bytes (RED) only packet 1 of a merged burst is handled")
        check(res["rows"][1]["loss_free"], "uart: 30 ms (each line its own chunk) is loss-free")
        d = res["rows"][2]["arrived"]
        check(d[0] == 3 and sum(d[1:]) > 0 and not res["rows"][2]["loss_free"],
              f"uart: without 00 bytes more than packet 1 is handled: {d}")
        check(res["diag"] == "parser", f"uart: diagnostic says parser (got {res['diag']})")
        check(res["best_gap"] == 30.0, "uart: smallest loss-free gap 30 ms")
    check(kb.chunks > 0, f"uart: {kb.chunks} chunks went through the real handler")

    # 5. quiet on, "drop": the 0x27 marker in a rotating slot is the measurement.
    kb, rc, res = _pace_run("quiet on, drop < 10 ms", ("drop", 10.0),
                            ["--pace-gaps", "1,20", "--pace-diag-gap", "0"], quiet=True, frames=10)
    check(rc == 0, "quiet pace: script reports PASS")
    if res:
        check(res["rows"][0]["arrived"] == [1] + [0] * 9 and res["rows"][1]["loss_free"],
              "quiet pace: at 1 ms only a marker in slot 1 is answered; at 20 ms every slot")
        check(res["best_gap"] == 20.0, "quiet pace: smallest loss-free gap 20 ms")
    check(all(p[1] == 1 for p in kb.sent if p[0] == 0x25), "quiet pace: every 0x25 is 25 01")
    check(sum(1 for p in kb.sent if p[0] == 0x27) == 1 + 20, "quiet pace: one 0x27 per frame (+ the read-only one)")

    # 5b. --pace-mode-off: no 0x25, colours dropped, every valid 0x24 still echoed.
    kb, rc, res = _pace_run("mode off, drop < 10 ms", ("drop", 10.0),
                            ["--pace-gaps", "1,20", "--pace-diag-gap", "0", "--pace-mode-off"])
    check(rc == 0, "mode off: script reports PASS")
    if res:
        check(res["rows"][0]["arrived"] == [3] + [0] * 9 and res["rows"][1]["loss_free"]
              and res["best_gap"] == 20.0, "mode off: same measurement (1 ms packet 1 only, 20 ms loss-free)")
    check([p[:2] for p in kb.sent if p[0] in (0x25, 0x26)] == [bytes([0x25, 0x00]), bytes([0x26, 0x00])],
          "mode off: only the prelude 25 00 / 26; no 0x25 before the step, none after")
    check(all(s == "stock" for _, s in kb.timeline), "mode off: the LEDs never left the normal effect")
    for name, args in (("--pace-mode-off with another step", RX_ARGS + ["--led", "--steps", "pace,colors",
                                                                         "--pace-mode-off"]),):
        kb = RadioKeyboard2()
        _, rc, _ = _run_script(f"refuse: {name}", kb, args, fake_receiver_hid(kb))
        check(rc == ("exit", 2) and not kb.sent, f"{name}: argument error, nothing sent")

    # 6. --packet-gap on the other steps: the hardware failure reproduced, then fixed.
    for gap, want in ((None, 1), ("15", 0)):
        kb = RadioKeyboard2(loss=("drop", 10.0))
        args = RX_ARGS + ["--led", "--steps", "colors"] + (["--packet-gap", gap] if gap else [])
        mod, rc, _ = _run_script(f"colors, drop < 10 ms, --packet-gap {gap}", kb, args, fake_receiver_hid(kb))
        print(f"\n== hostsim checks (colors, drop < 10 ms, --packet-gap {gap}) ==")
        _radio2_common(kb, mod, f"--packet-gap {gap}")
        seq = [s for _, s in kb.timeline]
        if gap is None:
            check(rc == 1, "no --packet-gap: missing echoes, FAIL (as on hardware 2026-10-21)")
            check(not any(s.startswith("solid") for s in seq), "no --packet-gap: never a whole board")
        else:
            check(rc == 0, "--packet-gap 15: PASS, no echo missing")
            check(_dedupe_bench(seq) == ["stock", "solid ff0000", "solid 00ff00", "solid 0000ff", "stock"],
                  "--packet-gap 15: RED, GREEN, BLUE, normal effect: " + " -> ".join(_compress(seq)))
            idx = [i for i, p in enumerate(kb.sent) if p[0] == 0x24]
            check(_spacing_ok(kb, 15, idx), f"--packet-gap 15: stream packets arrived >= 15 ms apart "
                                            f"(min {_min_spacing(kb, idx):.2f})")

    # 7. USB with --packet-gap: paced, still PASS.
    kb = Keyboard(hexpath=V2_HEX)
    mod, rc, _ = _run_script("USB v2, colors, --packet-gap 3", kb, ["--led", "--steps", "colors", "--packet-gap", "3"],
                             fake_hid(kb))
    print("\n== hostsim checks (USB, --packet-gap 3) ==")
    check(rc == 0, "USB --packet-gap 3: PASS")
    idx = [i for i, p in enumerate(kb.sent) if p[0] == 0x24]
    check(_spacing_ok(kb, 3, idx), f"USB --packet-gap 3: stream packets arrived >= 3 ms apart "
                                   f"(min {_min_spacing(kb, idx):.2f})")
    check(emutest.failed == 0, f"emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")

    # 8. Argument refusals: nothing sent.
    for name, args in (("pace on USB", ["--led", "--steps", "pace"]),
                       ("--pace-gaps 0", RX_ARGS + ["--led", "--steps", "pace", "--pace-gaps", "0"]),
                       ("--pace-gaps text", RX_ARGS + ["--led", "--steps", "pace", "--pace-gaps", "a"]),
                       ("--packet-gap -1", RX_ARGS + ["--led", "--packet-gap", "-1"]),
                       ("--pace-frames 0", RX_ARGS + ["--led", "--steps", "pace", "--pace-frames", "0"]),
                       ("too many pace frames", RX_ARGS + ["--led", "--steps", "pace", "--pace-frames", "200",
                                                           "--pace-gaps", ",".join(["1"] * 20)])):
        kb = RadioKeyboard2()
        _, rc, _ = _run_script(f"refuse: {name}", kb, args, fake_receiver_hid(kb))
        check(rc == ("exit", 2) and not kb.sent, f"{name}: argument error, nothing sent")


# ---- soak and partial steps against the fake receiver ------------------------------

SOAK_ARGS = ["--led", "--steps", "soak", "--packet-gap", "75"]


def _soak_key(p):
    """(frame index, position) of a tagged soak packet, else None (tag = frame + 1)."""
    if p[0] != 0x24 or p[30] & 0xC0 != 0xC0 or not p[31] & 0x80:
        return None
    return ((p[30] & 0x1F) << 7 | (p[31] & 0x7F)) - 1, p[1] // 9


def _soak_run(name, extra, quiet=False, loss=None, damage=None):
    kb = RadioKeyboard2(loss=loss, damage=damage)
    args = RX_ARGS + SOAK_ARGS + (["--quiet"] if quiet else []) + extra
    mod, rc, _ = _run_script(f"soak, {name}", kb, args, fake_receiver_hid(kb))
    res = _run_script.captured.get("soak")
    print(f"\n== hostsim checks (soak, {name}) ==")
    _radio2_common(kb, mod, name)
    check(res is not None, f"{name}: the soak step ran and left its result")
    soak = [p for p in kb.sent if _soak_key(p) is not None]
    check(all(not mod.is_vendor_frame(p) and _stream_valid(p) for p in soak),
          f"{name}: every soak packet is a valid 0x24 and never a vendor CRC frame")
    idx = [i for i, p in enumerate(kb.sent) if _soak_key(p) is not None or (p[0] == 0x22 and i > 5)]
    check(_spacing_ok(kb, 75, idx), f"{name}: soak packets arrived >= 75 ms apart (min {_min_spacing(kb, idx):.1f})")
    return kb, mod, rc, res or {}


def _damage_at(table):
    """damage callable: table maps (frame, position) or ("probe", n) to (way, xors)."""
    probes = [0]

    def damage(i, p):
        if p == bytes([0x22]) + bytes(31) and i > 5:        # a soak probe (not the read-only 0x22)
            probes[0] += 1
            return table.get(("probe", probes[0]))
        return table.get(_soak_key(p))
    return damage


def run_soak():
    # 1. Ideal receiver, quiet off: every echo exact.
    kb, mod, rc, res = _soak_run("quiet off, ideal", ["--soak-packets", "40"])
    check(rc == 0, "quiet off ideal: PASS")
    check(res.get("exact") == 40 and res.get("lost") == 0 and res.get("events") == 0,
          f"quiet off ideal: 40/40 exact echoes, none lost, no unexpected reply ({res.get('exact')}, "
          f"{res.get('lost')}, {res.get('events')})")
    check(sum(1 for p in kb.sent if _soak_key(p) is not None) == 40, "quiet off ideal: exactly 40 colour packets")

    # 2. Ideal receiver, quiet on: no echoes, every 0x22 probe answered.
    kb, mod, rc, res = _soak_run("quiet on, ideal", ["--soak-packets", "40"], quiet=True)
    pr = res.get("probes", {})
    check(rc == 0, "quiet on ideal: PASS")
    check(pr.get("sent") == 4 and pr.get("answered") == 4 and pr.get("lost") == 0 and res.get("events") == 0,
          f"quiet on ideal: 4/4 probes answered, no unexpected reply ({pr})")
    check(all(p[1] == 1 for p in kb.sent if p[0] == 0x25), "quiet on: every 0x25 is 25 01")
    check(not kb.lines or all(ln[1][6] not in (0x24,) for ln in kb.lines),
          "quiet on: the keyboard queued no reply line for any 0x24")

    # 3. Quiet off, damage injected: each reply classified as the firmware's rules dictate.
    table = {(1, 2): ("down", {2: 0x06}),     # count 9 -> 15: the keyboard answers 24 FE
             (1, 5): ("up", {1: 0x40}),       # echo start 45 -> 109: an echo the keyboard never sends
             (2, 1): ("down", {10: 0x18}),    # a colour byte: rendered and echoed damaged
             (3, 7): ("down", {0: 0x04}),     # 0x24 -> 0x20: VIA 'unhandled', FF + copy
             (4, 3): ("up", {0: 0x02}),       # echo byte 0 -> 0x26
             (5, 9): ("up", {0: 0x03})}       # echo byte 0 -> 0x27 with a copied body
    kb, mod, rc, res = _soak_run("quiet off, damage injected", ["--soak-packets", "60"],
                                 damage=_damage_at(table))
    check(len(kb.damaged) == 6, f"quiet off damage: all 6 damages applied ({len(kb.damaged)})")
    check(rc == 1, "quiet off damage: FAIL (damage seen)")
    check(res.get("kinds") == {"down": 1, "up": 2, "either": 3},
          f"quiet off damage: 1 down, 2 up, 3 either ({res.get('kinds')})")
    check(res.get("byte0") == 3 and res.get("via") == 1 and res.get("via_writes") == 0,
          f"quiet off damage: byte 0 changed 3x, VIA would have run 1 (FF) "
          f"({res.get('byte0')}, {res.get('via')})")
    check(res.get("exact") == 54 and res.get("damaged") == 6 and res.get("lost") == 0,
          f"quiet off damage: 54 exact, 6 damaged replies, none without a reply "
          f"({res.get('exact')}, {res.get('damaged')}, {res.get('lost')})")
    check(res.get("damaged_refs") == sorted(table),
          f"quiet off damage: each damaged reply matched to the packet that was damaged ({res.get('damaged_refs')})")

    # 4. Quiet on, damage injected.
    table = {(1, 4): ("down", {2: 0x06}),     # count -> 15: 24 FE although quiet is on
             (2, 6): ("down", {0: 0x04}),     # 0x24 -> 0x20: FF reply although quiet is on
             ("probe", 3): ("up", {3: 0x0C}),  # the 3rd probe's reply 22 01 00 06 -> 22 01 00 0a
             (4, 8): ("down", {0: 0x01})}     # 0x24 -> 0x25, start 72 even: quiet goes off
    kb, mod, rc, res = _soak_run("quiet on, damage injected", ["--soak-packets", "50"], quiet=True,
                                 damage=_damage_at(table))
    pr = res.get("probes", {})
    check(len(kb.damaged) == 4, f"quiet on damage: all 4 damages applied ({len(kb.damaged)})")
    check(rc == 1, "quiet on damage: FAIL (damage seen)")
    check(res.get("kinds") == {"down": 3, "up": 1, "quiet-off": 1},
          f"quiet on damage: 3 down, 1 up, 1 quiet-off (packet 10 echoed after the 0x25) ({res.get('kinds')})")
    check(pr.get("sent") == 5 and pr.get("answered") == 4 and pr.get("damaged") == 1 and pr.get("lost") == 0,
          f"quiet on damage: probes 5 sent, 4 answered, 1 reply damaged, 0 lost ({pr})")

    # 5. Lossy receiver (20 % of 0x24 lost), quiet off: lost echoes are counted, not misread as damage.
    kb, mod, rc, res = _soak_run("quiet off, 20 % random loss", ["--soak-packets", "40"], loss=("random", 0.2))
    lost_truth = sum(1 for i, p in enumerate(kb.sent) if _soak_key(p) is not None and i not in set(kb.accepted))
    check(res.get("lost") == lost_truth and res.get("events") == 0 and lost_truth > 0,
          f"loss: {res.get('lost')} without a reply = {lost_truth} that never reached hook C, no false damage")
    check(rc == 1, "loss: the soak reports the loss (FAIL), as a finding")

    # 6. Argument refusals: nothing sent.
    for name, args in (("soak on USB", ["--led", "--steps", "soak", "--packet-gap", "80"]),
                       ("partial on USB", ["--led", "--steps", "partial", "--packet-gap", "80"]),
                       ("soak --packet-gap 50", RX_ARGS + ["--led", "--steps", "soak", "--packet-gap", "50"]),
                       ("partial without --packet-gap", RX_ARGS + ["--led", "--steps", "partial"]),
                       ("--soak-packets 5", RX_ARGS + SOAK_ARGS + ["--soak-packets", "5"]),
                       ("--refresh-ms 2500", RX_ARGS + ["--led", "--steps", "partial", "--packet-gap", "80",
                                                        "--refresh-ms", "2500"]),
                       ("--partial-phases bogus", RX_ARGS + ["--led", "--steps", "partial", "--packet-gap",
                                                             "80", "--partial-phases", "dot,bogus"])):
        kb = RadioKeyboard2()
        _, rc, _ = _run_script(f"refuse: {name}", kb, args, fake_receiver_hid(kb))
        check(rc == ("exit", 2) and not kb.sent, f"{name}: argument error, nothing sent")


def _partial_run(name, loss=None, extra=()):
    kb = RadioKeyboard2(loss=loss)
    snaps = {}

    def setup(mod):
        mod.PHASE_HOOK = lambda phase, frame: snaps.__setitem__(
            phase, ([tuple(px) for px in kb.buf], [tuple(px) for px in frame], kb.view is not None))
    args = RX_ARGS + ["--led", "--steps", "partial", "--packet-gap", "80", "--quiet"] + list(extra)
    mod, rc, _ = _run_script(f"partial, {name}", kb, args, fake_receiver_hid(kb), setup=setup)
    res = _run_script.captured.get("partial") or {}
    print(f"\n== hostsim checks (partial, {name}) ==")
    _radio2_common(kb, mod, name)
    stream = [i for i, p in enumerate(kb.sent) if p[0] == 0x24 and p[1] % 9 == 0 and i > 8]
    check(all(_stream_valid(kb.sent[i]) and kb.sent[i][2] == (5 if kb.sent[i][1] == 81 else 9)
              and not mod.is_vendor_frame(kb.sent[i]) for i in stream),
          f"{name}: every packet is one whole 9-LED group (valid 0x24, never a vendor CRC frame)")
    check(_spacing_ok(kb, 80, stream), f"{name}: packets arrived >= 80 ms apart (min {_min_spacing(kb, stream):.1f})")
    i25 = max(i for i, p in enumerate(kb.sent) if p[0] == 0x25)
    check(kb.t_sent[i25 + 1] - kb.t_sent[i25] >= 99.0, "the first colour packet came >= 100 ms after 0x25")
    seq = [s for _, s in kb.timeline]
    lit = [k for k, s in enumerate(seq) if s != "stock"]
    check(bool(lit) and "stock" not in seq[lit[0]:lit[-1] + 1],
          f"{name}: no fall-back to the normal effect while streaming: " + " -> ".join(_compress(seq)))
    stats = {st["phase"]: st for st in res.get("stats", [])}
    return kb, rc, res, snaps, stats


def run_partial():
    # 1. Ideal receiver.
    kb, rc, res, snaps, stats = _partial_run("ideal receiver")
    check(rc == 0, "ideal: PASS (no reply to any packet)")
    for phase in ("static", "fn", "wipe"):
        buf, want, lit = snaps.get(phase, (None, None, False))
        check(buf == want and lit, f"ideal: at the end of '{phase}' the frame buffer is exactly the scene, rendered")
    buf, want, lit = snaps.get("dot", (None, None, False))
    wrong = sum(1 for a, b in zip(buf or [], want or []) if a != b)
    check(buf is not None and wrong <= 2 and lit, f"ideal: at the end of 'dot' at most the dot's 2 LEDs lag ({wrong})")
    st = stats.get("static", {})
    check(st.get("changed") == 10 and st.get("refresh", 0) > 30,
          f"ideal static: 10 packets for the first fill, then refresh only ({st.get('changed')}, {st.get('refresh')})")
    st = stats.get("dot", {})
    check(st and max(st["lag_steady"]) < 260, f"ideal dot: after the lead-in every change sent within 260 ms "
                                              f"(max {max(st['lag_steady']):.0f})")
    st = stats.get("wipe", {})
    check(st and st.get("changed", 0) >= 40, f"ideal wipe: all 10 groups sent for each of 4 colours ({st.get('changed')})")

    # 2. 25 % random loss: the refresh repairs the static phases; no timeout fall-back.
    kb, rc, res, snaps, stats = _partial_run("25 % random loss", loss=("random", 0.25))
    lost = len([i for i, p in enumerate(kb.sent) if p[0] == 0x24 and i > 8]) - len(
        [i for i in kb.accepted if kb.sent[i][0] == 0x24 and i > 8])
    check(lost > 20, f"loss: {lost} colour packets lost")
    for phase in ("static", "fn"):
        buf, want, lit = snaps.get(phase, (None, None, False))
        check(buf == want and lit, f"loss: at the end of '{phase}' the refresh has repaired every lost group")

    # 3. --partial-phases dot: only the walk, white on LEDs that are off.
    kb, rc, res, snaps, stats = _partial_run("dot only", extra=["--partial-phases", "dot"])
    check(rc == 0 and list(stats) == ["dot"] and list(snaps) == ["dot"], "dot only: PASS, only the dot phase ran")
    seq = [s for _, s in kb.timeline]
    walk = [int(s.split()[1]) for s in seq if s.startswith("led ") and s.endswith("ffffff")]
    check(walk == sorted(walk) and set(walk) == set(range(N_LEDS)),
          f"dot only: the board showed exactly one white LED at a time for each of the {N_LEDS} LEDs, in order "
          f"({len(set(walk))} seen); anything else in between: "
          f"{sorted(set(s for s in seq if not s.startswith('led ')))}")


IDLE_ARGS = ["--led", "--steps", "idle", "--packet-gap", "80"]


def _idle_run(name, extra, quiet=True, loss=None):
    kb = RadioKeyboard2(loss=loss)
    args = RX_ARGS + IDLE_ARGS + (["--quiet"] if quiet else []) + list(extra)
    mod, rc, _ = _run_script(f"idle, {name}", kb, args, fake_receiver_hid(kb))
    res = _run_script.captured.get("idle") or {}
    print(f"\n== hostsim checks (idle, {name}) ==")
    _radio2_common(kb, mod, name)
    check(bool(res), f"{name}: the idle step ran and left its result")
    # The read-only checks and the known-state prelude (0x25, 0x26) come first; the step
    # starts after the prelude's 0x26.
    first = next(i for i, p in enumerate(kb.sent) if p[0] == 0x26) + 1
    return kb, mod, rc, res, first


def run_idle():
    # 1. Lead-in, then silence, then probes: the stream, the fall-back without 0x26, nothing
    #    sent in the silence, probes only.
    kb, mod, rc, res, first = _idle_run("lead 2 s, silent 3 s, 5 probes", [
        "--idle-lead", "2", "--idle-silent", "3", "--idle-probe-every", "1", "--idle-probe-for", "5"])
    check(rc == 0, "lead: PASS")
    step = kb.sent[first:]
    lead = [first + k for k, p in enumerate(step) if p[0] == 0x24]
    probes = [first + k for k, p in enumerate(step) if p[0] == 0x22]
    cmds = [p[0] for p in step if p[0] not in (0x24, 0x22)]
    check(cmds[:1] == [0x25] and cmds[1:] == [0x26], "lead: one 0x25 before the stream, 0x26 only at the very end "
                                                      f"({' '.join(f'{c:02x}' for c in cmds)})")
    check(len(lead) >= 10 and all(_stream_valid(kb.sent[i]) and kb.sent[i][1] % 9 == 0
                                  and not mod.is_vendor_frame(kb.sent[i]) for i in lead),
          f"lead: {len(lead)} colour packets, each one whole 9-LED group, never a vendor CRC frame")
    check(_spacing_ok(kb, 80, lead), f"lead: packets arrived >= 80 ms apart (min {_min_spacing(kb, lead):.1f})")
    check(len(probes) == 5 and bool(lead) and probes[0] == lead[-1] + 1
          and probes == list(range(probes[0], probes[0] + 5)),
          f"lead: after the stream exactly 5 packets, all 0x22 probes ({len(probes)})")
    silence = kb.t_sent[probes[0]] - kb.t_sent[lead[-1]] if probes and lead else 0
    check(silence >= 2990, f"lead: nothing reached the keyboard for {silence:.0f} ms after the stream (>= 3000)")
    gaps = [kb.t_sent[b] - kb.t_sent[a] for a, b in zip(probes, probes[1:])]
    check(bool(gaps) and min(gaps) >= 990 and max(gaps) <= 1100, f"lead: probes 1 s apart ({[round(g) for g in gaps]})")
    check(res.get("answered") == 5 and res.get("lost") == 0 and res.get("events") == 0,
          f"lead: 5/5 probes answered, no unexpected reply ({res.get('answered')}, {res.get('lost')}, "
          f"{res.get('events')})")
    t_last = kb.t_sent[lead[-1]] if lead else 0
    shown = [s for t, s in kb.timeline if t <= t_last]
    back = [t for t, s in kb.timeline if t > t_last and s == "stock"]
    check("solid ff00ff" in shown, "lead: the board showed the stream (magenta)")
    check(bool(back) and 1990 <= back[0] - t_last <= 2400,
          f"lead: normal effect back {back[0] - t_last if back else float('nan'):.0f} ms after the last colour packet "
          "(the 2 s timeout, no 0x26)")
    check(bool(back) and not any(s != "stock" for t, s in kb.timeline if t > back[0]),
          "lead: no stream shown again after the fall-back (probes do not render)")

    # 2. A keyboard that ignores the first 3 probes (asleep): reported as lost, not a FAIL.
    n22 = [0]

    def asleep(i, p, t):
        if p[0] == 0x22 and i > 4:              # not the read-only check's 0x22
            n22[0] += 1
            return n22[0] <= 3
        return False
    kb, mod, rc, res, first = _idle_run("asleep for 3 probes", [
        "--idle-lead", "1", "--idle-silent", "1", "--idle-probe-every", "1", "--idle-probe-for", "5"],
        loss=("func", asleep))
    check(rc == 0, "asleep: PASS (lost probes are the measurement)")
    check(res.get("answered") == 2 and res.get("lost") == 3 and res.get("lost_runs") == [(1, 3)],
          f"asleep: probes 1-3 lost, 4-5 answered ({res.get('answered')}, {res.get('lost')}, {res.get('lost_runs')})")

    # 3. No lead-in: no 0x25 after the prelude, no colour packet at all.
    kb, mod, rc, res, first = _idle_run("no lead-in", [
        "--idle-lead", "0", "--idle-silent", "1", "--idle-probe-every", "1", "--idle-probe-for", "3"])
    step = kb.sent[first:]
    check(rc == 0 and [p[0] for p in step] == [0x22] * 3,
          f"no lead: only the 3 probes after the prelude ({' '.join(f'{p[0]:02x}' for p in step)})")
    check(sum(1 for p in kb.sent if p[0] == 0x25) == 1, "no lead: 0x25 only in the known-state prelude")
    check(res.get("answered") == 3, f"no lead: 3/3 probes answered ({res.get('answered')})")

    # 4. Quiet off: every lead-in echo is expected and taken.
    kb, mod, rc, res, first = _idle_run("quiet off", [
        "--idle-lead", "1.5", "--idle-silent", "1", "--idle-probe-every", "1", "--idle-probe-for", "2"], quiet=False)
    check(rc == 0 and res.get("events") == 0 and res.get("answered") == 2,
          f"quiet off: PASS, echoes all taken, 2/2 probes ({res.get('events')}, {res.get('answered')})")

    # 5. Refusals: nothing sent.
    for name, args in (("idle on USB", ["--led", "--steps", "idle", "--packet-gap", "80"]),
                       ("idle --packet-gap 50", RX_ARGS + ["--led", "--steps", "idle", "--packet-gap", "50"]),
                       ("idle with another step", RX_ARGS + ["--led", "--steps", "idle,colors", "--packet-gap",
                                                             "80"]),
                       ("--idle-probe-every 0.5", RX_ARGS + IDLE_ARGS + ["--idle-probe-every", "0.5"]),
                       ("--idle-silent 4000", RX_ARGS + IDLE_ARGS + ["--idle-silent", "4000"])):
        kb = RadioKeyboard2()
        _, rc, _ = _run_script(f"refuse: {name}", kb, args, fake_receiver_hid(kb))
        check(rc == ("exit", 2) and not kb.sent, f"{name}: argument error, nothing sent")


def run_plugin(path):
    rec = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rec, dict) and rec.get("transport") == "radio":
        run_plugin_radio(rec)
        return
    kb = Keyboard()
    writes = [e for e in rec["events"] if e["op"] == "write"]
    print(f"replaying {len(writes)} plugin writes through the emulated patch")
    check(all(e["length"] == 33 and len(e["data"]) <= 33 and e["data"][0] == 0 for e in writes),
          "every write: report ID 0, length 33")
    check(all(0x21 <= e["data"][1] <= 0x28 for e in writes), "plugin wrote only 0x21-0x28")
    replies_ok = True
    frames = {"frame1": rec["frame1"], "frame2": rec["frame2"]}
    for e in rec["events"]:
        if e["op"] == "mark":
            if e["name"] in frames:
                want = [tuple(px) for px in frames[e["name"]]]
                check(kb.buf == want, f"{e['name']}: all 86 LEDs reached set_color with the plugin's colours")
                check(kb.view == want, f"{e['name']}: keyboard shows it (render skipped the effect)")
            continue
        if e["op"] != "write":
            continue
        data = bytes(e["data"]) + bytes(33 - len(e["data"]))
        kb.packet(data[1:33])
        want = e.get("mock_reply")
        got = kb.inbox.popleft() if kb.inbox else None
        if want is None:
            replies_ok &= got is None
        else:
            replies_ok &= got is not None and list(got) == want[1:33]
    check(replies_ok, "the emulated patch replied exactly where and what the mock replied")
    kb.tick()
    check(kb.view is None, "after the final Shutdown (0x26) the stock effect renders again")
    check(emutest.failed == 0, f"emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")


RX_PLUGIN = ROOT / "plugin" / "Meletrix_Zoom75_TIGA_24G_SignalRGB.js"
RADIO_STATE = 0x20007E10                          # patch v2: "SRGR", last_ms, enabled +8, quiet +9


def _radio_state(kb):
    b = kb.board
    return dict(valid=b.r32(RADIO_STATE) == 0x53524752, enabled=b.r8(RADIO_STATE + 8), quiet=b.r8(RADIO_STATE + 9))


def _radio_plugin_checks(kb, label):
    cmds = [p[0] for p in kb.sent]
    check(set(cmds) <= {0x21, 0x22, 0x24, 0x25, 0x26, 0x27, 0x28},
          f"{label}: only 21 22 24 25 26 27 28 reached the keyboard, never 0x23 or 0x13 ({sorted(set(cmds))})")
    check(all(p[1] == 1 for p in kb.sent if p[0] == 0x25), f"{label}: every 0x25 is 25 01 (quiet)")
    check(not any(_updater_crc(p) == p[6] | p[7] << 8 for p in kb.sent),
          f"{label}: no packet is a valid vendor CRC frame (the receiver updater's own CRC)")
    check(not kb.bad_calls, f"{label}: real radio path clean" + ("" if not kb.bad_calls else f" {kb.bad_calls[:2]}"))
    check(emutest.failed == 0, f"{label}: emulator-level checks: {emutest.passed} passed, {emutest.failed} failed")


def run_plugin_radio(rec):
    """Replay of the plugin's 2.4G packets (plugintest.mjs, mock receiver) through patch v2."""
    kb = RadioKeyboard2()
    writes = [e for e in rec["events"] if e["op"] == "write"]
    print(f"replaying {len(writes)} 2.4G plugin writes through patch v2 behind the fake receiver")
    replies_ok = True
    for e in rec["events"]:
        if e["op"] == "mark":
            if e.get("frame"):
                kb.tick()
                want = [tuple(px) for px in e["frame"]]
                check(kb.buf == want and kb.view == want,
                      f"{e['name']}: the keyboard shows the plugin's scene (all 86 LEDs, rendered)")
            continue
        if e["op"] != "write":
            continue
        data = bytes(e["data"]) + bytes(33 - len(e["data"]))
        kb.packet(data[1:33])
        want = e.get("mock_reply")
        got = kb.inbox.popleft() if kb.inbox else None
        same = (got is None) if want is None else (got is not None and list(got) == want[1:33])
        if not same:
            print(f"    differs: {data[1:5].hex(' ')}: mock {want and bytes(want[1:5]).hex(' ')}, "
                  f"patch v2 {got and got[:4].hex(' ')}")
        replies_ok &= same
    check(replies_ok, "patch v2 replied exactly where and what the mock receiver replied (queries only; quiet on)")
    st = _radio_state(kb)
    check(st["valid"] and st["quiet"] == 1 and st["enabled"] == 0, f"after the final 0x26: mode off, quiet still on ({st})")
    kb.tick()
    check(kb.view is None, "after the final 0x26 the stock effect renders again")
    _radio_plugin_checks(kb, "replay")


def _bridge(name, scenario, loss=None):
    """Run plugin_bridge.mjs (the 2.4G plugin, real time) against patch v2 behind the fake receiver."""
    print(f"\n######## plugin2g, {name}: node tools/plugin_bridge.mjs {RX_PLUGIN.name} {scenario}")
    kb = RadioKeyboard2(loss=loss)
    proc = subprocess.Popen(["node", str(TOOLS / "plugin_bridge.mjs"), str(RX_PLUGIN), scenario],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    out = dict(kb=kb, marks={}, notes=[], denotes=[], logs=[], events=[], bad_writes=0)
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        m = json.loads(line)
        op, resp = m["op"], {}
        if op == "write":
            data = bytes(m["data"])
            if m["length"] != 33 or len(data) > 33 or data[0] != 0:
                out["bad_writes"] += 1
            kb.packet((data[1:] + bytes(32))[:32])
            resp = {"n": 33}
        elif op == "read":
            if not kb.inbox:
                if m["timeout"] > 0:
                    time.sleep(m["timeout"] / 1000.0)
                kb.tick()
            resp = {"data": [0] + list(kb.inbox.popleft()) if kb.inbox else []}
        elif op == "log":
            out["logs"].append(m["msg"])
            print(f"    plugin log: {m['msg']}")
        elif op == "notify":
            out["notes"].append((m["title"], m["text"]))
            resp = {"id": f"alert{len(out['notes'])}"}
        elif op == "denotify":
            out["denotes"].append(m["id"])
        elif op == "mark":
            kb.tick()
            out["marks"][m["name"]] = dict(buf=list(kb.buf), want=[tuple(px) for px in m["frame"]],
                                           lit=kb.view is not None, n=len(kb.sent), state=_radio_state(kb))
        elif op == "kb":
            out["events"].append((m["action"], m["value"], len(kb.sent)))
            if m["action"] == "mode":
                kb.board.w8(emutest.MODE, m["value"])
            elif m["action"] == "reset":
                kb.board.w32(RADIO_STATE, 0)            # v2 re-initialises it: mode off, quiet off
        proc.stdin.write(json.dumps(resp) + "\n")
        proc.stdin.flush()
        if op == "end":
            break
    rc = proc.wait(timeout=30)
    print(f"\n== hostsim checks (plugin2g, {name}) ==")
    check(rc == 0 and out["bad_writes"] == 0, f"{name}: the bridge ran to the end; every write report ID 0, length 33")
    _radio_plugin_checks(kb, name)
    idx = list(range(len(kb.sent)))
    after_cmd = [b for a, b in zip(idx, idx[1:]) if kb.sent[a][0] in (0x25, 0x26)]
    plain = [(a, b) for a, b in zip(idx, idx[1:]) if b not in after_cmd]
    d_plain = min((kb.t_sent[b] - kb.t_sent[a] for a, b in plain), default=float("inf"))
    d_cmd = min((kb.t_sent[b] - kb.t_sent[b - 1] for b in after_cmd), default=float("inf"))
    check(d_plain >= 79.0 and d_cmd >= 99.0,
          f"{name}: at the keyboard >= 80 ms between packets (min {d_plain:.1f}; Date.now has 1 ms steps), "
          f">= 100 ms after 0x25/0x26 (min {d_cmd:.1f})")
    return out


def _mark_shows(out, name):
    mk = out["marks"].get(name)
    wrong = sum(1 for a, b in zip(mk["buf"], mk["want"]) if a != b) if mk else None
    check(mk is not None and wrong == 0 and mk["lit"], f"{out['label']}: at '{name}' the keyboard shows the scene "
                                                       f"(rendered; {wrong} LEDs differ)")


def run_plugin2g():
    # 1. Ideal link, the full scenario.
    out = _bridge("full scenario", "full")
    out["label"] = "full"
    kb = out["kb"]
    for name in ("fill", "wipe red", "wipe yellow", "back", "healed"):
        _mark_shows(out, name)
    hs = [p[0] for p in kb.sent[:5]]
    check(hs == [0x22, 0x28, 0x21, 0x27, 0x25], f"full: handshake 22 28 21 27, then 25 01 ({bytes(hs).hex(' ')})")
    ev = {a: n for a, v, n in out["events"] if a == "reset"}
    modes = [(v, n) for a, v, n in out["events"] if a == "mode"]
    (_, n_left), (_, n_back) = modes
    left = kb.sent[n_left:n_back]
    check(sum(1 for p in left if p[0] == 0x24) <= 2 and all(p[0] in (0x22, 0x24) for p in left),
          f"full: after the switch to USB mode at most 2 colour packets, then only 0x22 retries "
          f"({bytes(p[0] for p in left).hex(' ')})")
    check(any("Fn+V" in t for _, t in out["notes"]), "full: the user was told to press Fn+V")
    back = kb.sent[n_back:ev["reset"]]
    check(bytes(p[0] for p in back[:5]) == bytes([0x22, 0x28, 0x21, 0x27, 0x25]) and all(p[0] == 0x24 for p in back[5:]),
          "full: after Fn+V one handshake, 25 01, then colour packets")
    check(len(out["denotes"]) == len(out["notes"]), "full: the notification was removed after recovery")
    healed = kb.sent[ev["reset"]:out["marks"]["healed"]["n"]]
    check(sum(1 for p in healed if p[0] == 0x25) == 1, "full: after the radio state reset exactly one 25 01")
    st = out["marks"]["healed"]["state"]
    check(st["valid"] and st["enabled"] == 1 and st["quiet"] == 1, f"full: the keyboard is back in mode on, quiet on ({st})")
    check(any("echoed a colour packet" in m for m in out["logs"]), "full: the reset was logged")
    sd = out["marks"]["shutdown"]
    check(kb.sent[-1][0] == 0x26 and not sd["lit"], "full: Shutdown sent 0x26 last; the stock effect renders")
    check(sum(1 for p in kb.sent if p[0] == 0x26) == 1, "full: one 0x26 in the whole run")

    # 2. 25 % of the colour packets lost: the refresh repairs the board.
    out = _bridge("25 % random loss", "still", loss=("random", 0.25))
    out["label"] = "loss"
    kb = out["kb"]
    lost = len(kb.sent) - len(kb.accepted)
    check(lost > 5, f"loss: {lost} colour packets lost")
    _mark_shows(out, "still")

    # 3. "busy" link model: a packet less than 75 ms after the last one kept is lost. The
    #    plugin's pacing must never trigger it.
    out = _bridge("busy link model, 75 ms", "still", loss=("busy", 75))
    out["label"] = "busy"
    kb = out["kb"]
    check(len(kb.accepted) == len(kb.sent), f"busy: no packet lost ({len(kb.sent) - len(kb.accepted)} of {len(kb.sent)})")
    _mark_shows(out, "still")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("hidtest", "stock", "plugin", "radio", "radio2",
                                                 "hidtest2", "pace", "soak", "partial", "idle", "plugin2g"):
        sys.exit(__doc__)
    what, rest = sys.argv[1], sys.argv[2:]
    if what == "plugin":
        run_plugin(rest[0])
    elif what == "soak":
        run_soak()
    elif what == "partial":
        run_partial()
    elif what == "idle":
        run_idle()
    elif what == "plugin2g":
        run_plugin2g()
    elif what == "radio":
        run_radio()
    elif what == "radio2":
        run_radio2()
    elif what == "pace":
        run_pace()
    elif what == "hidtest2":
        run_hidtest(rest, stock=False, hexpath=V2_HEX)
    else:
        run_hidtest(rest, stock=(what == "stock"))
    print(f"\nhostsim: {'PASS' if fails == 0 else f'FAIL ({fails})'}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
