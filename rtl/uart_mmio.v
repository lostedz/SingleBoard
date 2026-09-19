// SPDX-License-Identifier: ISC
// 68000 bus UART: 8N1, one RX holding byte, one TX holding byte plus shifter.
`default_nettype none
module uart_mmio #(
    parameter integer CLOCK_HZ = 50000000,
    parameter integer BAUD = 115200,
    parameter [23:0] BASE_ADDR = 24'hff0000
) (
    input wire clk, reset, bus_clk_en, bus_valid, bus_write,
    input wire [23:0] bus_addr,
    input wire [1:0] bus_be,
    input wire [15:0] bus_wdata,
    output reg [15:0] bus_rdata,
    output wire bus_ready,
    input wire uart_rx,
    output wire uart_tx, irq
);
    localparam integer DIV = (CLOCK_HZ + BAUD/2) / BAUD;
    localparam integer HALF = DIV/2;
    wire selected = bus_addr[23:3] == BASE_ADDR[23:3];
    wire [1:0] reg_sel = bus_addr[2:1];
    wire [7:0] write_byte = bus_be[0] ? bus_wdata[7:0] : bus_wdata[15:8];
    reg [2:0] irq_enable;
    reg [7:0] rx_data, tx_hold;
    reg rx_valid, tx_valid, overrun, framing;
    reg tx_busy;
    reg [9:0] tx_shift;
    reg [3:0] tx_left;
    reg [31:0] tx_timer;
    wire tx_pop = !tx_busy && tx_valid;
    wire tx_space = !tx_valid || tx_pop;
    wire data_write = bus_write && reg_sel == 0 && (|bus_be);
    assign bus_ready = !reset && selected && bus_valid && (!data_write || tx_space);
    wire fire = bus_ready && bus_clk_en;
    wire tx_push = fire && data_write;
    wire rx_pop = fire && !bus_write && reg_sel == 0 && (|bus_be);
    wire clear_errors = fire && bus_write && reg_sel == 1 && (|bus_be);
    assign uart_tx = tx_busy ? tx_shift[0] : 1'b1;
    assign irq = !reset && ((irq_enable[0] && rx_valid) ||
                 (irq_enable[1] && tx_space) || (irq_enable[2] && (overrun || framing)));
    wire [7:0] status = {irq,2'b00,framing,overrun,(!tx_busy && !tx_valid),tx_space,rx_valid};

    always @(*) begin
        bus_rdata = 0;
        if (selected) begin
            case (reg_sel)
                0: bus_rdata = rx_valid ? {rx_data,rx_data} : 16'b0;
                1: bus_rdata = {status,status};
                2: bus_rdata = {5'b0,irq_enable,5'b0,irq_enable};
                3: bus_rdata = DIV;
            endcase
        end
    end

    (* ASYNC_REG = "TRUE" *) reg rx_meta, rx_sync;
    always @(posedge clk) begin
        if (reset) begin rx_meta <= 1; rx_sync <= 1; end
        else begin rx_meta <= uart_rx; rx_sync <= rx_meta; end
    end

    localparam RX_IDLE=0, RX_START=1, RX_BITS=2, RX_STOP=3, RX_BREAK=4;
    reg [2:0] rx_state, rx_bit;
    reg [31:0] rx_timer;
    reg [7:0] rx_shift;
    wire rx_complete = rx_state == RX_STOP && rx_timer == 0;

    always @(posedge clk) begin
        if (reset) begin
            irq_enable <= 0;
            rx_valid <= 0; rx_data <= 0; overrun <= 0; framing <= 0;
            tx_valid <= 0; tx_hold <= 0; tx_busy <= 0;
            tx_shift <= 10'h3ff; tx_left <= 0; tx_timer <= 0;
            rx_state <= RX_IDLE; rx_bit <= 0; rx_timer <= 0; rx_shift <= 0;
        end else begin
            if (fire && bus_write && reg_sel == 2 && (|bus_be))
                irq_enable <= write_byte[2:0];
            if (clear_errors) begin
                if (write_byte[3]) overrun <= 0;
                if (write_byte[4]) framing <= 0;
            end
            if (rx_pop) rx_valid <= 0;
            // A new character/error wins over a simultaneous pop/W1C.
            if (rx_complete) begin
                if (!rx_sync) framing <= 1;
                else if (rx_valid && !rx_pop) overrun <= 1;
                else begin rx_data <= rx_shift; rx_valid <= 1; end
            end

            if (tx_pop) begin
                tx_shift <= {1'b1,tx_hold,1'b0};
                tx_left <= 10; tx_timer <= DIV-1; tx_busy <= 1;
                tx_valid <= 0;
            end else if (tx_busy) begin
                if (tx_timer != 0) tx_timer <= tx_timer-1;
                else begin
                    tx_timer <= DIV-1;
                    tx_shift <= {1'b1,tx_shift[9:1]};
                    tx_left <= tx_left-1;
                    if (tx_left == 1) tx_busy <= 0;
                end
            end
            if (tx_push) begin tx_hold <= write_byte; tx_valid <= 1; end

            case (rx_state)
                RX_IDLE: if (!rx_sync) begin rx_timer <= HALF-1; rx_state <= RX_START; end
                RX_START: begin
                    if (rx_timer != 0) rx_timer <= rx_timer-1;
                    else if (rx_sync) rx_state <= RX_IDLE; // reject short glitch
                    else begin rx_timer <= DIV-1; rx_bit <= 0; rx_state <= RX_BITS; end
                end
                RX_BITS: begin
                    if (rx_timer != 0) rx_timer <= rx_timer-1;
                    else begin
                        rx_shift[rx_bit] <= rx_sync;
                        rx_timer <= DIV-1;
                        if (rx_bit == 7) rx_state <= RX_STOP;
                        else rx_bit <= rx_bit+1;
                    end
                end
                RX_STOP: begin
                    if (rx_timer != 0) rx_timer <= rx_timer-1;
                    else rx_state <= rx_sync ? RX_IDLE : RX_BREAK;
                end
                RX_BREAK: if (rx_sync) rx_state <= RX_IDLE;
                default: rx_state <= RX_IDLE;
            endcase
        end
    end
endmodule
`default_nettype wire
