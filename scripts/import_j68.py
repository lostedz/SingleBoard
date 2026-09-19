"""Reproduce the small portability patch on a pinned upstream checkout."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / '.upstream-j68'
DST = ROOT / 'rtl' / 'j68'
DST.mkdir(parents=True, exist_ok=True)
names = ['cpu_j68', 'j68_addsub_32', 'j68_alu', 'j68_decode',
         'j68_decode_rom', 'j68_dpram_2048x20', 'j68_flags',
         'j68_loop', 'j68_mem_io', 'j68_test']
for name in names:
    text = (SRC / 'rtl' / (name + '.v')).read_text()
    # Select the upstream behavioral memories for synthesis and simulation.
    if '`ifdef verilator3' in text:
        before, conditional = text.split('`ifdef verilator3', 1)
        inferred, conditional = conditional.split('`else', 1)
        _, after = conditional.split('`endif', 1)
        text = before + inferred + after
    for image in ('j68_ram.mem', 'j68_ram_c.mem', 'j68_dec.mem', 'j68_dec_c.mem'):
        text = text.replace('"' + image + '"', '"rtl/j68/' + image + '"')
    if name == 'cpu_j68':
        text = text.replace('.RAM_INIT_FILE ((USE_CLK_ENA) ? "rtl/j68/j68_ram_c.mem" : "rtl/j68/j68_ram.mem")',
                            '.USE_CLK_ENA (USE_CLK_ENA)')
    if name == 'j68_dpram_2048x20':
        text = text.replace('parameter RAM_INIT_FILE = "rtl/j68/j68_ram.mem";', 'parameter USE_CLK_ENA = 0;')
        text = text.replace('$readmemb(RAM_INIT_FILE, r_mem_blk);',
                            'if (USE_CLK_ENA)\n            $readmemb("rtl/j68/j68_ram_c.mem", r_mem_blk);\n'
                            '        else\n            $readmemb("rtl/j68/j68_ram.mem", r_mem_blk);')
        # Synchronous output reset follows XST block-RAM inference guidance.
        text = text.replace('always@(posedge reset or posedge clock)', 'always@(posedge clock)')
        text = text.replace('reg  [19:0] r_mem_blk', '(* ram_style = "block" *) reg  [19:0] r_mem_blk')
    if name == 'j68_decode_rom':
        text = text.replace('reg  [35:0] r_mem_blk', '(* rom_style = "block" *) reg  [35:0] r_mem_blk')
    text = text.replace('// Testbench', '// Inferred FPGA memory (simulation and synthesis)')
    text = '// Portability adaptation: inferred memories; project-relative init paths.\n' + text
    (DST / (name + '.v')).write_text(text)
for name in ('j68_ram.mem', 'j68_ram_c.mem', 'j68_dec.mem', 'j68_dec_c.mem'):
    shutil.copyfile(SRC / 'rtl' / name, DST / name)
shutil.copyfile(SRC / 'README.md', DST / 'UPSTREAM_README.md')
shutil.copyfile(SRC / 'ucode' / 'j68_ucode.asm', DST / 'j68_ucode.asm')
