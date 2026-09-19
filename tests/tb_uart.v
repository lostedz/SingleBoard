`timescale 1ns/1ps
module tb_uart;
    reg clk=0;
    always #10 clk=~clk;
    localparam BIT_NS=8680; // round(50 MHz/115200) clocks, independent serial peer
    reg reset=1, enable=1, valid=0, wr=0, rx=1;
    reg [23:0] addr=0;
    reg [1:0] be=3;
    reg [15:0] wd=0;
    wire [15:0] rd;
    wire ready, tx, irq;
    reg [15:0] result;
    integer tx_count=0;
    reg [7:0] received [0:15];
    reg [7:0] serial_byte;
    integer bit_index;
    uart_mmio dut(.clk(clk),.reset(reset),.bus_clk_en(enable),.bus_valid(valid),
        .bus_write(wr),.bus_addr(addr),.bus_be(be),.bus_wdata(wd),
        .bus_rdata(rd),.bus_ready(ready),.uart_rx(rx),.uart_tx(tx),.irq(irq));
    // Independent serial receiver verifies bit order, width and stop bits.
    initial forever begin
        @(negedge tx);
        #(BIT_NS/2);
        if (tx !== 0) $fatal(1,"TX start bit too short");
        for (bit_index=0;bit_index<8;bit_index=bit_index+1) begin
            #BIT_NS; serial_byte[bit_index]=tx;
        end
        #BIT_NS;
        if (tx !== 1) $fatal(1,"TX stop bit invalid");
        received[tx_count]=serial_byte;
        tx_count=tx_count+1;
    end
    task access;
        input write_access;
        input [23:0] a;
        input [15:0] data;
        input [1:0] lanes;
        begin
            @(negedge clk); valid=1; wr=write_access; addr=a; wd=data; be=lanes;
            @(posedge clk);
            while (!ready) @(posedge clk);
            result=rd;
            @(negedge clk); valid=0;
        end
    endtask
    // Host serial clock deliberately differs slightly from the DUT divider.
    task send;
        input [7:0] data;
        input bad_stop;
        integer j;
        begin
            rx=0; #8650;
            for (j=0;j<8;j=j+1) begin rx=data[j]; #8650; end
            rx=!bad_stop; #8650;
            rx=1; #8650;
        end
    endtask
    initial begin
        #5000000; $fatal(1,"UART timeout");
    end
    initial begin
        repeat(5) @(negedge clk); reset=0;
        access(0,24'hff0002,0,3);
        if (result !== 16'h0606) $fatal(1,"Reset status %h",result);
        access(0,24'hff0006,0,3);
        if (result !== 434) $fatal(1,"Baud divider %h",result);
        // Requests outside the UART's eight-byte region are not acknowledged.
        @(negedge clk); valid=1; addr=24'hff0008;
        @(negedge clk); if(ready) $fatal(1,"Address decode alias"); valid=0;
        access(1,24'hff0000,16'h4100,2);
        access(1,24'hff0001,16'h0042,1);
        access(1,24'hff0000,16'h0043,3); // waits for holding register space
        wait(tx_count==3);
        if(received[0]!==8'h41 || received[1]!==8'h42 || received[2]!==8'h43)
            $fatal(1,"TX bytes duplicated, reordered or corrupted");
        repeat(500) @(negedge clk);
        fork
            access(1,24'hff0001,16'h44,1);
            send(8'h9c,0);
        join
        wait(tx_count==4);
        if(received[3]!==8'h44) $fatal(1,"Full-duplex TX failed");
        access(0,24'hff0001,0,1);
        if(result!==16'h9c9c) $fatal(1,"Full-duplex RX failed");
        access(1,24'hff0000,16'h99,0);
        #(BIT_NS*12);
        if(tx_count!=4) $fatal(1,"Zero-lane write transmitted data");
        access(1,24'hff0004,1,1); // RX IRQ enable
        send(8'ha6,0);
        if (!irq) $fatal(1,"Missing RX interrupt");
        access(0,24'hff0000,0,0); // no lane, no pop
        if(!irq) $fatal(1,"Zero-lane read popped RX");
        @(negedge clk); enable=0; valid=1; wr=0; addr=24'hff0001; be=1;
        repeat(10) @(negedge clk);
        if(!irq || rd!==16'ha6a6) $fatal(1,"Paused read lost data");
        valid=0; enable=1;
        access(0,24'hff0000,0,2);
        if(result!==16'ha6a6 || irq) $fatal(1,"RX data/pop failed");
        access(0,24'hff0001,0,1);
        if(result!==0) $fatal(1,"Empty RX read");
        send(8'h12,0); send(8'h34,0);
        access(0,24'hff0002,0,3);
        if(!result[3]) $fatal(1,"Overrun not reported");
        access(0,24'hff0001,0,1);
        if(result!==16'h1212) $fatal(1,"Overrun did not retain oldest byte");
        access(1,24'hff0002,16'h0800,2);
        access(0,24'hff0002,0,3);
        if(result[3]) $fatal(1,"Overrun W1C failed");
        access(1,24'hff0004,4,1); // error IRQ
        send(8'h55,1);
        if(!irq) $fatal(1,"Missing framing interrupt");
        access(0,24'hff0002,0,3);
        if(!result[4] || result[0]) $fatal(1,"Invalid frame was accepted");
        access(1,24'hff0003,16'h10,1);
        if(irq) $fatal(1,"Framing W1C failed");
        // A start glitch shorter than half a bit must be ignored.
        rx=0; #1000; rx=1; #(BIT_NS*12);
        access(0,24'hff0002,0,3);
        if(result[0] || result[4]) $fatal(1,"False start was accepted");
        send(8'h81,0);
        access(0,24'hff0000,0,3);
        if(result!==16'h8181) $fatal(1,"RX did not recover after framing error");
        access(1,24'hff0004,2,1);
        if(!irq) $fatal(1,"Missing TX-space interrupt");
        @(negedge clk); reset=1;
        repeat(3) @(negedge clk); reset=0;
        if(irq || !tx) $fatal(1,"Reset failed");
        $display("PASS UART: TX, RX, byte lanes, stalls, interrupts, overrun, framing, false starts");
        $finish;
    end
endmodule
