; Self-checking 68000 program. Assemble with vasm -m68000 -Fbin.
    org 0
    dc.l $00008000,start
    blk.l 24,0
    dc.l irq2                 ; vector 26: level 2 autovector
    blk.l 5,0
    dc.l trap0                ; vector 32: TRAP #0
    org $100
start:
    moveq #1,d7
    moveq #7,d0
    moveq #5,d1
    add.l d1,d0
    cmpi.l #12,d0
    bne fail
    sub.l d1,d0
    cmpi.l #7,d0
    bne fail

    moveq #2,d7
    move.l #$12345678,$1000.w
    cmpi.w #$1234,$1000.w
    bne fail
    cmpi.w #$5678,$1002.w
    bne fail
    move.b #$aa,$1000.w
    move.b #$55,$1001.w
    cmpi.l #$aa555678,$1000.w
    bne fail

    moveq #3,d7
    lea $1100.w,a0
    move.l #$89abcdef,(a0)+
    cmpa.l #$1104,a0
    bne fail
    move.l -(a0),d2
    cmpi.l #$89abcdef,d2
    bne fail
    move.w #$1234,6(a0)
    moveq #6,d3
    move.w 0(a0,d3.w),d2
    cmpi.w #$1234,d2
    bne fail

    moveq #4,d7
    moveq #0,d0
    moveq #9,d1
loop:
    addq.l #1,d0
    dbra d1,loop
    cmpi.l #10,d0
    bne fail
    bsr subroutine
    cmpi.l #11,d0
    bne fail

    moveq #5,d7
    moveq #0,d0
    move.b #$7f,d0
    addq.b #1,d0
    bvc fail
    bpl fail
    moveq #-1,d0
    addq.l #1,d0
    bcc fail
    bne fail

    moveq #6,d7
    move.l #$12345678,d0
    andi.l #$00ff00ff,d0
    ori.l #$80000000,d0
    eori.l #$00340000,d0
    cmpi.l #$80000078,d0
    bne fail
    moveq #1,d0
    lsl.l #8,d0
    cmpi.l #256,d0
    bne fail
    lsr.l #4,d0
    cmpi.l #16,d0
    bne fail
    bset #3,d0
    bclr #4,d0
    cmpi.l #8,d0
    bne fail

    moveq #7,d7
    moveq #12,d0
    mulu #13,d0
    cmpi.l #156,d0
    bne fail
    divu #12,d0
    cmpi.l #13,d0
    bne fail
    moveq #-12,d0
    muls #13,d0
    cmpi.l #-156,d0
    bne fail
    divs #12,d0
    cmpi.w #-13,d0
    bne fail

    moveq #8,d7
    move.l #$12345678,d0
    move.l #$abcdef01,d1
    movem.l d0-d1/a0,-(sp)
    clr.l d0
    clr.l d1
    suba.l a0,a0
    movem.l (sp)+,d0-d1/a0
    cmpi.l #$12345678,d0
    bne fail
    cmpi.l #$abcdef01,d1
    bne fail
    cmpa.l #$1100,a0
    bne fail
    cmpa.l #$8000,sp
    bne fail

    moveq #9,d7
    moveq #0,d6
    trap #0
    cmpi.l #1,d6
    bne fail
    cmpa.l #$8000,sp
    bne fail

    moveq #10,d7
    move.w #$cafe,$f000.l    ; bench requests level 2 interrupt
    stop #$2000              ; unmask interrupts and wait
    cmpi.l #2,d6
    bne fail
    move.w #$600d,$f000.l
    stop #$2700
    bra *
fail:
    move.w d7,$f002.l
    stop #$2700
    bra *
subroutine:
    addq.l #1,d0
    rts
trap0:
    addq.l #1,d6
    rte
irq2:
    addq.l #1,d6
    move.w #$beef,$f000.l
    rte

