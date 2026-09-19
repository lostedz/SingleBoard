`timescale 1ns/1ps
module tb_cpu_uart;
    reg clk=0;
    always #10 clk=~clk;
    reg reset=1, rx=1;
    wire valid, wr, ready, uart_ready, irq, tx;
    wire [23:0] addr;
    wire [15:0] wd, rd, uart_rd;
    wire [1:0] be;
    wire [31:0] pc;
    reg [7:0] mem [0:65535];
    wire select_uart=addr[23:3]==21'h1fe000;
    assign ready=select_uart ? uart_ready : valid;
    assign rd=select_uart ? uart_rd : {mem[{addr[15:1],1'b0}],mem[{addr[15:1],1'b1}]};
    m68000 cpu(.clk(clk),.reset(reset),.clk_en(1'b1),.ipl_n(irq ? 3'b101 : 3'b111),
        .bus_valid(valid),.bus_write(wr),.bus_addr(addr),.bus_be(be),.bus_wdata(wd),
        .bus_rdata(rd),.bus_ready(ready),.bus_fc(),.debug_pc(pc),.debug_sr());
    uart_mmio uart(.clk(clk),.reset(reset),.bus_clk_en(1'b1),.bus_valid(valid),
        .bus_write(wr),.bus_addr(addr),.bus_be(be),.bus_wdata(wd),
        .bus_rdata(uart_rd),.bus_ready(uart_ready),.uart_rx(rx),.uart_tx(tx),.irq(irq));
    integer i,j,n=0;
    reg [7:0] byte_in;
    reg done=0;
    initial forever begin
        @(negedge tx);
        #4340;
        if(tx!==0) $fatal(1,"CPU UART start bit");
        for(j=0;j<8;j=j+1) begin #8680; byte_in[j]=tx; end
        #8680;
        if(tx!==1) $fatal(1,"CPU UART stop bit");
        case(n)
            0: if(byte_in!==8'h4f) $fatal(1,"Expected O, got %h",byte_in);
            1: if(byte_in!==8'h4b) $fatal(1,"Expected K, got %h",byte_in);
            2: if(byte_in!==8'h21) $fatal(1,"Expected echo !, got %h",byte_in);
            default: $fatal(1,"Duplicate UART transmission");
        endcase
        n=n+1;
    end
    always @(posedge clk) if (!reset && valid && ready && wr && !select_uart) begin
        if(be[1]) mem[{addr[15:1],1'b0}]<=wd[15:8];
        if(be[0]) mem[{addr[15:1],1'b1}]<=wd[7:0];
        if(addr==24'h00f000) begin
            if(wd!==16'h600d) $fatal(1,"CPU UART firmware failed");
            done<=1;
        end
    end
    initial begin
        for(i=0;i<65536;i=i+1) mem[i]=0;
        $readmemh("tests/uart_smoke.hex",mem,0,1023);
        repeat(8) @(negedge clk); reset=0;
        wait(n==2);
        #8680;
        rx=0; #8680;
        for(i=0;i<8;i=i+1) begin rx=(8'h21>>i)&1; #8680; end
        rx=1;
        wait(done && n==3);
        $display("PASS CPU UART: transmitted OK, level-2 interrupt, received and echoed !");
        $finish;
    end
    initial begin #3000000; $fatal(1,"CPU UART timeout pc=%h",pc); end
endmodule
