// Runs a SignalRGB plugin in real time against tools/hostsim.py (command plugin2g): every
// device.write / read / log / notify goes to the Python side as one JSON line on stdout,
// and the answer comes back as one line on stdin (synchronous, like SignalRGB's API).
// Python runs the keyboard: the real patch v2 bytes behind the fake 2.4G receiver.
//
// usage (started by hostsim): node tools/plugin_bridge.mjs <plugin.js> <scenario>
//   full   still scene, colour wipes, the keyboard leaving 2.4G mode and Fn+V, a reset
//          of the keyboard's radio state, Shutdown
//   still  a still scene for 8 s, then Shutdown (for the lossy link models)
import { readSync, writeSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [pluginPath, scenario] = process.argv.slice(2);
const buf = Buffer.alloc(1 << 16);
let acc = "";

function rpc(obj) {
	writeSync(1, JSON.stringify(obj) + "\n");
	while (!acc.includes("\n")) {
		const n = readSync(0, buf, 0, buf.length, null);
		if (n === 0) throw new Error("hostsim closed the pipe");
		acc += buf.toString("utf8", 0, n);
	}
	const i = acc.indexOf("\n");
	const line = acc.slice(0, i);
	acc = acc.slice(i + 1);
	return JSON.parse(line);
}

const gate = new Int32Array(new SharedArrayBuffer(4));
const sleep = (ms) => { if (ms > 0) Atomics.wait(gate, 0, 0, ms); };

let scene = () => [0, 0, 0];
let posIndex = null;
let lastRead = 0;
globalThis.device = {
	write(data, length) { return rpc({ op: "write", data: [...data], length }).n; },
	read(data, length, timeout) {
		const r = rpc({ op: "read", timeout }).data;
		lastRead = r.length;
		const o = { length: r.length };     // like SignalRGB: not a plain Array (indexes only)
		r.forEach((v, i) => { o[i] = v; });
		return o;
	},
	getLastReadSize() { return lastRead; },
	pause(ms) { sleep(ms); },
	log(msg) { rpc({ op: "log", msg: String(msg) }); },
	notify(title, text) { return rpc({ op: "notify", title, text }).id; },
	denotify(id) { rpc({ op: "denotify", id }); },
	color(x, y) { return scene(posIndex.get(`${x},${y}`)); },
};
globalThis.LightingMode = "Canvas";
globalThis.forcedColor = "#009bde";

const P = await import(pathToFileURL(pluginPath).href);
posIndex = new Map(P.LedPositions().map((p, i) => [p.join(), i]));
const frame = () => Array.from({ length: 86 }, (_, i) => scene(i));
const mark = (name) => rpc({ op: "mark", name, frame: frame() });
const kb = (action, value = null) => rpc({ op: "kb", action, value });

// SignalRGB calls Render() about every 16 ms.
function run(ms) {
	const end = Date.now() + ms;
	let next = Date.now();
	while (Date.now() < end) {
		P.Render();
		next += 16;
		if (Date.now() > next + 50) next = Date.now();     // a blocking Render (handshake)
		sleep(next - Date.now());
	}
}

const sceneA = (i) => [(i * 37) & 0xFF, (i * 11 + 40) & 0xFF, (200 - i) & 0xFF];
P.Initialize();
if (scenario === "full") {
	scene = sceneA;
	run(2500);
	mark("fill");
	scene = () => [255, 0, 0];
	run(2000);
	mark("wipe red");
	scene = () => [255, 255, 0];
	run(2000);
	mark("wipe yellow");
	scene = sceneA;
	run(1500);
	kb("mode", 3);                  // cable plugged in and out: the keyboard is in USB mode
	run(2500);
	mark("left 2.4G");
	kb("mode", 2);                  // Fn+V
	run(7000);                      // the next 5 s retry passes
	mark("back");
	kb("reset");                    // the keyboard's radio state is lost (power-on defaults)
	run(4000);
	mark("healed");
} else {
	scene = sceneA;
	run(8000);
	mark("still");
}
P.Shutdown(false);
mark("shutdown");
rpc({ op: "end" });
