`timescale 1ns/1ps
module tb_sdram;
    parameter integer REFRESH_INTERVAL = 300;
    reg clk=0;
    always #10 clk=~clk;
    reg reset=1, enable=1, valid=0, write=0;
    reg [24:0] address=0;
    reg [15:0] wdata=0;
    reg [1:0] be=3;
    wire [15:0] rdata;
    wire ready, initialized, cke, cs, ras, cas, we;
    wire [12:0] a;
    wire [1:0] ba, dqm;
    wire [15:0] dq;
    wire memory_clock;
    assign #2 memory_clock = ~clk; // example forwarded-clock/PCB delay
    integer i, b, r, c;
    integer before_writes, before_refresh, before_active;
    reg [24:0] location;
    reg [15:0] expected;
    reg [31:0] rng=32'h125fabcd;
    reg [24:0] locations [0:127];
    reg [15:0] values [0:127];
    sdram_mt48lc16m16a2 #(.REFRESH_CYCLES(REFRESH_INTERVAL)) dut (
        .clk(clk),.reset(reset),.bus_clk_en(enable),.bus_valid(valid),
        .bus_write(write),.bus_addr(address),.bus_be(be),.bus_wdata(wdata),
        .bus_rdata(rdata),.bus_ready(ready),.init_done(initialized),
        .sdram_cke(cke),.sdram_cs_n(cs),.sdram_ras_n(ras),.sdram_cas_n(cas),
        .sdram_we_n(we),.sdram_addr(a),.sdram_ba(ba),.sdram_dqm(dqm),.sdram_dq(dq)
    );
    mt48lc16m16a2_model #(.READ_FLIGHT_NS(2),
        .MAX_REFRESH_NS(REFRESH_INTERVAL == 75 ? 1953.125 : 7812.5)) memory (
        .clk(memory_clock),.cke(cke),.cs_n(cs),.ras_n(ras),.cas_n(cas),.we_n(we),
        .addr(a),.ba(ba),.dqm(dqm),.dq(dq)
    );
    initial begin
        #5000000;
        $fatal(1,"SDRAM test timeout");
    end
    task transfer;
        input wr;
        input [24:0] addr;
        input [15:0] data;
        input [1:0] mask;
        input [15:0] want;
        begin
            @(negedge clk);
            valid=1; write=wr; address=addr; wdata=data; be=mask;
            while (!ready) @(negedge clk);
            if (!wr && rdata !== want)
                $fatal(1,"Read %h: expected %h got %h",addr,want,rdata);
            @(negedge clk);
            valid=0;
        end
    endtask
    initial begin
        if ($test$plusargs("VCD")) begin
            $dumpfile("build/sdram.vcd");
            $dumpvars(0,dut);
        end
        repeat (4) @(negedge clk);
        reset=0;
        // Request before initialization must stall until it is complete.
        transfer(1,0,16'h1234,3,0);
        if (!initialized || memory.mode_count != 1) $fatal(1,"Initialization failed");
        transfer(0,0,0,3,16'h1234);
        transfer(1,0,16'hab00,2,0);
        transfer(1,1,16'h00cd,1,0);
        transfer(0,0,0,3,16'habcd);
        transfer(1,0,16'hffff,0,0);
        transfer(0,0,0,1,16'habcd);

        // All banks, first/last row and first/last column: no address aliases.
        for (b=0;b<4;b=b+1)
            for (r=0;r<2;r=r+1)
                for (c=0;c<2;c=c+1) begin
                    location=(b<<23) | ((r*8191)<<10) | ((c*511)<<1);
                    expected=16'h4000+(b*4+r*2+c);
                    transfer(1,location,expected,3,0);
                end
        for (b=0;b<4;b=b+1)
            for (r=0;r<2;r=r+1)
                for (c=0;c<2;c=c+1) begin
                    location=(b<<23) | ((r*8191)<<10) | ((c*511)<<1);
                    expected=16'h4000+(b*4+r*2+c);
                    transfer(0,location,0,3,expected);
                end
        for (i=0;i<128;i=i+1) begin
            rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
            locations[i]={rng[23:0],1'b0};
            values[i]=rng[31:16]^rng[15:0];
            transfer(1,locations[i],values[i],3,0);
        end
        for (i=127;i>=0;i=i-1)
            transfer(0,locations[i],0,3,values[i]);

        // A paused master may hold completion indefinitely. Writes occur once,
        // read data is retained, and refresh must still make progress.
        before_writes=memory.write_count;
        before_active=memory.active_count;
        @(negedge clk);
        valid=1; write=1; address=25'h1020300; wdata=16'h5a3c; be=3;
        wait(memory.active_count > before_active);
        @(negedge clk);
        enable=0;
        wait(ready);
        before_refresh=memory.refresh_count;
        repeat (1200) @(negedge clk);
        if (!ready || memory.write_count != before_writes+1)
            $fatal(1,"Paused completion repeated or lost a write");
        if (memory.refresh_count < before_refresh+3) $fatal(1,"Refresh starved by paused response");
        enable=1;
        @(negedge clk); valid=0;
        transfer(0,25'h1020300,0,3,16'h5a3c);
        before_active=memory.active_count;
        @(negedge clk);
        valid=1; write=0;
        wait(memory.active_count > before_active);
        @(negedge clk);
        enable=0;
        wait(ready);
        repeat (800) begin
            @(negedge clk);
            if (!ready || rdata !== 16'h5a3c) $fatal(1,"Paused read response changed");
        end
        enable=1;
        @(negedge clk); valid=0;
        before_refresh=memory.refresh_count;
        repeat (1500) @(negedge clk);
        if (memory.refresh_count < before_refresh+4) $fatal(1,"Idle refresh stopped");
        $display("PASS SDRAM: reads=%0d writes=%0d refreshes=%0d",memory.read_count,memory.write_count,memory.refresh_count);
        $finish;
    end
endmodule
