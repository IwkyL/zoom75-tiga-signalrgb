/* Link the SignalRGB patch into the free tail of flash sector 53.
 * 0x0801A828 is the first byte after the official v1.1 image; 0x0801B000 is
 * the end of sector 53 and the hard limit (FEASIBILITY.md condition 1). */

MEMORY
{
    PATCH (rx) : ORIGIN = 0x0801A828, LENGTH = 0x0801B000 - 0x0801A828
}

ENTRY(srgb_rx)
EXTERN(srgb_render)

SECTIONS
{
    .text :
    {
        KEEP(*(.text.srgb_rx))
        KEEP(*(.text.srgb_render))
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > PATCH

    /* The patch owns no linker-placed RAM; its state is at a fixed address. */
    .data : { *(.data .data.*) } > PATCH
    .bss  : { *(.bss .bss.* COMMON) } > PATCH

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}

ASSERT(SIZEOF(.data) == 0, "patch must not use initialised RAM")
ASSERT(SIZEOF(.bss) == 0, "patch must not use zero-initialised RAM")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x0801B000, "patch overflows sector 53")
