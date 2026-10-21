// Offline test of the SignalRGB plugins with a mock `device` (no hardware, no SignalRGB).
//
// usage: node tools/plugintest.mjs [usb_plugin.js] [packets_out.json] [rx_packets_out.json]
//
// USB file (Meletrix_Zoom75_TIGA_SignalRGB.js, 1EA7:CEDD):
//   patched  mock answers like PHASE2_REPORT.md section 1: handshake, 0x25, streaming,
//            Forced colour, Shutdown 0x26, re-enable after Shutdown(true) + Render
//   stock    mock answers 0xFF (VIA "unhandled") like the stock firmware: must never
//            send 0x24/0x25/0x26, must notify, must stop retrying after 5 attempts
//   silent   no replies at first (VIA holding the interface), then the patch answers:
//            the 5 s retry must recover and start streaming
//   v1.0.1   the same three scenarios run on tests/fixtures/..._v1.0.1.js: every write,
//            read (with its timeout) and notification must be identical
// 2.4G file (Meletrix_Zoom75_TIGA_24G_SignalRGB.js, 6D66:8888), against a mock receiver
// with a fake clock that answers like patch v2 in 2.4G mode (replies after 45 ms; quiet
// switch; 0xFF + copy when the keyboard is not in 2.4G mode; optional loss; a packet sent
// while a reply is on its way or less than 50 ms after it arrived reaches VIA damaged and
// is answered 0xFF + its body, as the SignalRGB run of 2026-10-21 showed for a query sent
// 80 ms after the previous one):
//   handshake 22 28 21 27 then 25 01, never 0x23; >= 80 ms between any two packets
//            (100 after 0x25/0x26); at most one packet per Render(); the scene reaches
//            the keyboard; a still scene is refreshed (each group within 3 s, none sooner
//            than 1.5 s); a change goes out at once; one 0x26 at Shutdown; resume
//   loss     25 % of the colour packets lost: the refresh repairs the board
//   crc      a group whose bytes 6-7 form a valid vendor CRC is sent with one colour LSB
//            flipped, never as a valid CRC frame, and not more often than the others
//   fn+v     the keyboard leaves 2.4G mode mid-stream (0xFF replies): streaming stops,
//            one notification, retries send only 0x22 (5 s x5, then 10 s); after Fn+V the
//            handshake passes again and streaming resumes
//   heal     the keyboard's radio state is reset mid-stream (it echoes a colour packet):
//            exactly one 25 01, then streaming goes on without echoes
//   silent   no reply at all: one 0x22 per retry, never 0x24/0x25/0x26, one log line
// The packets of the patched scenarios are written to packets_out.json (USB) and
// rx_packets_out.json (2.4G), so that `python -I tools/hostsim.py plugin <file>` can
// replay them through the emulated patch (v1 on USB, v2 behind the fake receiver).
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pluginPath = process.argv[2] ?? join(root, "plugin", "Meletrix_Zoom75_TIGA_SignalRGB.js");
const packetsOut = process.argv[3] ?? join(root, "tests", "fixtures", "plugin_packets.json");
const rxPacketsOut = process.argv[4] ?? join(root, "tests", "fixtures", "plugin_packets_rx.json");
const rxPluginPath = join(root, "plugin", "Meletrix_Zoom75_TIGA_24G_SignalRGB.js");
const baselinePath = join(root, "tests", "fixtures", "Meletrix_Zoom75_TIGA_SignalRGB_v1.0.1.js");   // SHA-256 e6cbbf85...
const layout = JSON.parse(readFileSync(join(root, "plugin", "tiga_layout.json"), "utf8"));

let fails = 0;
function check(cond, msg) {
	console.log((cond ? "  ok    " : "  FAIL  ") + msg);
	if (!cond) fails++;
}

// Fresh module instance per scenario (module state is per URL).
let instance = 0;
async function loadPlugin(path = pluginPath) {
	const tmp = join(tmpdir(), `tiga_plugin_${process.pid}_${instance}.mjs`);
	copyFileSync(path, tmp);
	return import(pathToFileURL(tmp).href + `?i=${instance++}`);
}

const realNow = Date.now;
let fakeNow = 1_000_000;
Date.now = () => fakeNow;

function reply(cmd, b1 = 0, b2 = 0, b3 = 0) {
	const r = new Array(33).fill(0);
	r[1] = cmd; r[2] = b1; r[3] = b2; r[4] = b3;
	return r;
}

function makeDevice(firmware) {
	const dev = {
		firmware, events: [], inbox: [], logs: [], notes: [], denotes: [], lastRead: 0,
		colorFn: (x, y) => [(x * 8) & 0xFF, (y * 20) & 0xFF, (x * 3 + y * 7) & 0xFF],
		write(data, length) {
			const ev = { op: "write", data: [...data], length, mock_reply: null };
			this.events.push(ev);
			const cmd = data[1];
			let r = null;
			if (this.firmware === "patched") {
				const table = { 0x21: [0, 0, 0], 0x22: [1, 0, 6], 0x23: [0, 0, 0], 0x27: [86, 0, 0], 0x28: [2, 0, 0] };
				if (cmd in table) r = reply(cmd, ...table[cmd]);
			} else if (this.firmware === "stock") {
				r = new Array(33).fill(0);
				for (let i = 1; i < Math.min(33, data.length); i++) r[i] = data[i];
				r[1] = 0xFF;
			}
			if (r !== null) { this.inbox.push(r); ev.mock_reply = r; }
			return length;
		},
		read(data, length, timeout) {
			this.events.push({ op: "read", timeout });
			if (this.inbox.length) { const r = this.inbox.shift(); this.lastRead = r.length; return r; }
			this.lastRead = 0;
			return [];
		},
		getLastReadSize() { return this.lastRead; },
		pause() {},
		log(x) { this.logs.push(String(x)); },
		notify(t, d, p) { this.notes.push([t, d, p]); return `alert${this.notes.length}`; },
		denotify(id) { this.denotes.push(id); },
		color(x, y) { return this.colorFn(x, y); },
		writes() { return this.events.filter(e => e.op === "write"); },
		cmds() { return this.writes().map(e => e.data[1]); },
		mark(name) { this.events.push({ op: "mark", name }); },
	};
	return dev;
}

function framesFrom(writes, P) {
	// Rebuild the 86-LED frame from consecutive 0x24 packets.
	const frame = new Array(86).fill(null);
	for (const w of writes) {
		const [rid, cmd, start, count] = w.data;
		if (cmd !== 0x24) continue;
		for (let i = 0; i < count; i++) frame[start + i] = w.data.slice(4 + 3 * i, 7 + 3 * i);
	}
	return frame;
}

function checkStream(writes, expectFn, label, P) {
	const s = writes.filter(w => w.data[1] === 0x24);
	check(s.length === 10, `${label}: 10 stream packets per frame (got ${s.length})`);
	check(s.every((w, k) => w.data[2] === 9 * k && w.data[3] === (k < 9 ? 9 : 5)),
		`${label}: starts 0,9,..,81 and counts 9 x9 then 5`);
	check(s.every(w => w.length === 33 && w.data.length <= 33 && w.data[0] === 0),
		`${label}: report ID 0, length 33, payload fits`);
	const frame = framesFrom(s, P);
	const pos = P.LedPositions();
	check(frame.every((px, i) => px !== null && px.join() === expectFn(i, pos[i]).join()),
		`${label}: every LED index carries the colour sampled at its own position`);
	return frame;
}

// CRC16-CCITT-FALSE, bytes 6-7 zeroed (tools/srgb_hidtest.py vendor_crc).
function vendorCrc(p) {
	let c = 0xFFFF;
	for (let i = 0; i < 32; i++) {
		c ^= (i === 6 || i === 7 ? 0 : (p[i] ?? 0)) << 8;
		for (let b = 0; b < 8; b++) c = c & 0x8000 ? ((c << 1) ^ 0x1021) & 0xFFFF : (c << 1) & 0xFFFF;
	}
	return c;
}
const isVendorFrame = (p) => vendorCrc(p) === ((p[6] ?? 0) | (p[7] ?? 0) << 8);
const hexs = (a) => a.map(b => b.toString(16).padStart(2, "0")).join(" ");

console.log("== static exports ==");
for (const [path, vid, pid] of [[pluginPath, 0x1EA7, 0xCEDD], [rxPluginPath, 0x6D66, 0x8888]]) {
	globalThis.device = makeDevice("patched");
	const P = await loadPlugin(path);
	const tag = vid === 0x1EA7 ? "USB" : "2.4G";
	const names = P.LedNames(), pos = P.LedPositions(), size = P.Size();
	check(P.VendorId() === vid && P.ProductId() === pid, `${tag}: VID/PID ${hexs([vid >> 8, vid & 0xFF])}:${hexs([pid >> 8, pid & 0xFF])}`);
	check(names.length === 86 && pos.length === 86, `${tag}: 86 names and 86 positions`);
	check(JSON.stringify(names) === JSON.stringify(layout.names), `${tag}: names equal tools/gen_tiga_layout.py output`);
	check(JSON.stringify(pos) === JSON.stringify(layout.positions), `${tag}: positions equal tools/gen_tiga_layout.py output`);
	check(JSON.stringify(size) === JSON.stringify(layout.size), `${tag}: Size() ${JSON.stringify(size)} equals the generated canvas size`);
	check(pos.every(([x, y]) => Number.isInteger(x) && Number.isInteger(y) && x >= 0 && y >= 0 && x < size[0] && y < size[1]),
		`${tag}: every position inside Size()`);
	check(new Set(pos.map(p => p.join())).size === 86, `${tag}: no two LEDs share a canvas cell`);
	const params = P.ControllableParameters().map(p => p.property);
	check(!params.includes("shutdownMode") && !params.includes("shutdownColor"), `${tag}: no shutdown colour option (${params.join(", ")})`);
	const ep = (o) => P.Validate(Object.assign({ interface: 1, usage: 0x61, usage_page: 0xFF60, collection: 0 }, o));
	check(ep({}) === true, `${tag}: Validate accepts interface 1, 0xFF60/0x61`);
	check(!ep({ interface: 0 }) && !ep({ interface: 2 }) && !ep({ interface: 3 }), `${tag}: Validate rejects interfaces 0, 2, 3`);
	check(!ep({ usage_page: 0x0001 }) && !ep({ usage: 0x06 }), `${tag}: Validate rejects other usage page / usage`);
}
{
	const vectors = [["1302" + "00".repeat(30), 0xd230], ["240009" + "00".repeat(29), 0x5c39],
		["a54dca182530bb1d6d132cded6237b2ed91e3f721fcb1971174494d6493c9d5c", 0x829c],
		["3460be31201e69fedaa0eee8b9997f5c7c2999fdafe593253cd654af4dfad714", 0xa8bd],
		["27a0aeb3fee9232f8af2211f9ee491c5b10becb5563bfc1e6f93427ecbc8fe29", 0x10d2]];
	const bytes = (h) => h.match(/../g).map(x => parseInt(x, 16));
	check(vectors.every(([h, c]) => vendorCrc(bytes(h)) === c), "test CRC equals srgb_hidtest.vendor_crc on 5 vectors (EnterDFU frame included)");
	const src = readFileSync(rxPluginPath, "utf8");
	check(/function vendorCrc\(p\)[\s\S]*0x1021/.test(src) && /i === 6 \|\| i === 7 \? 0/.test(src),
		"2.4G plugin computes the same CRC (poly 0x1021, bytes 6-7 zeroed); behaviour checked in the crc scenario");
}

// ---- USB ------------------------------------------------------------------------------

async function usbPatched(path, checks) {
	const dev = makeDevice("patched");
	globalThis.device = dev;
	globalThis.LightingMode = "Canvas";
	globalThis.forcedColor = "#009bde";
	const P = await loadPlugin(path);
	const c = checks ? check : () => {};
	P.Initialize();
	c(JSON.stringify(dev.cmds()) === JSON.stringify([0x28, 0x21, 0x22, 0x23, 0x27, 0x25]),
		`Initialize: queries 28 21 22 23 27, then 25 (got ${dev.cmds().map(x => x.toString(16)).join(" ")})`);
	c(dev.notes.length === 0, "no notification");
	c(dev.inbox.length === 0, "every reply consumed (single read per query)");
	const reads = dev.events.filter(e => e.op === "read" && e.timeout > 0).length;
	c(reads === 5, `one waiting read per query, no extra VIA read (got ${reads})`);

	let n = dev.writes().length;
	P.Render();
	if (checks) checkStream(dev.writes().slice(n), (i, p) => dev.colorFn(p[0], p[1]), "Canvas frame", P);
	dev.mark("frame1");
	const frame1 = framesFrom(dev.writes().slice(n), P);

	n = dev.writes().length;
	globalThis.LightingMode = "Forced";
	globalThis.forcedColor = "#112233";
	P.Render();
	if (checks) checkStream(dev.writes().slice(n), () => [0x11, 0x22, 0x33], "Forced colour frame", P);
	dev.mark("frame2");
	globalThis.LightingMode = "Canvas";

	n = dev.writes().length;
	P.Shutdown(true);
	c(JSON.stringify(dev.cmds().slice(n)) === "[38]", "Shutdown(true) sends only 0x26");

	n = dev.writes().length;
	P.Render();
	const after = dev.cmds().slice(n);
	c(after[0] === 0x25 && after.slice(1).every(x => x === 0x24) && after.length === 11,
		"Render after Shutdown(true): 0x25 again, then 10 stream packets");
	dev.mark("frame3");

	n = dev.writes().length;
	P.Shutdown(false);
	c(JSON.stringify(dev.cmds().slice(n)) === "[38]", "Shutdown(false) sends only 0x26");
	c(dev.writes().every(w => w.data[0] === 0 && w.data[1] >= 0x21 && w.data[1] <= 0x28 && w.length === 33),
		"every write: report ID 0, command 0x21-0x28, length 33");
	return { dev, record: { events: dev.events, frame1, frame2: new Array(86).fill([0x11, 0x22, 0x33]) } };
}

async function usbStock(path, checks) {
	const dev = makeDevice("stock");
	globalThis.device = dev;
	const P = await loadPlugin(path);
	const c = checks ? check : () => {};
	P.Initialize();
	c(dev.notes.length === 1 && dev.notes[0][2] === 1, "one important notification");
	for (let k = 0; k < 8; k++) { fakeNow += 5000; P.Render(); }
	P.Shutdown(false);
	const cmds = dev.cmds();
	c(cmds.every(x => [0x21, 0x22, 0x23, 0x27, 0x28].includes(x)), "only read-only queries, never 0x24/0x25/0x26");
	c(cmds.length === 5 * 6, `initial handshake + 5 retries, then stops (got ${cmds.length / 5} handshakes)`);
	c(dev.notes.length === 1, "notification not repeated");
	return { dev };
}

async function usbSilent(path, checks) {
	const dev = makeDevice("silent");
	globalThis.device = dev;
	const P = await loadPlugin(path);
	const c = checks ? check : () => {};
	P.Initialize();
	P.Render();
	c(!dev.cmds().includes(0x24) && !dev.cmds().includes(0x25), "no streaming while unanswered");
	dev.firmware = "patched";
	fakeNow += 4999;
	P.Render();
	c(!dev.cmds().includes(0x25), "no retry before 5 s");
	fakeNow += 1;
	const n = dev.writes().length;
	P.Render();
	const x = dev.cmds().slice(n);
	c(x.slice(0, 6).join() === [0x28, 0x21, 0x22, 0x23, 0x27, 0x25].join(), "retry after 5 s: handshake, then 0x25");
	c(dev.denotes.length === 1, "notification removed after recovery");
	const n2 = dev.writes().length;
	P.Render();
	c(dev.cmds().slice(n2).length === 10 && dev.cmds().slice(n2).every(y => y === 0x24), "then streams normally");
	return { dev };
}

console.log("== USB: patched firmware ==");
const t0 = fakeNow;
const { record } = await usbPatched(pluginPath, true);
console.log("== USB: stock firmware (0xFF replies) ==");
await usbStock(pluginPath, true);
console.log("== USB: silent at first, then the patch answers ==");
await usbSilent(pluginPath, true);
const t1 = fakeNow;

console.log("== USB: identical to v1.0.1 ==");
if (!existsSync(baselinePath)) {
	check(false, `baseline ${baselinePath} missing`);
} else {
	const trace = async (path) => {
		fakeNow = t0;
		const out = [];
		for (const f of [usbPatched, usbStock, usbSilent]) {
			const { dev } = await f(path, false);
			out.push({ events: dev.events, notes: dev.notes, denotes: dev.denotes });
		}
		return JSON.stringify(out);
	};
	const mine = await trace(pluginPath), base = await trace(baselinePath);
	const nw = JSON.parse(mine).reduce((s, x) => s + x.events.filter(e => e.op === "write").length, 0);
	check(mine === base, `all three scenarios: every write, read (timeout included) and notification identical to v1.0.1 (${nw} writes)`);
	fakeNow = t1;
}

// ---- 2.4G receiver ----------------------------------------------------------------------

// SignalRGB's device.read does not return a plain Array (Array.isArray was false on hardware,
// 2026-10-21): the 2.4G mock returns an object with indexes and length only.
function arrayLike(bytes) {
	const o = { length: bytes.length };
	bytes.forEach((b, i) => { o[i] = b; });
	return o;
}

const QUERY = { 0x21: [0x21, 0, 0, 0], 0x22: [0x22, 1, 0, 6], 0x23: [0x23, 0, 0, 0], 0x27: [0x27, 86, 0, 0], 0x28: [0x28, 2, 0, 0] };
const pad32 = (a) => { const p = a.slice(0, 32); while (p.length < 32) p.push(0); return p; };

// Mock receiver + keyboard with patch v2: replies arrive rtt ms after the write.
function makeRadio(scene, opts = {}) {
	const dev = {
		mode: 2, enabled: false, quiet: false, asleep: false, rtt: 45, lose: opts.lose ?? null,
		collideMs: 50, lastReplyAt: -Infinity,
		events: [], pending: [], logs: [], notes: [], denotes: [], lastRead: 0,
		frame: new Array(86).fill(null), scene, posIndex: null,
		write(data, length) {
			const p = pad32([...data].slice(1));
			const ev = { op: "write", t: fakeNow, data: [...data], length, mock_reply: null };
			this.events.push(ev);
			if (this.asleep || (this.lose && this.lose(p))) { ev.lost = true; return length; }
			let r = null;
			if (fakeNow < this.lastReplyAt + this.collideMs) {
				ev.collided = true;                     // damaged on the busy link: VIA gets it
				r = [0xFF].concat(p.slice(1));
			} else if (this.mode !== 2 || p[0] < 0x21 || p[0] > 0x28) {
				r = [0xFF].concat(p.slice(1));          // VIA: unhandled
			} else if (p[0] in QUERY) {
				r = pad32(QUERY[p[0]]);
			} else if (p[0] === 0x24) {
				const [, start, count] = p;
				if (start + count > 86) r = pad32([0x24, 0xFD]);
				else if (count > 9) r = pad32([0x24, 0xFE]);
				else {
					if (this.enabled) for (let i = 0; i < count; i++) this.frame[start + i] = p.slice(3 + 3 * i, 6 + 3 * i);
					if (!this.quiet) r = p;
				}
			} else if (p[0] === 0x25) {
				this.enabled = true; this.quiet = (p[1] & 1) === 1;
				if (!this.quiet) r = p;
			} else if (p[0] === 0x26) {
				this.enabled = false;
				if (!this.quiet) r = p;
			}
			if (r !== null) {
				ev.mock_reply = [0].concat(r);
				this.pending.push({ at: fakeNow + this.rtt, data: [0].concat(r) });
				this.lastReplyAt = fakeNow + this.rtt;
			}
			return length;
		},
		read(data, length, timeout) {
			this.events.push({ op: "read", t: fakeNow, timeout });
			if (this.pending.length && this.pending[0].at <= fakeNow + timeout) {
				fakeNow = Math.max(fakeNow, this.pending[0].at);
				const r = this.pending.shift().data;
				this.lastRead = r.length;
				return arrayLike(r);
			}
			fakeNow += timeout;
			this.lastRead = 0;
			return arrayLike([]);
		},
		getLastReadSize() { return this.lastRead; },
		pause(ms) { this.events.push({ op: "pause", t: fakeNow, ms }); fakeNow += ms; },
		log(x) { this.logs.push(String(x)); },
		notify(t, d, p) { this.notes.push([t, d, p]); return `alert${this.notes.length}`; },
		denotify(id) { this.denotes.push(id); },
		color(x, y) { return this.scene(this.posIndex.get(`${x},${y}`), fakeNow); },
		writes() { return this.events.filter(e => e.op === "write"); },
		cmds() { return this.writes().map(e => e.data[1]); },
		mark(name, frame) { this.events.push({ op: "mark", name, frame }); },
		sceneFrame() { return Array.from({ length: 86 }, (_, i) => this.scene(i, fakeNow)); },
		shows(frame) { return this.frame.every((px, i) => px !== null && px.join() === frame[i].join()); },
	};
	return dev;
}

async function radioSetup(scene, opts) {
	const dev = makeRadio(scene, opts);
	globalThis.device = dev;
	globalThis.LightingMode = "Canvas";
	globalThis.forcedColor = "#009bde";
	const P = await loadPlugin(rxPluginPath);
	dev.posIndex = new Map(P.LedPositions().map((p, i) => [p.join(), i]));
	return { dev, P };
}

// SignalRGB calls Render() about every 16 ms; a Render that blocked (handshake) delays the next.
function runFor(P, dev, ms, onTick = null) {
	const end = fakeNow + ms;
	let tick = fakeNow;
	while (fakeNow < end) {
		if (onTick) onTick(fakeNow);
		const n = dev.writes().length;
		P.Render();
		const colour = dev.writes().slice(n).filter(e => e.data[1] === 0x24).length;
		dev.maxPerRender = Math.max(dev.maxPerRender ?? 0, colour);
		tick += 16;
		fakeNow = Math.max(fakeNow, tick);
		tick = fakeNow;
	}
}

// Start-to-start spacing between consecutive writes, the one after a 0x25/0x26 separately.
function spacing(writes) {
	let min = Infinity, minCmd = Infinity;
	for (let k = 1; k < writes.length; k++) {
		const d = writes[k].t - writes[k - 1].t;
		if ([0x25, 0x26].includes(writes[k - 1].data[1])) minCmd = Math.min(minCmd, d);
		else min = Math.min(min, d);
	}
	return { min, minCmd };
}

function commonRadioChecks(dev, label) {
	const w = dev.writes();
	check(w.every(e => e.data[0] === 0 && e.length === 33 && e.data.length <= 33 && [0x21, 0x22, 0x24, 0x25, 0x26, 0x27, 0x28].includes(e.data[1])),
		`${label}: every write report ID 0, length 33, command 21 22 24 25 26 27 28 (never 0x23)`);
	check(w.every(e => !isVendorFrame(pad32(e.data.slice(1)))), `${label}: no packet is a valid vendor CRC frame`);
	check(w.filter(e => e.data[1] === 0x25).every(e => e.data[2] === 1), `${label}: every 0x25 is 25 01 (quiet)`);
	const s = spacing(w);
	check(s.min >= 80 && s.minCmd >= 100, `${label}: >= 80 ms between packets (min ${s.min}), >= 100 after 0x25/0x26 (min ${s.minCmd})`);
	const q = w.map((e, k) => [e, w[k + 1]]).filter(([e, n]) => n && QUERY[e.data[1]] && !e.lost);
	check(q.every(([e, n]) => n.t - e.t >= 150), `${label}: >= 150 ms after every query (min ${Math.min(...q.map(([e, n]) => n.t - e.t))})`);
	check(w.every(e => !e.collided), `${label}: no packet sent while a reply was on its way or < 50 ms after it (${w.filter(e => e.collided).length})`);
	check((dev.maxPerRender ?? 0) <= 1, `${label}: at most one colour packet per Render() (max ${dev.maxPerRender ?? 0})`);
}

const sceneA = (i) => [(i * 37) & 0xFF, (i * 11 + 40) & 0xFF, (200 - i) & 0xFF];
let rxRecord;

console.log("== 2.4G: handshake, stream, refresh, change, shutdown, resume ==");
{
	let dot = -1;                                           // one LED turned white later
	const scene = (i) => (i === dot ? [255, 255, 255] : sceneA(i));
	const { dev, P } = await radioSetup(scene);
	P.Initialize();
	const hs = dev.cmds();
	check(JSON.stringify(hs) === JSON.stringify([0x22, 0x28, 0x21, 0x27, 0x25]),
		`Initialize: 22 28 21 27, then 25 01; no 0x23 (got ${hexs(hs)})`);
	const firstRead = dev.events.findIndex(e => e.op === "read");
	const firstWrite = dev.events.findIndex(e => e.op === "write");
	check(firstRead >= 0 && firstRead < firstWrite && dev.events[firstRead].timeout === 0, "stale reports flushed before the first query");
	const qreads = dev.events.filter(e => e.op === "read" && e.timeout > 0);
	check(qreads.length === 4 && qreads.every(e => e.timeout <= 250), `one waiting read (<= 250 ms) per query (${qreads.map(e => e.timeout).join(", ")})`);
	check(dev.notes.length === 0 && dev.enabled && dev.quiet, "no notification; the keyboard is in SignalRGB mode with quiet on");
	dev.mark("hs_end");

	let n = dev.writes().length;
	const tStart = fakeNow;
	runFor(P, dev, 1200);
	const fill = dev.writes().slice(n).filter(e => e.data[1] === 0x24);
	check(fill.slice(0, 10).map(e => e.data[2]).join() === "0,9,18,27,36,45,54,63,72,81", "first fill: the 10 groups in order");
	check(dev.shows(dev.sceneFrame()), `the keyboard shows the scene after ${fakeNow - tStart} ms`);
	dev.mark("scene1", dev.sceneFrame());

	n = dev.writes().length;
	runFor(P, dev, 6000);                                   // still scene: refresh only
	const still = dev.writes().slice(n);
	const perGroup = new Map();
	for (const e of still) { const g = e.data[2] / 9; (perGroup.get(g) ?? perGroup.set(g, []).get(g)).push(e.t); }
	const gaps = [...perGroup.values()].flatMap(ts => ts.slice(1).map((t, k) => t - ts[k]));
	check(still.every(e => e.data[1] === 0x24) && perGroup.size === 10, "still scene: only colour packets, every group refreshed");
	check(Math.min(...gaps) >= 1500 && Math.max(...gaps) <= 3000,
		`still scene: each group re-sent every 1.5-3 s (min ${Math.min(...gaps)}, max ${Math.max(...gaps)} ms); ${(still.length / 6).toFixed(1)} packets/s`);

	n = dev.writes().length;
	dot = 40;                                               // LED 40 turns white
	const tChange = fakeNow;
	runFor(P, dev, 400);
	const sentDot = dev.writes().slice(n).find(e => e.data[1] === 0x24 && e.data[2] === 36);
	check(sentDot !== undefined && sentDot.t - tChange <= 100, `a change goes out at the next free slot (${sentDot ? sentDot.t - tChange : "never"} ms)`);
	check(dev.shows(dev.sceneFrame()), "the keyboard shows the changed scene");
	dev.mark("scene2", dev.sceneFrame());

	n = dev.writes().length;
	P.Shutdown(true);
	check(JSON.stringify(dev.cmds().slice(n)) === "[38]" && !dev.enabled, "Shutdown(true): one 0x26, SignalRGB mode off");
	n = dev.writes().length;
	runFor(P, dev, 1200);
	const resumed = dev.cmds().slice(n);
	check(resumed[0] === 0x25 && resumed.slice(1).every(x => x === 0x24) && resumed.length >= 11 && dev.enabled,
		`Render after Shutdown(true): 25 01 once, then colour packets (${resumed.length - 1})`);
	check(dev.shows(dev.sceneFrame()), "after resume the keyboard shows the scene again");
	n = dev.writes().length;
	P.Shutdown(false);
	check(JSON.stringify(dev.cmds().slice(n)) === "[38]", "Shutdown(false): one 0x26");
	check(dev.cmds().filter(x => x === 0x25).length === 2, "0x25 only at the start and after the resume (no periodic 0x25)");
	check(dev.pending.length === 0 && dev.writes().filter(e => e.data[1] === 0x24).every(e => e.mock_reply === null),
		"quiet on: no reply to any colour packet");
	commonRadioChecks(dev, "stream");
	rxRecord = { transport: "radio", events: dev.events };
}

console.log("== 2.4G: 25 % of the colour packets lost ==");
{
	let seed = 12345;
	const rand = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
	const { dev, P } = await radioSetup(sceneA, { lose: (p) => p[0] === 0x24 && rand() < 0.25 });
	P.Initialize();
	runFor(P, dev, 9000);
	const lost = dev.writes().filter(e => e.lost).length;
	check(lost > 10, `${lost} colour packets lost`);
	check(dev.shows(dev.sceneFrame()), "the refresh has repaired every lost group");
	commonRadioChecks(dev, "loss");
}

console.log("== 2.4G: a group whose bytes 6-7 form a valid vendor CRC ==");
{
	// LED 1 R,G = the CRC of group 0's packet (which does not depend on bytes 6-7).
	const base = (i) => [(i * 5 + 1) & 0xFF, (i * 3 + 2) & 0xFF, (i + 3) & 0xFF];
	const g0 = [0x24, 0, 9];
	for (let i = 0; i < 9; i++) g0.push(...base(i));
	const crc = vendorCrc(pad32(g0));
	const scene = (i) => (i === 1 ? [crc & 0xFF, crc >> 8, base(1)[2]] : base(i));
	const want = [0x24, 0, 9];
	for (let i = 0; i < 9; i++) want.push(...scene(i));
	check(isVendorFrame(pad32(want)), "the scene's group 0 packet is a valid vendor CRC frame (test setup)");
	const { dev, P } = await radioSetup(scene);
	P.Initialize();
	runFor(P, dev, 8000);
	const g0w = dev.writes().filter(e => e.data[1] === 0x24 && e.data[2] === 0);
	const g1w = dev.writes().filter(e => e.data[1] === 0x24 && e.data[2] === 9);
	check(g0w.length > 0 && g0w.every(e => e.data[4] === (want[3] ^ 1) && e.data.slice(5).join() === pad32(want).slice(4).join()),
		`sent with byte 3 (LED 0 red) ${want[3]} -> ${want[3] ^ 1}, all other bytes unchanged`);
	check(g0w.length <= g1w.length + 1, `not resent more often than the others (group 0: ${g0w.length}, group 1: ${g1w.length})`);
	check(dev.logs.some(l => l.includes("nudged")), "logged");
	commonRadioChecks(dev, "crc");
}

console.log("== 2.4G: the keyboard leaves 2.4G mode, then Fn+V ==");
{
	const { dev, P } = await radioSetup(sceneA);
	P.Initialize();
	runFor(P, dev, 1500);
	dev.mode = 3;                                           // cable plugged in and out: USB mode
	const tLost = fakeNow;
	let n = dev.writes().length;
	runFor(P, dev, 1000);
	const after = dev.writes().slice(n);
	check(after.filter(e => e.data[1] === 0x24).length <= 2, `streaming stops after the first 0xFF reply (${after.length} packet(s) sent)`);
	check(dev.notes.length === 1 && /Fn\+V/.test(dev.notes[0][1]), "one notification telling to press Fn+V");
	n = dev.writes().length;
	runFor(P, dev, 5 * 5000 + 3 * 10000 - 1000);           // five 5 s retries, then 10 s ones
	const retries = dev.writes().slice(n);
	check(retries.every(e => e.data[1] === 0x22), `retries send only 0x22 (${retries.length}: ${hexs(retries.map(e => e.data[1]))})`);
	const rt = retries.map(e => e.t - tLost);
	const dts = rt.slice(1).map((t, k) => t - rt[k]);
	check(retries.length === 7 && dts.slice(0, 4).every(d => d >= 5000 && d < 5400) && dts.slice(4).every(d => d >= 10000 && d < 10400),
		`retry times: every 5 s five times, then every 10 s (${rt.map(t => (t / 1000).toFixed(1)).join(", ")} s)`);
	check(dev.logs.filter(l => l.includes("Reply to 0x22")).length === 1, "the repeated VIA reply to 0x22 is logged once");
	check(dev.notes.length === 1, "notification not repeated");
	check(dev.logs.filter(l => l.includes("Handshake failed")).length === 1, "the repeated failure is logged once");
	dev.mode = 2;                                           // Fn+V
	n = dev.writes().length;
	runFor(P, dev, 32000);
	const back = dev.cmds().slice(n);
	check(back.slice(0, 5).join() === [0x22, 0x28, 0x21, 0x27, 0x25].join() && back.slice(5).every(x => x === 0x24) && back.length > 15,
		`after Fn+V the next retry passes: 22 28 21 27, 25 01, then streaming (${back.length} packets)`);
	check(dev.denotes.length === 1 && dev.shows(dev.sceneFrame()), "notification removed; the keyboard shows the scene");
	commonRadioChecks(dev, "fn+v");
}

console.log("== 2.4G: the keyboard's radio state is reset mid-stream ==");
{
	const { dev, P } = await radioSetup(sceneA);
	P.Initialize();
	runFor(P, dev, 2000);
	dev.enabled = false; dev.quiet = false;                 // power-on defaults
	const n = dev.writes().length;
	runFor(P, dev, 4000);
	const after = dev.writes().slice(n);
	const k25 = after.findIndex(e => e.data[1] === 0x25);
	check(after.filter(e => e.data[1] === 0x25).length === 1 && k25 >= 1 && k25 <= 3,
		`one 25 01 right after the first echo (packet ${k25 + 1} after the reset)`);
	check(after.slice(k25 + 1).every(e => e.mock_reply === null) && dev.enabled && dev.quiet, "then no more echoes; mode and quiet back on");
	check(dev.shows(dev.sceneFrame()), "the refresh restores the board");
	commonRadioChecks(dev, "heal");
}

console.log("== 2.4G: no reply at all (keyboard off, asleep or not paired) ==");
{
	const { dev, P } = await radioSetup(sceneA);
	dev.asleep = true;
	P.Initialize();
	runFor(P, dev, 60000);
	const c = dev.cmds();
	check(c.every(x => x === 0x22) && c.length === 9, `only 0x22, one per retry (${c.length} in 60 s)`);
	check(dev.notes.length === 1 && dev.logs.filter(l => l.includes("Handshake failed")).length === 1, "one notification, one log line");
	P.Shutdown(false);
	check(dev.cmds().length === c.length, "Shutdown sends nothing (never streamed)");
}

Date.now = realNow;
mkdirSync(dirname(packetsOut), { recursive: true });
writeFileSync(packetsOut, JSON.stringify(record));
writeFileSync(rxPacketsOut, JSON.stringify(rxRecord));
console.log(`\npatched-scenario packets written to ${packetsOut} and ${rxPacketsOut}`);
console.log(`plugintest: ${fails === 0 ? "PASS" : `FAIL (${fails})`}`);
process.exit(fails ? 1 : 0);
