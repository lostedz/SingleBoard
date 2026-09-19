// SPDX-License-Identifier: ISC
// Independent command-level checker for the subset used by this controller.
// Sparse storage retains full bank/row/column tags; this is NOT a vendor model.
`timescale 1ns/1ps
module mt48lc16m16a2_model #(
    parameter integer READ_FLIGHT_NS = 0,
    parameter real MAX_REFRESH_NS = 7812.5
) (
    input wire clk, cke, cs_n, ras_n, cas_n, we_n,
    input wire [12:0] addr,
    input wire [1:0] ba, dqm,
    inout wire [15:0] dq
);
    reg [3:0] open_bank = 0;
    reg [12:0] row [0:3];
    realtime activated [0:3];
    realtime precharged [0:3];
    realtime written [0:3];
    realtime last_refresh = -1000;
    realtime last_mode = -1000;
    realtime last_activate = -1000;
    integer refresh_count = 0;
    integer write_count = 0;
    integer read_count = 0;
    integer active_count = 0;
    integer mode_count = 0;
    integer init_refresh = 0;
    reg pre_seen = 0;
    reg mode_set = 0;
    reg [23:0] tags [0:8191];
    reg [15:0] words [0:8191];
    integer used = 0;
    integer cycle = 0;
    integer due = -1;
    reg [15:0] read_word;
    reg [15:0] dout;
    reg drive = 0;
    reg [1:0] dqm1 = 0, dqm2 = 0;
    reg [1:0] read_mask = 0;
    integer k, slot;
    reg [23:0] key;
    wire [3:0] cmd = {cs_n,ras_n,cas_n,we_n};
    assign dq[15:8] = drive && !read_mask[1] ? dout[15:8] : 8'bz;
    assign dq[7:0] = drive && !read_mask[0] ? dout[7:0] : 8'bz;

    initial begin
        for (k=0;k<4;k=k+1) begin
            activated[k] = -1000;
            precharged[k] = -1000;
            written[k] = -1000;
        end
    end

    function integer lookup;
        input [23:0] tag;
        integer j;
        begin
            lookup = -1;
            for (j=0;j<used;j=j+1)
                if (tags[j] == tag) lookup = j;
        end
    endfunction

    task preload_word;
        input [23:0] word_address;
        input [15:0] value;
        integer s;
        begin
            s = lookup(word_address);
            if (s < 0) begin
                if (used == 8192) $fatal(1,"SDRAM model sparse storage full");
                s = used;
                used = used + 1;
                tags[s] = word_address;
            end
            words[s] = value;
        end
    endtask

    always @(posedge clk) begin
        cycle = cycle + 1;
        dqm1 <= dqm;
        dqm2 <= dqm1;
        if (cycle == due) begin
            dout <= #(6+READ_FLIGHT_NS) read_word; // worst-case CL2 tAC for -75
            read_mask <= #(6+READ_FLIGHT_NS) dqm2;
            drive <= #(6+READ_FLIGHT_NS) 1;
        end
        if (cycle == due + 1) drive <= #(3+READ_FLIGHT_NS) 0;
        if (mode_set && $realtime-last_refresh > MAX_REFRESH_NS)
            $fatal(1,"Refresh deadline missed at %t",$time);
        for (k=0;k<4;k=k+1)
            if (open_bank[k] && $realtime-activated[k] > 120000)
                $fatal(1,"tRAS maximum exceeded");
        if (cke && !cs_n && cmd != 4'b0111) begin
            if ($realtime < 200000) $fatal(1,"Initialization power wait too short");
            if ($realtime-last_refresh < 66) $fatal(1,"tRFC violated");
            if ($realtime-last_mode < 40) $fatal(1,"tMRD violated");
            case (cmd)
                4'b0010: begin // PRECHARGE
                    for (k=0;k<4;k=k+1) begin
                        if (addr[10] || ba == k) begin
                            if (open_bank[k] && $realtime-activated[k] < 44)
                                $fatal(1,"tRAS violated");
                            if ($realtime-written[k] < 15) $fatal(1,"tWR violated");
                            open_bank[k] = 0;
                            precharged[k] = $realtime;
                        end
                    end
                    pre_seen = 1;
                end
                4'b0001: begin // AUTO REFRESH
                    if (!pre_seen || open_bank != 0) $fatal(1,"Refresh with open banks");
                    for (k=0;k<4;k=k+1)
                        if ($realtime-precharged[k] < 20) $fatal(1,"Refresh tRP violated");
                    last_refresh = $realtime;
                    refresh_count = refresh_count + 1;
                    init_refresh = init_refresh + 1;
                end
                4'b0000: begin // LOAD MODE REGISTER
                    if (open_bank != 0 || init_refresh < 2) $fatal(1,"Invalid initialization order");
                    if (addr !== 13'h020 || ba !== 0) $fatal(1,"Expected BL1 / CL2 mode");
                    last_mode = $realtime;
                    mode_set = 1;
                    mode_count = mode_count + 1;
                end
                4'b0011: begin // ACTIVE
                    if (!mode_set || open_bank[ba]) $fatal(1,"Invalid ACTIVATE");
                    if ($realtime-precharged[ba] < 20) $fatal(1,"tRP violated");
                    if ($realtime-activated[ba] < 66) $fatal(1,"tRC violated");
                    if ($realtime-last_activate < 15) $fatal(1,"tRRD violated");
                    open_bank[ba] = 1;
                    row[ba] = addr;
                    activated[ba] = $realtime;
                    last_activate = $realtime;
                    active_count = active_count + 1;
                end
                4'b0100,4'b0101: begin
                    if (!mode_set || !open_bank[ba]) $fatal(1,"Access without active row");
                    if ($realtime-activated[ba] < 20) $fatal(1,"tRCD violated");
                    if (addr[12:9] !== 0) $fatal(1,"Unexpected auto-precharge or column bits");
                    key = {ba,row[ba],addr[8:0]};
                    slot = lookup(key);
                    if (cmd == 4'b0100) begin
                        if (drive) $fatal(1,"DQ bus contention");
                        if (slot < 0) begin
                            preload_word(key,16'hxxxx);
                            slot = lookup(key);
                        end
                        if (!dqm[1]) begin
                            if ((^dq[15:8]) === 1'bx) $fatal(1,"Invalid write high byte");
                            words[slot][15:8] = dq[15:8];
                        end
                        if (!dqm[0]) begin
                            if ((^dq[7:0]) === 1'bx) $fatal(1,"Invalid write low byte");
                            words[slot][7:0] = dq[7:0];
                        end
                        written[ba] = $realtime;
                        write_count = write_count + 1;
                    end else begin
                        if (cycle <= due+1) $fatal(1,"Overlapping read bursts");
                        read_word = (slot < 0) ? 16'hxxxx : words[slot];
                        due = cycle + 2;
                        read_count = read_count + 1;
                    end
                end
                default: $fatal(1,"Unsupported SDRAM command %b",cmd);
            endcase
        end
    end
endmodule
