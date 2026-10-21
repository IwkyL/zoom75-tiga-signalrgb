// Post-script: export the analysis to text files so it can be searched outside Ghidra.
//   functions.tsv  entry, size, name, #callers, #callees
//   decomp.c       decompiled C of every function
//   listing.asm    every code unit, with function headers and literal values
//   strings.tsv    defined strings and the addresses that reference them
//
// args: <output_dir>
// @category Firmware

import java.io.File;
import java.io.PrintWriter;

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;

public class ExportAll extends GhidraScript {

    private String hex(Address a) {
        return String.format("0x%08X", a.getOffset());
    }

    @Override
    public void run() throws Exception {
        File out = new File(getScriptArgs()[0]);
        out.mkdirs();
        Listing listing = currentProgram.getListing();
        Memory mem = currentProgram.getMemory();

        try (PrintWriter w = new PrintWriter(new File(out, "functions.tsv"), "UTF-8")) {
            w.println("entry\tsize\tname\tcallers\tcallees");
            FunctionIterator it = listing.getFunctions(true);
            while (it.hasNext()) {
                Function f = it.next();
                w.printf("%s\t%d\t%s\t%d\t%d%n", hex(f.getEntryPoint()), f.getBody().getNumAddresses(),
                    f.getName(), f.getCallingFunctions(monitor).size(), f.getCalledFunctions(monitor).size());
            }
        }

        try (PrintWriter w = new PrintWriter(new File(out, "strings.tsv"), "UTF-8")) {
            w.println("addr\tvalue\treferenced_from");
            DataIterator di = listing.getDefinedData(true);
            while (di.hasNext()) {
                Data d = di.next();
                if (!d.hasStringValue()) {
                    continue;
                }
                StringBuilder refs = new StringBuilder();
                for (Reference r : getReferencesTo(d.getAddress())) {
                    Function f = getFunctionContaining(r.getFromAddress());
                    refs.append(hex(r.getFromAddress()));
                    if (f != null) {
                        refs.append("(").append(f.getName()).append(")");
                    }
                    refs.append(" ");
                }
                String v = String.valueOf(d.getValue()).replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r");
                w.printf("%s\t%s\t%s%n", hex(d.getAddress()), v, refs.toString().trim());
            }
        }

        try (PrintWriter w = new PrintWriter(new File(out, "listing.asm"), "UTF-8")) {
            for (CodeUnit cu : listing.getCodeUnits(true)) {
                Address a = cu.getAddress();
                if (!a.getAddressSpace().isMemorySpace() || mem.getBlock(a) == null || !mem.getBlock(a).isInitialized()) {
                    continue;
                }
                Function f = getFunctionAt(a);
                if (f != null) {
                    w.printf("%n;===== FUNCTION %s %s =====%n", hex(a), f.getName());
                } else if (cu.getLabel() != null) {
                    w.printf("%s:%n", cu.getLabel());
                }
                StringBuilder sb = new StringBuilder();
                if (cu instanceof Instruction) {
                    for (Reference r : cu.getReferencesFrom()) {
                        Address to = r.getToAddress();
                        if (!to.isMemoryAddress()) {
                            continue;
                        }
                        sb.append(" ").append(r.getReferenceType().getName()).append("->").append(hex(to));
                        Data d = getDataAt(to);
                        if (r.getReferenceType().isData() && mem.getBlock(to) != null && mem.getBlock(to).isInitialized()) {
                            try {
                                sb.append(String.format("=0x%08X", mem.getInt(to) & 0xffffffffL));
                            } catch (Exception e) {
                                // ignore unreadable
                            }
                        }
                        if (d != null && d.hasStringValue()) {
                            sb.append(" \"").append(String.valueOf(d.getValue()).replace("\n", "\\n")).append("\"");
                        }
                        Function tf = getFunctionAt(to);
                        if (tf != null) {
                            sb.append(" <").append(tf.getName()).append(">");
                        }
                    }
                }
                byte[] b = cu.getBytes();
                StringBuilder hb = new StringBuilder();
                for (int i = 0; i < Math.min(b.length, 8); i++) {
                    hb.append(String.format("%02x", b[i]));
                }
                w.printf("%s  %-16s %s%s%n", hex(a), hb, cu.toString(), sb.length() > 0 ? "   ;" + sb : "");
            }
        }

        DecompInterface ifc = new DecompInterface();
        ifc.setOptions(new DecompileOptions());
        ifc.openProgram(currentProgram);
        try (PrintWriter w = new PrintWriter(new File(out, "decomp.c"), "UTF-8")) {
            FunctionIterator it = listing.getFunctions(true);
            while (it.hasNext() && !monitor.isCancelled()) {
                Function f = it.next();
                DecompileResults res = ifc.decompileFunction(f, 60, monitor);
                w.printf("%n// ===== %s %s =====%n", hex(f.getEntryPoint()), f.getName());
                if (res != null && res.decompileCompleted()) {
                    w.println(res.getDecompiledFunction().getC());
                } else {
                    w.println("// decompile failed: " + (res == null ? "null" : res.getErrorMessage()));
                }
            }
        }
        ifc.dispose();
        println("ExportAll done -> " + out.getAbsolutePath());
    }
}
