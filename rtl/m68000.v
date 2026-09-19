// SPDX-License-Identifier: ISC
// Synchronous 68000-compatible CPU with a 16-bit, big-endian memory bus.
// See README.md for the handshake contract and compatibility limitations.
`default_nettype none
module m68000 (
    input wire clk,
    input wire reset,
    input wire clk_en,
    input wire [2:0] ipl_n,
    output wire bus_valid,
    output wire bus_write,
    output wire [23:0] bus_addr,
    output wire [1:0] bus_be,
    output wire [15:0] bus_wdata,
    input wire [15:0] bus_rdata,
    input wire bus_ready,
    output wire [2:0] bus_fc,
    output wire [31:0] debug_pc,
    output wire [15:0] debug_sr
);
    wire rd, wr;
    wire [31:0] address;
    wire [1:0] byte_enable;

    // Preserve the pending request while paused. A transfer completes only
    // on a rising edge with clk_en && bus_valid && bus_ready.
    assign bus_valid = !reset && (rd || wr) && (|byte_enable);
    assign bus_write = wr;
    assign bus_addr = address[23:0];
    assign bus_be = byte_enable;

    cpu_j68 #(.USE_CLK_ENA(0)) cpu (
        .rst(reset), .clk(clk), .clk_ena(clk_en | reset),
        .rd_ena(rd), .wr_ena(wr),
        .data_ack(bus_valid && bus_ready && clk_en),
        .byte_ena(byte_enable), .address(address),
        .rd_data(bus_rdata), .wr_data(bus_wdata),
        .fc(bus_fc), .ipl_n(ipl_n),
        .dbg_pc_reg(debug_pc), .dbg_sr_reg(debug_sr),
        .dbg_reg_addr(), .dbg_reg_wren(), .dbg_reg_data(),
        .dbg_usp_reg(), .dbg_ssp_reg(), .dbg_vbr_reg(),
        .dbg_cycles(), .dbg_ifetch(), .dbg_irq_lvl()
    );
endmodule
`default_nettype wire
