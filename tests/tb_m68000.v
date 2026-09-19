`timescale 1ns/1ps
module tb_m68000;
    reg clk = 0;
`ifdef TEST_SDRAM
    always #10 clk = ~clk;
`else
    always #5 clk = ~clk;
`endif
    reg reset = 1;
    reg clk_en = 1;
    reg [2:0] ipl_n = 7;
    wire valid, wr;
    wire [23:0] addr;
    wire [1:0] be;
    wire [15:0] wdata;
    wire [2:0] fc;
    wire [31:0] pc;
    wire [15:0] sr;
    reg [7:0] mem [0:65535];
    wire [15:0] rdata;
    integer wait_cycles = 0;
    integer pause_mode = 0;
    integer age = 0;
    integer cycles = 0;
    integer transfers = 0;
    integer passes = 0;
    integer irq_seen = 0;
    integer boot_reads = 0;
    integer i;
    reg [15:0] prng = 16'h1ace;
    wire ready;
`ifdef TEST_SDRAM
    reg memory_reset = 1;
    wire initialized, cke, cs_n, ras_n, cas_n, we_n;
    wire [12:0] sa;
    wire [1:0] bank, dqm;
    wire [15:0] dq;
    sdram_mt48lc16m16a2 controller (
        .clk(clk), .reset(memory_reset), .bus_clk_en(clk_en),
        .bus_valid(valid), .bus_write(wr), .bus_addr({1'b0,addr}),
        .bus_be(be), .bus_wdata(wdata), .bus_rdata(rdata), .bus_ready(ready),
        .init_done(initialized), .sdram_cke(cke), .sdram_cs_n(cs_n),
        .sdram_ras_n(ras_n), .sdram_cas_n(cas_n), .sdram_we_n(we_n),
        .sdram_addr(sa), .sdram_ba(bank), .sdram_dqm(dqm), .sdram_dq(dq)
    );
    mt48lc16m16a2_model sdram (
        .clk(~clk), .cke(cke), .cs_n(cs_n), .ras_n(ras_n), .cas_n(cas_n),
        .we_n(we_n), .addr(sa), .ba(bank), .dqm(dqm), .dq(dq)
    );
`else
    assign rdata = {mem[{addr[15:1],1'b0}], mem[{addr[15:1],1'b1}]};
    assign ready = valid && age >= wait_cycles;
`endif
    reg pending = 0;
    reg [45:0] held;
    wire [45:0] request = {wr, addr, be, wdata, fc};

    m68000 dut (
        .clk(clk), .reset(reset), .clk_en(clk_en), .ipl_n(ipl_n),
        .bus_valid(valid), .bus_write(wr), .bus_addr(addr),
        .bus_be(be), .bus_wdata(wdata), .bus_rdata(rdata),
        .bus_ready(ready), .bus_fc(fc), .debug_pc(pc), .debug_sr(sr)
    );

    initial begin
        if ($value$plusargs("WAIT=%d", wait_cycles)) begin end
        if ($value$plusargs("PAUSE=%d", pause_mode)) begin end
        for (i = 0; i < 65536; i = i + 1) mem[i] = 0;
        $readmemh("tests/smoke.hex", mem, 0, 1023);
`ifdef TEST_SDRAM
        // Test fixture only: real hardware needs a boot ROM/loader.
        #1;
        for (i = 0; i < 1024; i = i + 2)
            sdram.preload_word(i/2, {mem[i],mem[i+1]});
`endif
        if ($test$plusargs("VCD")) begin
            $dumpfile("build/m68000.vcd");
            $dumpvars(0, tb_m68000);
        end
        repeat (8) @(negedge clk);
`ifdef TEST_SDRAM
        memory_reset = 0;
`endif
        reset = 0;
        // Run twice, verifying reset restarts vector loading after STOP.
        wait (passes == 1);
        @(negedge clk);
        reset = 1;
        ipl_n = 7;
        repeat (8) @(negedge clk);
        reset = 0;
        wait (passes == 2);
        $display("PASS wait=%0d pause=%0d cycles=%0d transfers=%0d", wait_cycles, pause_mode, cycles, transfers);
        $finish;
    end

    always @(negedge clk) begin
        prng = {prng[14:0], prng[15] ^ prng[13] ^ prng[12] ^ prng[10]};
        case (pause_mode)
            1: clk_en = reset || (cycles % 5 == 0);
            2: clk_en = reset || (prng[2:0] == 0);
            default: clk_en = 1;
        endcase
    end

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (cycles > 300000) $fatal(1, "Timeout pc=%h sr=%h", pc, sr);
        if (reset) begin
            age = 0;
            pending = 0;
            irq_seen = 0;
            boot_reads = 0;
        end else begin
            if (pending && (!valid || request !== held))
                $fatal(1, "Bus request changed before acknowledgement pc=%h", pc);
            pending = valid && !(ready && clk_en);
            held = request;
            if (valid && clk_en && ready) begin
                if (addr[23:16] !== 0) $fatal(1, "Unexpected address %h", addr);
                if (be == 0) $fatal(1, "Empty byte enable");
                transfers = transfers + 1;
                if (boot_reads < 4) begin
                    if (wr || addr !== boot_reads * 2 || be !== 2'b11)
                        $fatal(1, "Incorrect reset vector read %0d at %h", boot_reads, addr);
                    boot_reads = boot_reads + 1;
                end
                if (wr) begin
                    if (be[1]) mem[{addr[15:1],1'b0}] <= wdata[15:8];
                    if (be[0]) mem[{addr[15:1],1'b1}] <= wdata[7:0];
                    if (addr == 24'h00f002)
                        $fatal(1, "CPU self-test failed at stage %0d pc=%h", wdata, pc);
                    if (addr == 24'h00f000) begin
                        case (wdata)
                            16'hcafe: ipl_n <= 3'b101;
                            16'hbeef: begin
                                ipl_n <= 3'b111;
                                irq_seen = 1;
                            end
                            16'h600d: begin
                                if (!irq_seen) $fatal(1, "Interrupt handler did not run");
                                passes = passes + 1;
                            end
                            default: $fatal(1, "Unexpected result %h", wdata);
                        endcase
                    end
                end
                age <= 0;
            end else if (valid) age <= age + 1;
            else age <= 0;
        end
    end
endmodule
