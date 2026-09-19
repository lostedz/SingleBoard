// SPDX-License-Identifier: ISC
// Instantiate beside the controller at the board boundary. clk must already
// use a BUFG. Do not replace this with a fabric inverter for implementation.
`default_nettype none
module sdram_spartan6_clock (
    input wire clk,
    output wire sdram_clk
);
    ODDR2 #(.DDR_ALIGNMENT("NONE"), .INIT(1'b0), .SRTYPE("SYNC")) forward_clock (
        .Q(sdram_clk), .C0(clk), .C1(~clk), .CE(1'b1),
        .D0(1'b0), .D1(1'b1), .R(1'b0), .S(1'b0)
    );
endmodule
`default_nettype wire
