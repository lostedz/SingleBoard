# Memory-mapped UART

`rtl/uart_mmio.v` implements a full-duplex UART for the SingleBoard CPU bus.
Defaults are **50 MHz, 115200 baud, 8N1**, with no hardware flow control.
The rounded divider is 434 clocks/bit (about 115207 baud). `CLOCK_HZ` and
`BAUD` are synthesis parameters; the divider is not software-programmable.
Use parameter combinations giving 8 through 65535 clocks/bit.

There is a one-byte RX holding register and a one-byte TX holding register
in addition to the TX shift register. Software can queue the next TX byte
while one is transmitting. An RX overrun preserves the unread byte and drops
the new one. RX has a two-flop synchronizer, validates the start bit halfway
through it, samples data at bit centers, and checks the stop bit. A framing
error discards the frame; a continuously low line waits for a return to idle
before detecting another start.

## Register map

The default eight-byte region starts at **0xFF0000**. Change the aligned
`BASE_ADDR` parameter to relocate it; address bits [2:0] of that parameter
are ignored. Byte addresses within a register pair refer to the same register.

| Offset | Register | Read | Write |
| --- | --- | --- | --- |
| +0/+1 | DATA | RX byte, or zero if empty; consumes a valid byte | Queue a TX byte; waits if TX holding register is full |
| +2/+3 | STATUS | Status bits below | Write one to bits 3/4 to clear their sticky error flags |
| +4/+5 | CONTROL | Interrupt enables | Bit 0 RX-ready IRQ; bit 1 TX-space IRQ; bit 2 error IRQ |
| +6/+7 | DIVIDER | 16-bit clocks per serial bit (434 by default) | Ignored |

STATUS bits:

| Bit | Meaning |
| --- | --- |
| 0 | RX byte available |
| 1 | TX can accept a byte |
| 2 | TX completely idle, including its holding register |
| 3 | Sticky RX overrun |
| 4 | Sticky framing error |
| 5–6 | Zero |
| 7 | Interrupt output active |

DATA, STATUS and CONTROL reads duplicate the byte onto both CPU lanes. Thus
68000 byte accesses work at either address in the pair. For writes,
`bus_be[1]` selects `bus_wdata[15:8]`, and `bus_be[0]` selects
`bus_wdata[7:0]`; if both lanes are enabled, the low byte is used once.
Zero-lane transactions have no side effects. Read DIVIDER as a word.

## CPU and SDRAM integration

All bus side effects occur exactly on a rising edge with
`bus_valid && bus_clk_en && bus_ready`. Hold the request until that handshake.
The UART acknowledges only its own address range. Its ready/data outputs are
combinational: status and RX availability are sampled at the completion edge,
not frozen when the CPU pauses. TX and RX continue running independently of
`bus_clk_en`, which must match the CPU's `clk_en`.

Reserve the UART region in the board address decoder; **exclude it from
SDRAM writes**. A typical response mux is:

```verilog
wire uart_select = cpu_addr[23:3] == 21'h1fe000; // 0xFF0000–0xFF0007
// UART gets cpu_valid (it has its own address decoder).
// SDRAM gets cpu_valid && !uart_select && !boot_select.
// Boot ROM gets cpu_valid && boot_select && !uart_select.
assign cpu_ready = uart_select ? uart_ready :
                   boot_select ? boot_ready : sdram_ready;
assign cpu_rdata = uart_select ? uart_rdata :
                   boot_select ? boot_rdata : sdram_rdata;
```

`irq` is an active-high **level** output. With UART as the only interrupt
source, use `ipl_n = irq ? 3'b101 : 3'b111` for 68000 level 2, and install
the handler in autovector 26 at address 0x68. Combine multiple interrupt
sources in a board-level priority encoder. Interrupt enables reset to zero.
Reading DATA clears RX-ready; STATUS W1C clears errors. TX-space IRQ remains
asserted while space is available, so disable it when there is nothing left
to send. A new receive/error event wins over a simultaneous read/clear.

Example polled transmit using odd-address byte accesses:

```asm
wait_tx:
    btst    #1,$ff0003
    beq     wait_tx
    move.b  #'A',$ff0001
```

A write can also wait directly for space, but polling allows software to do
other work. `tests/uart_smoke.asm` is a complete CPU example that transmits
`OK`, receives `!` using a level-2 interrupt, and echoes it.

## FPGA pins and build

Connect `uart_rx` and `uart_tx` to the board's logic-level serial connector
or a compatible USB-to-UART adapter. Cross TX to RX and connect ground. These
are FPGA logic signals, not RS-232 voltage levels; use a transceiver for an
RS-232 connector. Assign LOC and I/O standard in the board UCF according to
the actual connector and bank voltage. RX should idle high. Keep the 50 MHz
clock running while using CPU clock enables.

The design uses ordinary Verilog-2005 registers, with no vendor IP. ISE may
use the `ASYNC_REG` attribute to recognize the RX synchronizer. Board timing
constraints should treat the external RX input as asynchronous, without
cutting the path between the two synchronizer registers.

```sh
python scripts/test_uart.py
python scripts/synth_spartan6.py --top uart_mmio --generate-only
python scripts/synth_spartan6.py --top uart_mmio
```

Tests use independent serial transmit/receive stimulus at the default baud
rate, including a slightly mismatched receive baud. They cover byte lanes,
full-duplex operation, TX backpressure, zero-lane accesses, a paused CPU read,
interrupts, overrun, framing errors, false starts, reset and address decode.
The second test executes the included 68000 firmware against the UART.
The firmware image is committed; rebuilding it requires vasm:

```sh
vasmm68k -m68000 -Fbin -o build/uart_smoke.bin tests/uart_smoke.asm
python scripts/rom_to_hex.py --name uart_smoke
```

ISE fit/timing and board-level validation have not been run here. Pin locations
remain board-specific; no connector pinout has been assumed.
