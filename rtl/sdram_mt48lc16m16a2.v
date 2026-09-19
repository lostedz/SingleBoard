// SPDX-License-Identifier: ISC
// MT48LC16M16A2-75, fixed 50 MHz, burst length 1, CAS latency 2.
// SDRAM samples commands on the FALLING edge of clk: forward an inverted
// clock using an ODDR2 at the FPGA boundary. See docs/sdram.md.
`default_nettype none
module sdram_mt48lc16m16a2 #(
    parameter integer INIT_CYCLES = 10000,   // 200 us after stable power/clock
    parameter integer REFRESH_CYCLES = 300  // 6 us, standard/industrial grade
) (
    input wire clk,
    input wire reset,
    input wire bus_clk_en,
    input wire bus_valid,
    input wire bus_write,
    input wire [24:0] bus_addr,
    input wire [1:0] bus_be,
    input wire [15:0] bus_wdata,
    output reg [15:0] bus_rdata,
    output wire bus_ready,
    output reg init_done,
    output reg sdram_cke,
    output wire sdram_cs_n,
    output wire sdram_ras_n,
    output wire sdram_cas_n,
    output wire sdram_we_n,
    output reg [12:0] sdram_addr,
    output reg [1:0] sdram_ba,
    output reg [1:0] sdram_dqm,
    inout wire [15:0] sdram_dq
);
    localparam [3:0] NOP=4'b0111, PRE=4'b0010, REF=4'b0001,
                     MRS=4'b0000, ACT=4'b0011, RD=4'b0101, WR=4'b0100;
    localparam [4:0] POWER_WAIT=0, CKE_WAIT=1, INIT_PRE=2,
        INIT_REF1=3, INIT_REF2=4, INIT_MODE=5, INIT_FINISH=6,
        IDLE=7, ACTIVATE=8, ACCESS=9, READ_WAIT=10, CLOSE_ROW=11,
        COMPLETE=12, WAIT_CYCLES=13;
    reg [4:0] state, after_wait;
    reg [31:0] delay_count;
    reg [31:0] refresh_age;
    reg [3:0] command;
    reg [24:0] request_addr;
    reg [15:0] request_data;
    reg [1:0] request_be;
    reg request_write;
    reg response_pending;
    reg dq_drive;
    reg [15:0] dq_out;
    // Capture at the end of the SDRAM data-valid cycle, before its next
    // clock-to-output transition. The 20 ns interval minus 6 ns maximum tAC
    // leaves ~14 ns for clock, PCB, input and setup delays.
    (* IOB = "TRUE" *) reg [15:0] dq_sample;
    always @(negedge clk) dq_sample <= sdram_dq;
    assign {sdram_cs_n,sdram_ras_n,sdram_cas_n,sdram_we_n} = command;
    assign sdram_dq = dq_drive ? dq_out : 16'bz;
    assign bus_ready = response_pending && init_done && !reset;

    // All physical outputs are registered. DQM stays low during idle/read
    // because SDRAM read masking has a two-cycle latency; writes alone mask.
    always @(posedge clk) begin
        if (reset) begin
            state <= POWER_WAIT;
            after_wait <= IDLE;
            delay_count <= INIT_CYCLES - 1;
            refresh_age <= 0;
            command <= NOP;
            sdram_cke <= 0;
            sdram_addr <= 0;
            sdram_ba <= 0;
            sdram_dqm <= 0;
            dq_drive <= 0;
            dq_out <= 0;
            response_pending <= 0;
            init_done <= 0;
            bus_rdata <= 0;
            request_addr <= 0;
            request_data <= 0;
            request_be <= 0;
            request_write <= 0;
        end else begin
            command <= NOP;
            dq_drive <= 0;
            sdram_dqm <= 0;
            if (init_done && refresh_age < REFRESH_CYCLES)
                refresh_age <= refresh_age + 1;
            if (bus_valid && bus_clk_en && response_pending)
                response_pending <= 0;
            case (state)
                POWER_WAIT: begin
                    if (delay_count != 0) delay_count <= delay_count - 1;
                    else begin
                        sdram_cke <= 1;
                        state <= CKE_WAIT;
                    end
                end
                CKE_WAIT: state <= INIT_PRE;
                INIT_PRE: begin
                    command <= PRE;
                    sdram_addr <= 13'h400; // precharge ALL banks
                    delay_count <= 1;      // 2 wait states plus command state
                    after_wait <= INIT_REF1;
                    state <= WAIT_CYCLES;
                end
                INIT_REF1, INIT_REF2: begin
                    command <= REF;
                    delay_count <= 3;      // >= 4 clocks = 80 ns tRFC
                    after_wait <= (state == INIT_REF1) ? INIT_REF2 : INIT_MODE;
                    state <= WAIT_CYCLES;
                end
                INIT_MODE: begin
                    command <= MRS;
                    sdram_ba <= 0;
                    sdram_addr <= 13'h020; // sequential BL=1, CL=2
                    delay_count <= 1;      // >= 2 clocks tMRD
                    after_wait <= INIT_FINISH;
                    state <= WAIT_CYCLES;
                end
                INIT_FINISH: begin
                    init_done <= 1;
                    refresh_age <= 0;
                    state <= IDLE;
                end
                IDLE: begin
                    // Response storage is independent of maintenance. A CPU
                    // paused on its completion cannot prevent refresh.
                    if (refresh_age >= REFRESH_CYCLES) begin
                        command <= REF;    // every transaction closes its row
                        refresh_age <= 0;
                        delay_count <= 3;
                        after_wait <= IDLE;
                        state <= WAIT_CYCLES;
                    end else if (bus_valid && bus_clk_en && !response_pending) begin
                        request_addr <= bus_addr;
                        request_data <= bus_wdata;
                        request_be <= bus_be;
                        request_write <= bus_write;
                        state <= ACTIVATE;
                    end
                end
                ACTIVATE: begin
                    command <= ACT;
                    sdram_ba <= request_addr[24:23];
                    sdram_addr <= request_addr[22:10];
                    delay_count <= 1;      // conservative >= 40 ns tRCD
                    after_wait <= ACCESS;
                    state <= WAIT_CYCLES;
                end
                ACCESS: begin
                    sdram_addr <= {4'b0000,request_addr[9:1]}; // A10=0, no AP
                    if (request_write) begin
                        command <= WR;
                        sdram_dqm <= ~request_be;
                        dq_out <= request_data;
                        dq_drive <= 1;
                        delay_count <= 1;  // >= 40 ns write recovery
                        after_wait <= CLOSE_ROW;
                        state <= WAIT_CYCLES;
                    end else begin
                        command <= RD;
                        delay_count <= 3;  // CL2: appears N+2, sampled at N+3
                        state <= READ_WAIT;
                    end
                end
                READ_WAIT: begin
                    if (delay_count != 0) delay_count <= delay_count - 1;
                    else begin
                        // Transfer the falling-edge input register at P+4.
                        bus_rdata <= dq_sample;
                        state <= CLOSE_ROW;
                    end
                end
                CLOSE_ROW: begin
                    command <= PRE;
                    sdram_addr <= 13'h400;
                    delay_count <= 1;
                    after_wait <= COMPLETE;
                    state <= WAIT_CYCLES;
                end
                COMPLETE: begin
                    response_pending <= 1;
                    state <= IDLE;
                end
                WAIT_CYCLES: begin
                    if (delay_count != 0) delay_count <= delay_count - 1;
                    else state <= after_wait;
                end
                default: state <= POWER_WAIT;
            endcase
        end
    end
endmodule
`default_nettype wire
