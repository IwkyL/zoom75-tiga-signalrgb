#!/usr/bin/env bash
# Full offline suite: v1 (V2_SESSION_PROMPT step 5b) + v2 + the pace, soak, partial and idle tests
# + both SignalRGB plugins (generated files, mock, replay, closed loop on 2.4G). No hardware.
# usage: bash tools/run_offline_suite.sh <output dir outside the project, e.g. a scratch folder>
# Runs every check sequentially (real-time hostsim runs must not overlap), about 12 minutes.
cd "/c/Projects/Custom Firmware" || exit 1
OUT="$1"
mkdir -p "$OUT"
PY=".venv/Scripts/python.exe -I"
FROZEN="original/WQ206B1UBTG2_V10_3424_KB81_RGB_LCD_SV3.0.0_20250412.hex out/patched.hex out/build/srgb.bin patch/srgb.c patch/fw.h patch/srgb.ld build_patch.py tools/selfcheck.py tools/emutest.py out_v2/patched_v2.hex patch_v2/srgb_radio.c patch_v2/fw_radio.h patch_v2/srgb_radio.ld build_patch_v2.py tools/selfcheck_v2.py tools/emutest_v2.py"
sha256sum $FROZEN > "$OUT/frozen_before.txt"
run() {
    local name="$1"; shift
    local t0=$(date +%s)
    "$@" > "$OUT/$name.txt" 2>&1
    local rc=$?
    local t1=$(date +%s)
    local last=$(grep -v '^\s*$' "$OUT/$name.txt" | tail -1)
    printf '%-28s exit %d  %4ds  %s\n' "$name" "$rc" "$((t1 - t0))" "$last" >> "$OUT/summary.txt"
}
: > "$OUT/summary.txt"
run v1_selfcheck        $PY tools/selfcheck.py
run v1_emutest          $PY tools/emutest.py
run v1_hidtest_readonly $PY tools/hostsim.py hidtest
run v1_hidtest_led      $PY tools/hostsim.py hidtest --led --walk-delay 0.3
run v1_stock_led        $PY tools/hostsim.py stock --led
run v1_radio            $PY tools/hostsim.py radio
run plugin_generated    $PY tools/gen_plugins.py --check
run v1_plugintest_node  node tools/plugintest.mjs
run v1_plugin_replay    $PY tools/hostsim.py plugin logs/plugin_packets.json
run v2_plugin_replay    $PY tools/hostsim.py plugin logs/plugin_packets_rx.json
run v2_selfcheck_neg    $PY tools/selfcheck_v2.py --negative
run v2_emutest          $PY tools/emutest_v2.py
run v2_radio2           $PY tools/hostsim.py radio2
run v2_hidtest2_led     $PY tools/hostsim.py hidtest2 --led --walk-delay 0.3
run v2_hidtest2_ro      $PY tools/hostsim.py hidtest2
run v2_pace             $PY tools/hostsim.py pace
run v2_soak             $PY tools/hostsim.py soak
run v2_partial          $PY tools/hostsim.py partial
run v2_idle             $PY tools/hostsim.py idle
run v2_plugin2g         $PY tools/hostsim.py plugin2g
sha256sum $FROZEN > "$OUT/frozen_after.txt"
if cmp -s "$OUT/frozen_before.txt" "$OUT/frozen_after.txt"; then
    echo "frozen files unchanged during the suite" >> "$OUT/summary.txt"
else
    echo "FROZEN FILES CHANGED" >> "$OUT/summary.txt"
fi
cat "$OUT/summary.txt"
