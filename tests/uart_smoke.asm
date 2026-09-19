; 68000 integration test: send OK, receive/echo ! using level-2 autovector.
    org 0
    dc.l $8000,start
    blk.l 24,0
    dc.l irq2
    org $100
start:
    move.b #'O',$ff0001
    move.b #'K',$ff0001
    move.b #1,$ff0005
    stop #$2000
    move.w #$600d,$f000.l
    stop #$2700
    bra *
irq2:
    move.b $ff0001,d0
    cmpi.b #'!',d0
    bne fail
    move.b d0,$ff0001
    rte
fail:
    move.w #$dead,$f000.l
    stop #$2700
    bra *
