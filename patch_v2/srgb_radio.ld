/* Link the patch v2 radio code right after the patch v1 code in the free tail
 * of flash sector 53. 0x0801AA08 is the first byte after v1 (0x0801A828 + 480);
 * 0x0801B000 is the end of sector 53 and the hard limit (FEASIBILITY.md
 * condition 1). */

MEMORY
{
    PATCH2 (rx) : ORIGIN = 0x0801AA08, LENGTH = 0x0801B000 - 0x0801AA08
}

ENTRY(srgb_rx_radio)
EXTERN(srgb_radio_send)
EXTERN(srgb_render2)

SECTIONS
{
    .text :
    {
        KEEP(*(.text.srgb_rx_radio))
        KEEP(*(.text.srgb_radio_send))
        KEEP(*(.text.srgb_render2))
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > PATCH2

    /* The patch owns no linker-placed RAM; its state is at a fixed address. */
    .data : { *(.data .data.*) } > PATCH2
    .bss  : { *(.bss .bss.* COMMON) } > PATCH2

    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}

ASSERT(ADDR(.text) == 0x0801AA08, "radio code must start right after patch v1")
ASSERT(SIZEOF(.data) == 0, "patch must not use initialised RAM")
ASSERT(SIZEOF(.bss) == 0, "patch must not use zero-initialised RAM")
ASSERT(ADDR(.text) + SIZEOF(.text) <= 0x0801B000, "patch overflows sector 53")
