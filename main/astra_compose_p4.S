// Exact RGB565 light composition. All pointers are 16-byte aligned and counts
// are positive multiples of eight. PIE registers are caller-saved by the ABI.
    .section .text.astra_compose_p4,"ax",@progbits
    .align 4

// q0 = background, q1 = light; q5/q6/q7 = red/green/blue masks.
    .macro add565
    esp.andq q2, q0, q5
    esp.andq q3, q1, q5
    esp.vadd.u16 q2, q2, q3
    esp.andq q2, q2, q5
    esp.andq q3, q0, q6
    esp.andq q4, q1, q6
    esp.vadd.u16 q3, q3, q4
    esp.vmin.u16 q3, q3, q6
    esp.andq q0, q0, q7
    esp.andq q1, q1, q7
    esp.vadd.u16 q0, q0, q1
    esp.vmin.u16 q0, q0, q7
    esp.orq q0, q0, q2
    esp.orq q0, q0, q3
    .endm

    .global astra_add_p4
    .type astra_add_p4,@function
astra_add_p4:
    addi sp, sp, -16
    li t0, 0xF800
    sh t0, 0(sp)
    li t0, 0x07E0
    sh t0, 2(sp)
    li t0, 0x001F
    sh t0, 4(sp)
    mv a3, sp
    esp.vldbc.16.ip q5, a3, 2
    esp.vldbc.16.ip q6, a3, 2
    esp.vldbc.16.ip q7, a3, 0
    mv a3, a0
.Ladd_loop:
    esp.vld.128.ip q1, a1, 16
    esp.max.u16.a q1, a4
    beqz a4, .Ladd_empty
    esp.vld.128.ip q0, a0, 16
    add565
    esp.vst.128.ip q0, a3, 16
    j .Ladd_next
.Ladd_empty:
    addi a0, a0, 16
    addi a3, a3, 16
.Ladd_next:
    addi a2, a2, -8
    bnez a2, .Ladd_loop
    addi sp, sp, 16
    ret
    .size astra_add_p4, .-astra_add_p4

    .global astra_mix_add_p4
    .type astra_mix_add_p4,@function
astra_mix_add_p4:
    addi sp, sp, -32
    esp.movx.r.sar a5
    sw a5, 28(sp)
    li a5, 0
    esp.movx.w.sar a5
    li t0, 32
    sub t0, t0, a4
    sh t0, 0(sp)
    sh a4, 2(sp)
    li t0, 0xF800
    sh t0, 4(sp)
    li t0, 0x07E0
    sh t0, 6(sp)
    li t0, 0x001F
    sh t0, 8(sp)
.Lmix_loop:
    mv a5, sp
    esp.vldbc.16.ip q5, a5, 2
    esp.vldbc.16.ip q6, a5, 2
    esp.vld.128.ip q0, a1, 16
    esp.vld.128.ip q1, a2, 16
    esp.orq q2, q0, q1
    esp.max.u16.a q2, a4
    beqz a4, .Lmix_empty

    // Use explicitly unsigned shifts; VSLD's arithmetic shifts sign-extend red.
    li a4, 11
    esp.movx.w.sar a4
    esp.vsr.u32 q3, q0
    esp.vsr.u32 q4, q1
    addi a5, sp, 8
    esp.vldbc.16.ip q7, a5, 0
    esp.andq q3, q3, q7
    esp.andq q4, q4, q7
    li a4, 0
    esp.movx.w.sar a4
    esp.vmul.u16 q3, q3, q5
    esp.vmul.u16 q4, q4, q6
    esp.vadd.u16 q3, q3, q4
    li a4, 5
    esp.movx.w.sar a4
    esp.vsr.u32 q3, q3
    esp.andq q3, q3, q7
    li a4, 11
    esp.movx.w.sar a4
    esp.vsl.32 q2, q3

    // Green products fit 16 bits even without normalizing (2016 * 32).
    addi a5, sp, 6
    esp.vldbc.16.ip q7, a5, 0
    esp.andq q3, q0, q7
    esp.andq q4, q1, q7
    li a4, 0
    esp.movx.w.sar a4
    esp.vmul.u16 q3, q3, q5
    esp.vmul.u16 q4, q4, q6
    esp.vadd.u16 q3, q3, q4
    li a4, 5
    esp.movx.w.sar a4
    esp.vsr.u32 q3, q3
    esp.andq q3, q3, q7
    esp.orq q2, q2, q3

    addi a5, sp, 8
    esp.vldbc.16.ip q7, a5, 0
    esp.andq q3, q0, q7
    esp.andq q4, q1, q7
    li a4, 0
    esp.movx.w.sar a4
    esp.vmul.u16 q3, q3, q5
    esp.vmul.u16 q4, q4, q6
    esp.vadd.u16 q3, q3, q4
    li a4, 5
    esp.movx.w.sar a4
    esp.vsr.u32 q3, q3
    esp.andq q3, q3, q7
    esp.orq q1, q2, q3

    addi a5, sp, 4
    esp.vldbc.16.ip q5, a5, 2
    esp.vldbc.16.ip q6, a5, 2
    esp.vldbc.16.ip q7, a5, 0
    esp.vld.128.ip q0, a0, 0
    add565
    esp.vst.128.ip q0, a0, 16
    j .Lmix_next
.Lmix_empty:
    addi a0, a0, 16
.Lmix_next:
    addi a3, a3, -8
    bnez a3, .Lmix_loop
    lw a5, 28(sp)
    esp.movx.w.sar a5
    addi sp, sp, 32
    ret
    .size astra_mix_add_p4, .-astra_mix_add_p4
