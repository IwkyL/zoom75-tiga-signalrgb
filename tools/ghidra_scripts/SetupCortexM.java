// Pre-script for a raw Cortex-M flash image loaded at 0x08000000:
// adds uninitialized RAM / peripheral / system blocks (AT32F415 memory map) and
// creates functions at every vector table handler and at the HEX start address.
//
// args (optional): <ram_size_hex> <vector_count> <entry_hex> [<rw_image.bin> <rw_exec_addr_hex>]
// With an RW image, SRAM starts with those initialized bytes (the decompressed scatter-load
// region) and every Thumb code pointer found in it becomes a function start.
// @category Firmware

import java.io.FileInputStream;
import java.nio.file.Files;
import java.nio.file.Paths;

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;

public class SetupCortexM extends GhidraScript {

    private void block(String name, long start, long size, boolean write, boolean vol) throws Exception {
        Memory mem = currentProgram.getMemory();
        Address a = toAddr(start);
        if (mem.getBlock(a) != null) {
            return;
        }
        MemoryBlock b = mem.createUninitializedBlock(name, a, size, false);
        b.setRead(true);
        b.setWrite(write);
        b.setExecute(false);
        b.setVolatile(vol);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        long ramSize = args.length > 0 ? Long.decode(args[0]) : 0x8000;
        int vectors = args.length > 1 ? Integer.decode(args[1]) : 16 + 80;
        long entry = args.length > 2 ? Long.decode(args[2]) : -1;
        String rwPath = args.length > 4 ? args[3] : null;
        long rwExec = args.length > 4 ? Long.decode(args[4]) : 0;
        String seeds = args.length > 5 ? args[5] : "";

        long flashEnd = 0x08000000L + currentProgram.getMemory().getBlock(toAddr(0x08000000L)).getSize();
        java.util.List<Long> rwCodePtrs = new java.util.ArrayList<>();
        if (rwPath != null) {
            byte[] rw = Files.readAllBytes(Paths.get(rwPath));
            Memory mem = currentProgram.getMemory();
            MemoryBlock b = mem.createInitializedBlock("SRAM_RW_INIT", toAddr(rwExec),
                new FileInputStream(rwPath), rw.length, monitor, false);
            b.setRead(true);
            b.setWrite(true);
            b.setExecute(false);
            long zi = rwExec + rw.length;
            block("SRAM", zi, 0x20000000L + ramSize - zi, true, false);
            for (int i = 0; i + 4 <= rw.length; i += 4) {
                long v = (rw[i] & 0xffL) | (rw[i + 1] & 0xffL) << 8 | (rw[i + 2] & 0xffL) << 16 | (rw[i + 3] & 0xffL) << 24;
                if ((v & 1) == 1 && v > 0x08000170L && v < flashEnd) {
                    rwCodePtrs.add(v & ~1L);
                }
            }
        }
        setAnalysisOption(currentProgram, "Aggressive Instruction Finder", "true");

        block("SRAM", 0x20000000L, ramSize, true, false);
        block("PERIPH_APB_AHB", 0x40000000L, 0x30000, true, true);
        block("OTGFS", 0x50000000L, 0x40000, true, true);
        block("SYSTEM_ROM", 0x1FFF0000L, 0x10000, false, false);
        block("CORTEX_PPB", 0xE0000000L, 0x100000, true, true);

        Address vt = toAddr(0x08000000L);
        createLabel(vt, "vector_table", true, SourceType.USER_DEFINED);
        String[] names = {"initial_sp", "Reset_Handler", "NMI_Handler", "HardFault_Handler",
            "MemManage_Handler", "BusFault_Handler", "UsageFault_Handler", null, null, null, null,
            "SVC_Handler", "DebugMon_Handler", null, "PendSV_Handler", "SysTick_Handler"};
        for (int i = 0; i < vectors; i++) {
            Address slot = vt.add(i * 4L);
            long v = getInt(slot) & 0xffffffffL;
            createDWord(slot);
            if (i == 0 || v == 0 || (v & 1) == 0) {
                continue;
            }
            if (v < 0x08000000L || v >= 0x08000000L + currentProgram.getMemory().getBlock(vt).getSize()) {
                break; // ran past the table
            }
            Address target = toAddr(v & ~1L);
            String n = (i < names.length && names[i] != null) ? names[i] : String.format("IRQ%d_Handler", i - 16);
            if (getFunctionAt(target) == null) {
                disassemble(target);
                createFunction(target, n);
            } else {
                createLabel(target, n, false, SourceType.USER_DEFINED);
            }
        }
        if (entry > 0) {
            Address e = toAddr(entry & ~1L);
            disassemble(e);
            if (getFunctionAt(e) == null) {
                createFunction(e, "hex_entry");
            }
            addEntryPoint(e);
        }
        for (String s : seeds.split("[+,]")) { // '+' because cmd.exe splits .bat arguments on ','
            if (!s.isBlank()) {
                rwCodePtrs.add(Long.decode(s.trim()) & ~1L);
            }
        }
        for (long p : rwCodePtrs) {
            Address a = toAddr(p);
            if (getFunctionAt(a) == null && getInstructionAt(a) == null && getDataAt(a) == null) {
                disassemble(a);
                createFunction(a, null);
            }
        }
        println("SetupCortexM done, RW code pointers seeded: " + rwCodePtrs.size());
    }
}
