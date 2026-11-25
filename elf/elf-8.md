# ELF 的一些探索（八）：用于堆栈回溯与 GDB 调试的段

上一篇我们的真实 C 程序已经在 Linux 内核和 Glibc 的托举下跑起来了。但是只要写代码，就逃不开两件事：**程序崩溃时打印函数调用栈（堆栈回溯，Stack Unwinding）**，以及**平时开发时用 GDB 打断点、单步执行、查看当前到 C 代码哪一行了、查看局部变量的值是多少**。

在第四篇中我们介绍过 `.symtab` 和 `.strtab`，它们记录了全局符号和局部标号对应的虚拟地址。但细想一下，光有 `.symtab` 远远不够：
1. 当程序在 `0x2080` 地址崩溃时，CPU 手里只有当前的 `%rip` 和 `%rsp`，它怎么一层一层倒推出是谁调用了当前函数、上一层函数的寄存器原来是多少？
2. 我们在第一篇就说过，C 代码编译成二进制后，**“内存中的数据本身是平凡的，我们在 C 中一再强调的数据类型概念荡然无存了”**，局部变量也仅仅变成了栈帧上的一个偏移量（比如 `-20(%rbp)`）。那为什么 GDB 却能清楚地知道局部变量的名字叫 `a`、类型是 `int`、定义在 `dynamic.c` 的第 6 行第 9 列？

为了实现这两大超能力，ELF 文件专门设计了 **`.eh_frame` 家族** 与 **`.debug_*`（DWARF）家族**。我们一个一个来拆解。

---

## 1. 堆栈回溯神器：`.eh_frame_hdr` 与 `.eh_frame`

### 1.1 为什么不能只靠 `rbp`（`fp`）回溯堆栈？
比如说我们的程序 `A` 函数调用 `B` 函数，`B` 函数调用 `C` 函数，`C` 函数在执行到 `0x2080` 的时候出现了异常坏掉了，这个时候我们需要返回堆栈回溯信息；或者我们在做 GDB 调试运行到 `0x2080` 时敲下 `bt`（backtrace）想看调用栈信息，这也需要堆栈回溯。

有看过第六篇的小伙伴可能会说：我们在第六篇不是讲过每个函数开头都会把上一层函数的 `fp`（在 x86-64 中叫 `rbp`）保存在栈帧里吗？顺着 `rbp` 的控制链一路往上摸，不就能回溯所有堆栈了吗？

在最简单的场景下确实没问题，但在真实工程里光靠 `rbp` 有三个致命缺陷：
1. **我们的堆栈不一定有 `rbp` 的排布方式**：现代编译器如果开启了优化（比如 `-O2` 或 `-fomit-frame-pointer`），为了省出一个通用寄存器，根本就不会用 `rbp` 做栈底指针，而是全程直接用 `rsp` 加偏移来访问局部变量，`rbp` 链直接就断了！
2. **无法恢复其他寄存器**：在异常处理（如 C++ 的 `try-catch` 栈展开）或 GDB 切换栈帧时，我们不仅需要知道栈底在哪，还需要恢复保存在堆栈中的其他各种 callee-saved 寄存器（如 `rbx`、`r12~r15`）的值，光看 `rbp` 根本不知道这些寄存器存在栈帧的哪个位置。
3. **同一个函数在不同指令位置处，栈布局是动态变化的**：刚进入函数第一条指令时还没压入 `rbp`；执行完 `push %rbp` 后栈顶往下移了 8 字节；执行完 `leave` 后栈又恢复了。因为压栈出栈是一个动态的过程，处于同一个函数的不同指令地址时，计算栈底的公式是不一样的！

因此，我们需要一个专门的段来**记录每个函数在不同指令位置下是如何保存和修改堆栈的**，这个段就是 **`.eh_frame`**，此外还需要一个 **`.eh_frame_hdr`** 来记录 `.eh_frame` 的快速检索管理信息。

### 1.2 `.eh_frame_hdr` 二进制结构逐字节解析
我们先用 `readelf -x` 把 `./dynamic` 程序中的 `.eh_frame_hdr`（起始虚拟地址 `0x2018`）和 `.eh_frame`（起始虚拟地址 `0x2050`）导出来看看：

```text
Contents of section .eh_frame_hdr:
 2018 011b033b 34000000 05000000 08f0ffff  ...;4...........
 2028 68000000 38f0ffff 90000000 48f0ffff  h...8.......H...
 2038 a8000000 68f0ffff 50000000 51f1ffff  ....h...P...Q...
 2048 c0000000

Contents of section .eh_frame:
 2050 14000000 00000000 017a5200 01781001  .........zR..x..
 2060 1b0c0708 90010000 14000000 1c000000  ................
 2070 10f0ffff 26000000 00440710 00000000  ....&....D......
 2080 24000000 34000000 98efffff 30000000  $...4.......0...
 2090 000e1046 0e184a0f 0b770880 003f1a3a  ...F..J..w...?.:
 20a0 2a332422 00000000 14000000 5c000000  *3$"........\...
 20b0 a0efffff 10000000 00000000 00000000  ................
 20c0 14000000 74000000 98efffff 20000000  ....t....... ...
 20d0 00000000 00000000 1c000000 8c000000  ................
 20e0 89f0ffff 61000000 00450e10 8602430d  ....a....E....C.
 20f0 0602580c 07080000 00000000           ..X......... 
```

我们来像拆盲盒一样把 `.eh_frame_hdr`（`0x2018 ~ 0x204b`）的每一个 4 字节（小端序）彻底算清楚：
1. **第 1 个 4 字节（`0x2018`: `01 1b 03 3b`）**：版本与指针编码方式等头部元信息。
2. **第 2 个 4 字节（`0x201c`: `34 00 00 00` -> `0x34`）**：表示当前这个字（`0x201c`）相对于 `.eh_frame` 段首地址的偏移量。我们来验算一下：`0x2018 + 0x4 + 0x34 = 0x2050`，正好就是下面 `.eh_frame` 段的起始地址！
3. **第 3 个 4 字节（`0x2020`: `05 00 00 00` -> `5`）**：表示 `.eh_frame` 中包含的 **FDE（Frame Description Entry，函数栈帧描述表项）的个数**，这里是 `5`。
   * 也就是说，`.eh_frame` 总共描述了 5 个函数的堆栈信息。我们对照地址可以查出这 5 个函数分别是：**`_start`、`.plt`、`main`、`.plt.got`、`.plt.sec`**。
   * 有同学会问：不对啊，符号表里不是还有 `.init`、`.fini`、`register_tm_clones` 那些函数吗？为什么不是每一个函数都能享受 `.eh_frame` 的待遇？
   * 因为真正的用户程序调用链是 `_start -> __libc_start_main -> __libc_start_call_main -> main -> .plt.sec / .plt.got -> .plt`，而 `.init` 和 `.fini` 在调用的时候并不处于 `main` 函数的执行期内，不属于用户的 C 业务逻辑的一部分，自然没有必要浪费空间被用户跟踪和管理。
4. **随后的 10 个 4 字节（2 个一组，共 5 组，对应 5 个 FDE 的二分查找表）**：
   * 每一组的**第 1 个 4 字节**是**该函数起始地址相对于 `.eh_frame_hdr` 首地址（`0x2018`）的有符号偏移量**；
   * 每一组的**第 2 个 4 字节**是**该函数对应的 FDE 在 `.eh_frame` 中的地址相对于 `.eh_frame_hdr` 首地址（`0x2018`）的偏移量**。
   * 我们拿第一组（`0x2024`: `08 f0 ff ff` 和 `0x2028`: `68 00 00 00`）来验算一下：
     * 函数起始地址 = `0x2018 + 0xfffff008`（即 `-0xff8`）= **`0x1020`**，去查前面的 Section Headers，正好是 **`.plt` 段的入口地址**！
     * 对应的 FDE 地址 = `0x2018 + 0x68` = **`0x2080`**，直接就定位到了 `.eh_frame` 内部 `0x2080` 处的 FDE！

```text
+-----------------------------------------------------------------------------+
|                .eh_frame_hdr (0x2018) 头部与 FDE 查找表结构                 |
+-----------------------------------------------------------------------------+
| 0x2018: [Version/Enc: 01 1b 03 3b]                                          |
| 0x201c: [eh_frame_ptr: 0x00000034] ===> 0x201c + 0x34 = 0x2050 (.eh_frame)  |
| 0x2020: [fde_count   : 0x00000005] (共描述 5 个函数)                        |
+-----------------------------------------------------------------------------+
| [二分查找表: 5 组 (initial_loc_offset, fde_offset)]                         |
|  #1 (.plt)    : func = 0x2018 + 0xfffff008 = 0x1020 | FDE = 0x2018+0x68=0x2080|
|  #2 (.plt.got): func = 0x2018 + 0xfffff038 = 0x1050 | FDE = 0x2018+0x90=0x20a8|
|  #3 (.plt.sec): func = 0x2018 + 0xfffff048 = 0x1060 | FDE = 0x2018+0xa8=0x20c0|
|  #4 (_start)  : func = 0x2018 + 0xfffff068 = 0x1080 | FDE = 0x2018+0x50=0x2068|
|  #5 (main)    : func = 0x2018 + 0xfffff151 = 0x1169 | FDE = 0x2018+0xc0=0x20d8|
+-----------------------------------------------------------------------------+
```

### 1.3 `.eh_frame` 与 CFI 伪指令推演
找到了函数对应的 FDE 之后，`.eh_frame` 又是怎么记录这个函数在不同指令位置下的堆栈变化的呢？

原理其实很巧妙：GCC 在把 C 代码编译成汇编的时候，除了生成正常的 asm 指令之外，还会在每次堆栈发生变化或寄存器压入堆栈的指令旁边，自动插入一些 **`.cfi_*`（Call Frame Information）伪指令**。汇编器在编译时，就会把这些伪指令的信息编码保存到 `.eh_frame` 段中，记录下“哪个指令地址出现了什么伪指令、把栈改成了什么样”。

在做堆栈回溯时，有一个核心基准地址叫做 **CFA（Canonical Frame Address，规范帧地址/栈底基准点）**，通常定义为**上一层调用者执行 `call` 指令压入返回地址之前的那个 `%rsp` 位置**（在表格中用 `c` 表示）。既然 x86-64 的 `call` 指令会自动把 8 字节的返回地址（`rip` / `ra`）压入栈中，那么刚进入被调用函数第一条指令时，上一层的栈顶 CFA 自然就在当前 **`rsp + 8`** 的位置，而返回地址 `ra` 就保存在 **`c - 8`**（即当前的 `rsp + 0`）处！

常见的 CFI 伪指令包括：
* **`.cfi_startproc`** / **`.cfi_endproc`**：标志着一个函数 FDE 的开始与结束。
* **`.cfi_def_cfa_offset offset`**：修改计算 CFA 的公式 `CFA = reg + offset` 中的 `offset` 值（保持 `reg` 不变）。比如执行了一条 `push %rbp` 使得 `rsp` 变小了 8，为了指回同一个栈底 CFA，`offset` 就要跟着加上 8（从原来的 `8` 变成 `16`，即 `CFA = rsp + 16`）。
* **`.cfi_def_cfa_register register`**：修改计算 CFA 的公式 `CFA = reg + offset` 中的基址寄存器 `reg`（保持 `offset` 不变）。比如执行了 `mov %rsp, %rbp` 后，把计算基准寄存器从 `rsp` 换成 `rbp`。
* **`.cfi_offset register, offset`**：指出在 `CFA + offset`（即 `c + offset`）的内存位置处保存了寄存器 `register` 的旧值。比如 `push %rbp` 之后插入 `.cfi_offset rbp, -16`，说明在 `c - 16` 的地方存了上一层函数的 `rbp`，便于回溯时恢复它。
* **`.cfi_def_cfa register, offset`**：同时修改 `CFA = reg + offset` 中的寄存器 `reg` 和偏移量 `offset`。

我们直接来看我们程序中 `main` 函数（`0x1169 ~ 0x11ca`）的反汇编以及穿插其中的 CFI 伪指令：

```text
0000000000001169 <main>:
  .cfi_startproc                # 开始当前 FDE，刚执行完 call 压入 rip，故 CFA = rsp + 8，ra 在 c - 8
    1169:   f3 0f 1e fa             endbr64 
    116d:   55                      push   %rbp
  .cfi_def_cfa_offset 16        # 压入 rbp 使 rsp 减 8，故 offset 加 8 变为 16 (CFA = rsp + 16)
  .cfi_offset rbp, -16          # rbp 被压在当前 rsp 处，即 c - 16 = (rsp + 16) - 16 = rsp
    116e:   48 89 e5                mov    %rsp,%rbp
  .cfi_def_cfa_register rbp     # rbp 等于当前 rsp，基准寄存器改为 rbp (CFA = rbp + 16)
                                # 此后无论 rsp 怎么 sub 开辟空间，都不影响 rbp + 16 找到栈底！
    1171:   48 83 ec 30             sub    $0x30,%rsp
    1175:   89 7d ec                mov    %edi,-0x14(%rbp)
    1178:   48 89 75 e0             mov    %rsi,-0x20(%rbp)
    ...
    11be:   e8 9d fe ff ff          call   1060 <printf@plt>
    11c3:   b8 00 00 00 00          mov    $0x0,%eax
    11c8:   c9                      leave 
  .cfi_def_cfa rsp, 8           # leave 恢复了 rsp 和 rbp，此时栈顶只剩返回地址，CFA 变回 rsp + 8
    11c9:   c3                      ret 
  .cfi_endproc                  # 结束当前 FDE
```

汇编器把这些伪指令编码进 `.eh_frame` 后，我们用 `readelf -wf ./dynamic` 可以看到保存在 FDE 里的 DWARF CFA 状态机指令：

```text
00000088 000000000000001c 0000008c FDE cie=00000000 pc=0000000000001169..00000000000011ca
  DW_CFA_advance_loc: 5 to 000000000000116e   # cfi 指令生效的地址 (0x1169 + 5 = 0x116e)
  DW_CFA_def_cfa_offset: 16
  DW_CFA_offset: r6 (rbp) at cfa-16           # 生效的 cfi 指令内容
  DW_CFA_advance_loc: 3 to 0000000000001171   # 前进 3 字节到 0x1171
  DW_CFA_def_cfa_register: r6 (rbp)
  DW_CFA_advance_loc1: 88 to 00000000000011c9 # 前进 88 字节到 0x11c9
  DW_CFA_def_cfa: r7 (rsp) ofs 8
  DW_CFA_nop
  DW_CFA_nop                                  # 最后用 00 对齐
```

如果用 `readelf -wF ./dynamic`，则可以直接看到展开后的指令区间堆栈状态表：

```text
00000088 000000000000001c 0000008c FDE cie=00000000 pc=0000000000001169..00000000000011ca
   LOC           CFA      rbp   ra    
0000000000001169 rsp+8    u     c-8   
000000000000116e rsp+16   c-16  c-8   
0000000000001171 rbp+16   c-16  c-8   
00000000000011c9 rsp+8    c-16  c-8 
```

有了这张表，**堆栈回溯的迭代过程**就清晰得像做小学算术题一样了：

```text
+-----------------------------------------------------------------------------+
|                    基于 .eh_frame 的单步堆栈回溯算法流程                    |
+-----------------------------------------------------------------------------+
| 假设程序在 IP = 0x1170 处暂停或崩溃，当前寄存器值为 RSP_0, RBP_0:            |
|                                                                             |
|  1. 查 .eh_frame_hdr 二分表: 0x1170 落在 main (0x1169..0x11ca) 的 FDE 中    |
|  2. 执行该 FDE 的 CFA 指令直到 IP <= 0x1170 (只算前两行，停在 LOC 0x116e):  |
|       * 得到当前栈底 CFA: c = RSP_0 + 16                                    |
|       * 恢复上一层函数的 RBP: RBP_prev = *(c - 16)                          |
|       * 恢复上一层函数的返回地址 (即上一层调用点 IP): IP_prev = *(c - 8)    |
|       * 恢复上一层函数的栈顶 RSP: RSP_prev = c                              |
|  3. 拿着恢复出来的 (IP_prev, RSP_prev, RBP_prev)，再次去 .eh_frame_hdr      |
|     查找上一层函数的 FDE，不断循环迭代，即可还原整条调用链的所有栈帧！      |
+-----------------------------------------------------------------------------+
```

---

## 2. GDB 源码级调试的秘密：`.debug_*`（DWARF）段全解析

`.eh_frame` 是哪怕不加 `-g` 编译选项也会默认生成的（因为 C++ 异常处理和崩溃栈回溯都要用它）。而当我们在 `gcc` 编译时加上 **`-g`** 选项后，ELF 文件里就会多出一组以 `.debug_` 开头的 DWARF 格式调试段，专门用来支撑 GDB 的源码级调试。

### 2.1 `.debug_str` 与 `.debug_info`（编译器的语法树快照）
首先是 `.debug_str` 段，它是一个字符串池，保存了 `.debug_info` 需要用到的所有长字符串（如数据类型名、变量名、函数名、编译选项等），`.debug_info` 里很多名字字段存的其实就是 `.debug_str` 中的偏移量：

```text
Contents of section .debug_str:
 0000 6c6f6e67 206c6f6e 6720696e 74007369  long long int.si
 0010 7a655f74 00656e76 7000474e 55204331  ze_t.envp.GNU C1
 0020 37203131 2e332e30 202d6d74 756e653d  7 11.3.0 -mtune=
 0030 67656e65 72696320 2d6d6172 63683d78  generic -march=x
 0040 38362d36 34202d67 202d6661 73796e63  86-64 -g -fasync
 0050 68726f6e 6f75732d 756e7769 6e642d74  hronous-unwind-t
 0060 61626c65 73202d66 73746163 6b2d7072  ables -fstack-pr
 0070 6f746563 746f722d 7374726f 6e67202d  otector-strong -
 0080 66737461 636b2d63 6c617368 2d70726f  fstack-clash-pro
 0090 74656374 696f6e20 2d666366 2d70726f  tection -fcf-pro
 00a0 74656374 696f6e00 6c6f6e67 206c6f6e  tection.long lon
 00b0 6720756e 7369676e 65642069 6e740075  g unsigned int.u
 00c0 6e736967 6e656420 63686172 00617267  nsigned char.arg
 00d0 63007374 726c656e 00766973 69745f74  c.strlen.visit_t
 00e0 696d6500 73686f72 7420756e 7369676e  ime.short unsign
 00f0 65642069 6e740070 72696e74 66006172  ed int.printf.ar
 0100 6776006d 61696e00 73686f72 7420696e  gv.main.short in
 0110 7400                                 t.           
```

而最核心的 **`.debug_info` 段** 记录了所有的调试信息，里面包含了文件信息、类型定义、函数定义、变量定义等。最精妙的是，它是**分层次嵌套定义**的（`<0>` -> `<1>` -> `<2>`），和编译器的抽象语法树（AST）高度一致，相当于直接把编译器前端语义分析得到的结构保留到 ELF 文件里了！

我们用 `readelf -wi ./dynamic` 顺着它的三个嵌套层次来看：

#### 第 `<0>` 层：根节点（编译单元 Compilation Unit）
```text
  Compilation Unit @ offset 0x0:
   Length:        0x124 (32-bit)
   Version:       5
   Unit Type:     DW_UT_compile (1)
   Abbrev Offset: 0x0
   Pointer Size:  8
 <0><c>: Abbrev Number: 5 (DW_TAG_compile_unit)
    <d>   DW_AT_producer    : (indirect string, offset: 0x1a): GNU C17 11.3.0 -mtune=generic -march=x86-64 -g ...
    <11>  DW_AT_language    : 29 (C11)
    <12>  DW_AT_name        : (indirect line string, offset: 0x0): dynamic.c
    <16>  DW_AT_comp_dir    : (indirect line string, offset: 0xa): /home/zyy/system_security/Env
    <1a>  DW_AT_low_pc      : 0x1169
    <22>  DW_AT_high_pc     : 0x61
    <2a>  DW_AT_stmt_list   : 0x0
```
`<0><c>` 中的 `0` 是嵌套层次，`c`（`0xc`）是该节点在 `.debug_info` 段内的起始字节偏移。第 0 层根节点保存了整个源文件的全局信息：编译选项（`DW_AT_producer`）、编程语言（`DW_AT_language`: C11）、源文件名（`dynamic.c`）、编译目录（`/home/zyy/system_security/Env`）、代码起始 PC（`0x1169`）与代码长度（`0x61`）。

#### 第 `<1>` 层：全局定义节点（类型、`typedef`、修饰符、全局变量、函数）
在根节点下，第 1 层依次定义了该文件中出现的所有全局符号和类型：

1. **`typedef` 节点（`DW_TAG_typedef`）与基本类型节点（`DW_TAG_base_type`）**：
   ```text
    <1><2e>: Abbrev Number: 6 (DW_TAG_typedef)
         <2f>   DW_AT_name        : (indirect string, offset: 0xe): size_t
         <33>   DW_AT_decl_file   : 3
         <34>   DW_AT_decl_line   : 209
         <35>   DW_AT_decl_column : 23
         <36>   DW_AT_type        : <0x3a>
    <1><3a>: Abbrev Number: 1 (DW_TAG_base_type)
         <3b>   DW_AT_byte_size   : 8
         <3c>   DW_AT_encoding    : 7 (unsigned)
         <3d>   DW_AT_name        : (indirect string, offset: 0xad): long unsigned int
   ```
   你看，`<1><2e>` 记录了 `size_t` 这个 `typedef` 是在第 3 号文件（后面 `.debug_line` 会看到是 `stddef.h`）的第 209 行第 23 列定义的，它指向的目标类型是 `<0x3a>`——往下一看，偏移 `0x3a` 处的节点正好是 8 字节无符号的基本类型 `long unsigned int`！如果类型名很短（比如 `int`、变量名 `a`，4 字节以内）会直接内联存储，名字比较长存不下时就存 `.debug_str` 的偏移量。
2. **`const` 与指针等复合类型节点**：
   ```text
    <1><79>: Abbrev Number: 8 (DW_TAG_const_type)
         <7a>   DW_AT_type        : <0x72>
    <1><122>: Abbrev Number: 4 (DW_TAG_pointer_type)
         <123>  DW_AT_byte_size   : 8
         <123>  DW_AT_type        : <0xb0>
   ```
   像搭积木一样：`<0x72>` 是 `char`，那么指向 `<0x72>` 的 `DW_TAG_const_type` 就是 `const char`；同理，指向 `char*`（`<0xb0>`）的 `DW_TAG_pointer_type` 就是 `char**`！
3. **全局变量节点（`DW_TAG_variable`）**：
   ```text
    <1><8c>: Abbrev Number: 9 (DW_TAG_variable)
         <8d>   DW_AT_name        : (indirect string, offset: 0xd9): visit_time
         <91>   DW_AT_decl_file   : 1
         <92>   DW_AT_decl_line   : 3
         <93>   DW_AT_decl_column : 12
         <94>   DW_AT_type        : <0x64>
         <98>   DW_AT_external    : 1
         <98>   DW_AT_declaration : 1
   ```
   记录了变量 `visit_time` 在第 1 号文件（`dynamic.c`）第 3 行第 12 列声明，类型为 `<0x64>`（`int`），`DW_AT_external` + `DW_AT_declaration` 表示它是外部引入、本地仅做 `extern` 声明的变量。
4. **函数节点（`DW_TAG_subprogram`）**：
   ```text
    <1><cb>: Abbrev Number: 13 (DW_TAG_subprogram)
         <cc>   DW_AT_external    : 1
         <cc>   DW_AT_name        : (indirect string, offset: 0x103): main
         <d0>   DW_AT_decl_file   : 1
         <d1>   DW_AT_decl_line   : 5
         <d2>   DW_AT_decl_column : 5
         <d3>   DW_AT_prototyped  : 1
         <d3>   DW_AT_type        : <0x64>
         <d7>   DW_AT_low_pc      : 0x1169
         <df>   DW_AT_high_pc     : 0x61
         <e7>   DW_AT_frame_base  : 1 byte block: 9c   (DW_OP_call_frame_cfa)
         <e9>   DW_AT_call_all_tail_calls: 1
         <e9>   DW_AT_sibling     : <0x122>
   ```
   描述了 `main` 函数的返回类型（`<0x64>` 即 `int`）、代码起止范围（`0x1169`，长度 `0x61`）、下一个兄弟节点位置（`sibling: <0x122>`），以及最关键的 **`DW_AT_frame_base: 9c (DW_OP_call_frame_cfa)`**——告诉 GDB：这个函数内部所有局部变量的栈偏移，都以我们在 `.eh_frame` 里算出来的那个 **CFA** 作为栈底基准！

#### 第 `<2>` 层：函数内部嵌套（形参 `formal_parameter` 与局部变量 `variable`）
进入 `main` 函数节点内部，嵌套层次变成了 `<2>`：

```text
      <2><ed>: Abbrev Number: 2 (DW_TAG_formal_parameter)
         <ee>   DW_AT_name        : (indirect string, offset: 0xcd): argc
         <f2>   DW_AT_decl_file   : 1
         <f2>   DW_AT_decl_line   : 5
         <f2>   DW_AT_decl_column : 14
         <f3>   DW_AT_type        : <0x64>
         <f7>   DW_AT_location    : 2 byte block: 91 5c   (DW_OP_fbreg: -36)
      ...
      <2><114>: Abbrev Number: 14 (DW_TAG_variable)
         <115>   DW_AT_name        : a
         <117>   DW_AT_decl_file   : 1
         <118>   DW_AT_decl_line   : 6
         <119>   DW_AT_decl_column : 9
         <11a>   DW_AT_type        : <0x64>
         <11e>   DW_AT_location    : 2 byte block: 91 6c   (DW_OP_fbreg: -20)
      <2><121>: Abbrev Number: 0
```

这里最精彩的是 **`DW_AT_location`** 字段：
* 第一个字节 `0x91` 代表位置类型是 `DW_OP_fbreg`（即相对于 `frame_base` CFA 的偏移量）。
* 第二个字节是 **SLEB128 编码的有符号偏移量**：对于 `argc` 是 `0x5c`（十进制 92，减去 $2^7=128$ 正好等于 **`-36`**！）；对于局部变量 `a` 是 `0x6c`（十进制 108，减去 128 正好等于 **`-20`**！）。
* 我们回看上一节 `main` 的汇编：`mov %edi, -0x14(%rbp)`（`-0x14` 就是 `-20`，而 `rbp = CFA - 16`，所以 `argc` 在 `rbp - 0x14 = CFA - 36`；局部变量 `a` 在 `rbp - 4 = CFA - 20`）！最后一个 `Abbrev Number: 0` 空节点标志着当前函数 `<2>` 嵌套层次的结束。

```text
+-----------------------------------------------------------------------------+
|                .debug_info 抽象语法树 (DIE 层级结构示意图)                  |
+-----------------------------------------------------------------------------+
| <0> DW_TAG_compile_unit (dynamic.c, low_pc=0x1169, high_pc=0x61)            |
|  |                                                                          |
|  +-- <1> DW_TAG_typedef (size_t) ---------> 指向 <0x3a>                     |
|  +-- <1> DW_TAG_base_type <0x3a> (long unsigned int, size=8)                |
|  +-- <1> DW_TAG_base_type <0x64> (int, size=4)                              |
|  +-- <1> DW_TAG_const_type <0x79> --------> 指向 <0x72> (char)              |
|  +-- <1> DW_TAG_variable (visit_time, external=1, type=<0x64>)              |
|  +-- <1> DW_TAG_subprogram (main, frame_base=CFA, type=<0x64>)              |
|  |    |                                                                     |
|  |    +-- <2> DW_TAG_formal_parameter (argc, type=<0x64>, loc=CFA-36 [91 5c])|
|  |    +-- <2> DW_TAG_formal_parameter (argv, type=char**, loc=CFA-48)       |
|  |    +-- <2> DW_TAG_variable (a, line=6, type=<0x64>, loc=CFA-20 [91 6c])  |
|  |    +-- <2> Abbrev Number: 0 (当前层结束标志)                             |
|  |                                                                          |
|  +-- <1> DW_TAG_pointer_type <0x122> (char**, size=8)                       |
+-----------------------------------------------------------------------------+
```

### 2.2 `.debug_aranges` 与 `.debug_abbrev`（数据压缩模板）
1. **`.debug_aranges`（编译单元地址映射表）**：
   一个大型程序由很多个 `.c` 文件（编译单元）组成，当 GDB 停在某个指令地址时，先查 `.debug_aranges` 就能立刻知道这个地址属于哪个 `.c` 编译单元：
   ```text
     Length:                   44
     Version:                  2
     Offset into .debug_info:  0x0
     Pointer Size:             8
     Segment Size:             0

       Address            Length
       0000000000001119 0000000000000094 
       0000000000000000 0000000000000000

   Contents of section .debug_aranges:
    0000 2c000000 02000000 00000800 00000000  ,...............
    0010 19110000 00000000 94000000 00000000  ................
    0020 00000000 00000000 00000000 00000000  ................
   ```
   头部 16 字节依次记录了后续长度（`0x2c = 44`，`4 + 44 = 48` 字节）、版本号 `2`、在 `.debug_info` 中的偏移 `0x0`、指针长度 `8`。随后每两个 8 字节是一组 `(Address, Length)`：这里起始地址是 `0x1119`，长度是 `0x94`，最后两个全 `0` 表示数组结束。

2. **`.debug_abbrev`（结构模板定义表）**：
   细心的小伙伴在看刚才的 `.debug_info` 时肯定注意到了，每个节点开头都有一个 **`Abbrev Number`**（比如 `main` 是 `13`，`argc` 是 `2`）。这是干什么用的？
   如果 `.debug_info` 里的每个节点都把属性名和属性编码格式存一遍，调试段会无比臃肿。所以 DWARF 把**“每种节点包含哪些属性字段、每个字段是什么数据类型（Form）”**抽出来定义在 **`.debug_abbrev`** 段中，而 `.debug_info` 里只存压缩后的纯数值！
   ```text
     Number TAG (0x0)
      1      DW_TAG_base_type    [no children]
       DW_AT_byte_size    DW_FORM_data1
       DW_AT_encoding     DW_FORM_data1
       DW_AT_name         DW_FORM_strp
       DW_AT value: 0     DW_FORM value: 0
      2      DW_TAG_formal_parameter    [no children]
       DW_AT_name         DW_FORM_strp
       DW_AT_decl_file    DW_FORM_implicit_const: 1
       DW_AT_decl_line    DW_FORM_implicit_const: 5
       DW_AT_decl_column  DW_FORM_data1
       DW_AT_type         DW_FORM_ref4
       DW_AT_location     DW_FORM_exprloc
       DW_AT value: 0     DW_FORM value: 0
      ...
      5      DW_TAG_compile_unit    [has children]
       DW_AT_producer     DW_FORM_strp
       DW_AT_language     DW_FORM_data1
       DW_AT_name         DW_FORM_line_strp
       DW_AT_comp_dir     DW_FORM_line_strp
       DW_AT_low_pc       DW_FORM_addr
       DW_AT_high_pc      DW_FORM_data8
       DW_AT_stmt_list    DW_FORM_sec_offset
       DW_AT value: 0     DW_FORM value: 0
   ```
   解析器在读取 `.debug_info` 时，先读出 `Abbrev Number`（比如 `1`），去 `.debug_abbrev` 里查第 1 号模板，就知道接下来紧跟着的 1 字节是 `byte_size`（`DW_FORM_data1`）、再 1 字节是 `encoding`（`DW_FORM_data1`）、再 4 字节是 `.debug_str` 的字符串偏移（`DW_FORM_strp`），从而完美解码压缩后的数据！

### 2.3 `.debug_line_str` 与 `.debug_line`（汇编地址与 C 代码行号的映射）
最后，当我们在 GDB 里使用 `n`（下一行 C 代码）或者 `si`（下一条汇编指令）时，GDB 怎么知道哪几条汇编指令对应 C 代码的第几行呢？答案就在 **`.debug_line_str`** 和 **`.debug_line`** 段中：

```text
Hex dump of section '.debug_line_str':
  0x00000000 64796e61 6d69632e 63002f68 6f6d652f dynamic.c./home/
  0x00000010 7a79792f 73797374 656d5f73 65637572 zyy/system_secur
  0x00000020 6974792f 456e7600 2f757372 2f696e63 ity/Env./usr/inc
  0x00000030 6c756465 002f7573 722f6c69 622f6763 lude./usr/lib/gc
  0x00000040 632f7838 365f3634 2d6c696e 75782d67 c/x86_64-linux-g
  0x00000050 6e752f31 312f696e 636c7564 65007374 nu/11/include.st
  0x00000060 64696f2e 68007374 64646566 2e6800   dio.h.stddef.h.
```

再看 `readelf -wl ./dynamic` 解析出的 `.debug_line` 内容：

```text
 The Directory Table (offset 0x22, lines 3, columns 1):
  Entry Name
  0     (indirect line string, offset: 0xa): /home/zyy/system_security/Env
  1     (indirect line string, offset: 0x28): /usr/include
  2     (indirect line string, offset: 0x35): /usr/lib/gcc/x86_64-linux-gnu/11/include

 The File Name Table (offset 0x34, lines 4, columns 2):
  Entry Dir     Name
  0     0       (indirect line string, offset: 0x0): dynamic.c
  1     0       (indirect line string, offset: 0x0): dynamic.c
  2     1       (indirect line string, offset: 0x5e): stdio.h
  3     2       (indirect line string, offset: 0x66): stddef.h

 Line Number Statements:
  [0x00000048]  Set column to 57
  [0x0000004a]  Extended opcode 2: set Address to 0x1169
  [0x00000055]  Special opcode 9: advance Address by 0 to 0x1169 and Line by 4 to 5
  [0x00000056]  Set column to 11
  [0x00000058]  Advance PC by constant 17 to 0x117a
  [0x00000059]  Special opcode 90: advance Address by 6 to 0x1180 and Line by 1 to 6
  [0x0000005a]  Set column to 9
  [0x0000005c]  Special opcode 215: advance Address by 15 to 0x118f and Line by 0 to 6
  [0x0000005d]  Set column to 7
  [0x0000005f]  Special opcode 48: advance Address by 3 to 0x1192 and Line by 1 to 7
  [0x00000060]  Set column to 6
  [0x00000062]  Special opcode 215: advance Address by 15 to 0x11a1 and Line by 0 to 7
  [0x00000063]  Set column to 5
  [0x00000065]  Special opcode 48: advance Address by 3 to 0x11a4 and Line by 1 to 8
  [0x00000066]  Set column to 1
  [0x00000068]  Advance PC by 36 to 0x11c8
  [0x0000006a]  Special opcode 6: advance Address by 0 to 0x11c8 and Line by 1 to 9
  [0x0000006b]  Advance PC by 2 to 0x11ca
  [0x0000006d]  Extended opcode 1: End of Sequence
```

这里揭示了两个非常关键的机制：
1. **GDB 是怎么显示 C 源码的？**
   `Directory Table` 和 `File Name Table` 记录了源文件的文件名和所在目录（注意这里的 Entry 序号 `1: dynamic.c`、`2: stdio.h`、`3: stddef.h` 和前面 `.debug_info` 里的 `DW_AT_decl_file` 是严格一致的！）。
   当我们在 GDB 里调试时，GDB 会显示当前行的 C 代码文本。**但其实 ELF 文件里根本没有保存 C 源代码的字符串！** GDB 是根据这里的目录表和文件名表，去硬盘上打开 `/home/zyy/system_security/Env/dynamic.c` 这个外部文件，把对应的第几行文本读出来显示给你看。所以如果你在编译完 ELF 之后，偷偷修改了外部 `.c` 文件的内容，GDB 调试时打印出来的代码内容就会跟着改变！
2. **行号状态机指令（Line Number Statements）**：
   为了极度节省存储空间，`.debug_line` 只保存了**相邻语句之间的地址差值和行号差值**。比如：
   ```text
     [0x00000055]  Special opcode 9: advance Address by 0 to 0x1169 and Line by 4 to 5
     [0x00000059]  Special opcode 90: advance Address by 6 to 0x1180 and Line by 1 to 6
   ```
   这说明从 `0x1169 ~ 0x117f` 的这一段汇编代码对应的是 C 代码的第 5 行；到了 `0x1180` 就进入了 C 代码的第 6 行（依此类推，`0x1180 ~ 0x1191` 对应第 6 行，`0x1192 ~ 0x11a3` 对应第 7 行，`0x11a4 ~ 0x11c7` 对应第 8 行，`0x11c8 ~ 0x11c9` 对应第 9 行）。GDB 在执行 `si`（单步一条汇编）和 `n`（单步一行 C 代码，即连续跑完属于当前行号的所有汇编指令直到跨入下一行地址范围）时，就是靠这张映射表来精准控制的。