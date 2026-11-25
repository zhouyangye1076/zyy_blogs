# ELF 的一些探索（十）：动态链接技术与相关 ELF 段深度解析

在第七篇我们讲了静态链接库（`libc.a`），在第九篇我们讲了位置无关可执行文件（PIE）。现在我们把视野放到整个操作系统上：如果系统里同时运行着 100 个 C 程序，每个程序都用静态链接把 `printf`、`strlen` 等 Glibc 代码各自复印一份塞进自己的可执行文件里，不仅硬盘空间被大量浪费，内存里也会同时躺着 100 份一模一样的 `libc` 代码。更糟糕的是，一旦 `libc` 发现了一个安全漏洞，你必须把电脑上成千上万个程序全部重新编译链接一遍！

为了彻底解决这个痛点，现代操作系统普遍采用**动态链接（Dynamic Linking）**：把公共的库代码单独编译成一个个**共享库文件（`.so`，Shared Object，比如系统的 `libc.so.6` 和我们自己写的 `mylib.so`）**。主程序 `dynamic` 在编译时并不把 `mylib.so` 的代码塞进来，而是在程序启动运行时，由动态链接器把 `mylib.so` 和 `libc.so.6` 一起载入内存，现场把符号绑定起来。

按惯例，我们先别急着背段名，先想一想**把链接推迟到运行时，会面临哪三个核心技术难题**：
1. **跨模块怎么寻址？** 在第九篇的 PIE 里，代码和本模块全局变量在同一个 ELF 文件里，相对距离固定，所以能用 `%rip + offset` 相对寻址。但现在 `strlen` 函数和 `visit_time` 全局变量在另一个文件 `mylib.so` 里！这两个文件在运行时被各自加上不同的随机 `bias` 载入内存，连它们之间的相对距离都是随机不确定的，普通的 PC 相对寻址彻底失效了，汇编指令该怎么写？
2. **几百个外部函数怎么平衡启动速度？** 一个程序可能引用了共享库里成百上千个函数，但一次运行往往只走到其中几个分支。如果在启动时把所有函数一次性全查找绑定完，启动岂不是很慢？能不能等某个函数第一次被调用时再去按需绑定（延迟绑定，Lazy Binding）？
3. **怎么在几十个 `.so` 里极速查找到目标符号？** 绑定一个函数如 `"strlen"` 时，要拿着字符串去一个个共享库的符号表里比对查找，怎样设计数据结构才能让字符串查找快如闪电？

搞懂了这三个问题，你在第七篇 Section Headers 里看到的那十几个动态链接专属段（`.got`、`.plt`、`.plt.got`、`.plt.sec`、`.dynsym`、`.dynstr`、`.rela.dyn`、`.rela.plt`、`.gnu.hash`、`.dynamic`）的秘密也就迎刃而解了！

---

## 1. 难题一：跨模块的函数与全局变量怎么寻址？（`.got` 与 `.plt.got` / `.plt.sec`）

还记得我们在第二篇讲“地址差距超过 4GB 或编译期不知道变量地址时该怎么办”时介绍的 **GOT（Global Offset Table，全局偏移表）** 吗？当时我们说：
> *“如果我们在编译阶段不知道需要访问的变量的地址，那么我们可以用 got 来作为访问跳板，只要在执行访问指令之前可以得到需要访问的变量的地址，然后填入 got 就可以解决这个问题。”*

动态链接解决跨模块寻址的核心武器，正是这个 **`.got` 段**！虽然主程序 `.text` 段和外部 `mylib.so` 之间的偏移量是不确定的，但**主程序自己的 `.text` 段和主程序自己的 `.got` 段在同一个 ELF 文件里，它们之间的相对偏移是完全固定的！**

所以我们只需要在主程序的 `.got` 数组里为每个外部函数和外部变量分配一个 8 字节表项：动态链接器在运行时查到外部函数或变量的真实地址后，把地址填进 `.got` 表项里；主程序代码每次只需通过固定的 `%rip + offset` 读取自己的 `.got` 表项，就能间接跳转到外部函数或读写外部变量了！

我们先用 `readelf -x .got ./dynamic` 看一眼 `.got` 段（起始地址 `0x3fb0`，大小 `0x50` 即 10 个 8 字节表项）在文件中的初始模样：

```text
Contents of section .got:
 3fb0 b03d0000 00000000 00000000 00000000  .=..............
 3fc0 00000000 00000000 30100000 00000000  ........0.......
 3fd0 40100000 00000000 00000000 00000000  @...............
 3fe0 00000000 00000000 00000000 00000000  ................
 3ff0 00000000 00000000 00000000 00000000  ................
```

### 1.1 外部函数的调用：`.plt.got` 与 `.plt.sec` 跳板
对于外部函数（比如 `strlen`），在 C 代码里我们习惯直接 `call` 一个函数地址。为了不改变 `call 函数` 的调用方式，编译器为每一个需要动态链接的外部函数都生成了一段短短 16 字节的跳板代码。因为每个外部函数都有一段这样的代码，而且大小一样大、紧挨着排在一起，所以它们组成了 **`.plt.got`** 和 **`.plt.sec`** 段（我们也可以把它们看作函数跳板数组）：

```text
Disassembly of section .plt.got:
0000000000001050 <__cxa_finalize@plt>:
    1050:   f3 0f 1e fa             endbr64 
    1054:   f2 ff 25 9d 2f 00 00    bnd jmp *0x2f9d(%rip)        # 3ff8 <__cxa_finalize@GLIBC_2.2.5>
    105b:   0f 1f 44 00 00          nopl   0x0(%rax,%rax,1)

Disassembly of section .plt.sec:
0000000000001060 <printf@plt>:
    1060:   f3 0f 1e fa             endbr64 
    1064:   f2 ff 25 5d 2f 00 00    bnd jmp *0x2f5d(%rip)        # 3fc8 <printf@GLIBC_2.2.5>
    106b:   0f 1f 44 00 00          nopl   0x0(%rax,%rax,1)

0000000000001070 <strlen@plt>:
    1070:   f3 0f 1e fa             endbr64 
    1074:   f2 ff 25 55 2f 00 00    bnd jmp *0x2f55(%rip)        # 3fd0 <strlen@Base>
    107b:   0f 1f 44 00 00          nopl   0x0(%rax,%rax,1)
```

当我们调用某个外部函数比如 `strlen` 时，编译器生成的指令是 `call strlen@plt`（即跳转到 `0x1070`），我们逐行看这 3 条指令在干什么：
1. **`endbr64`**：这条指令是用来防止控制流被劫持（比如类似于被 ROP 攻击）的硬件安全指令，类似于 ARM 架构当中的 BTI 指令。如果跳转的目标地址第一条指令不是 `endbr64`，CPU 就会认为出现了非法跳转错误并报错。
2. **`bnd jmp *0x2f55(%rip)`**：以当前 `%rip`（`0x107b`）加上固定偏移 `0x2f55`，直接读取 `.got` 表中 `0x3fd0` 处存放的 `strlen` 函数真实地址并跳过去！
   * **为什么这里是 `jmp` 而不是 `call`？** 因为前面进入 `strlen@plt` 时已经执行过一次 `call`（把返回地址压栈了），如果这里再用 `call`，栈里就会压入两个返回地址，得 `ret` 两次才能回得来；用 `jmp` 的话，等 `strlen` 执行完毕只需要 `ret` 一次就直接回到原调用点了！
   * **前面的 `bnd` 是什么？** `bnd` 是一个特殊的修饰符（Intel MPX 扩展），这个时候跳转地址会被检查是不是在 `bnd0 ~ bnd3` 寄存器界定的范围之间，如果不是就直接挂掉，这样也可以防止控制流被劫持。
3. **`nopl 0x0(%rax,%rax,1)`**：这是一条 5 字节的空操作指令，专门用来填充字节，确保所有的 PLT 表项都是 **16 字节对齐**的（你看 `0x1050`、`0x1060`、`0x1070` 全是 `0x10` 的倍数），这样 CPU 取指和跳转的效率比较高。

### 1.2 全局变量的外部链接：与函数恰好相反的有趣现象！
函数外部链接的跳板我们看懂了，那**跨模块全局变量**的外部链接呢？这里有一个非常反直觉、却又极其巧妙的现象！

比如说，`visit_time` 原本是我们定义在共享库 `mylib.so` 里的一个全局变量，同时主程序 `dynamic` 也引用了它。
* 因为主程序 `dynamic` 的代码在编译链接时，链接器为了优化主程序的访存效率，在主程序的 `.bss` 段（地址 `0x4010`）里给 `visit_time` 分配了一块实体空间，并生成了一条 `R_X86_64_COPY` 重定位记录（意思是运行时把 `mylib.so` 里 `visit_time` 的初始值直接拷贝到主程序的 `0x4010` 处，全进程统一使用主程序 `0x4010` 这个地址作为 `visit_time` 的唯一真身）。
* 这样一来，主程序访问 `visit_time` 反而变成了直接访问自己 `.bss` 段的 `0x4010`；**反倒是定义它的亲生父母 `mylib.so` 自己，在编译时不确定 `visit_time` 将来会被映射到调用者的哪个地址去了！**

所以，`mylib.so` 本身的 `.got` 表除了存储 `mylib.so` 调用外部函数的地址之外，还必须为自己的全局变量 `visit_time` 分配一个 `.got` 表项来存储它的最终地址！因此，`mylib.so` 访问 `visit_time` 的时候，必须**先读自己的 `.got` 表得到 `visit_time` 的地址，然后再去读写 `visit_time`**！

我们直接看 `mylib.so` 中 `update_time` 函数的反汇编代码，对比它访问本地静态变量 `time.0` 和导出全局变量 `visit_time` 的巨大差异：

```text
    112a:   89 05 00 2f 00 00       mov    %eax,0x2f00(%rip)        # 4030 <time.0>
    1130:   48 8b 05 b9 2e 00 00    mov    0x2eb9(%rip),%rax        # 3ff0 <visit_time-0x3c>
    1137:   8b 10                   mov    (%rax),%edx
    1139:   8b 05 f1 2e 00 00       mov    0x2ef1(%rip),%eax        # 4030 <time.0>
    113f:   01 c2                   add    %eax,%edx
    1141:   48 8b 05 a8 2e 00 00    mov    0x2ea8(%rip),%rax        # 3ff0 <visit_time-0x3c>
    1148:   89 10                   mov    %edx,(%rax)
```

你看，对比太鲜明了：
* 本地静态全局变量 `time.0`（地址 `0x4030`）因为不会被外部文件抢走映射，每次被访问时都是直接用 `0x2f00(%rip)` 或 `0x2ef1(%rip)` 在本模块全局空间一步到位读写的；
* 而跨模块全局变量 `visit_time` 被访问时，必须先执行 `mov 0x2eb9(%rip), %rax`，从 `mylib.so` 自己的 `.got` 表项（`0x3ff0`）中取出 `visit_time` 的真实地址存到 `%rax` 寄存器，然后再用 `mov (%rax), %edx` 和 `mov %edx, (%rax)` 间接访问它！

### 1.3 `.got` 表的完整排布结构
现在我们可以把 `.got` 数组（`0x3fb0 ~ 0x3ff8`）的内部排布总结出来了：
1. **第 0 个表项（`0x3fb0`）**：存放本模块 **`.dynamic` 段的段首地址**（你看前面 `.got` 的 hex dump 第一项是 `b0 3d 00 00 00 00 00 00`，小端序还原就是 **`0x3db0`**，正好是 `.dynamic` 段的地址！），因为动态链接器在自举和解析时需要用它来查 `.dynamic` 表。
2. **第 1 个表项（`0x3fb8`）**：存放本模块 **`link_map` 结构体的指针地址**，因为 `link_map` 可以为动态链接提供当前模块所有需要的信息（初始在文件中为 `0`，由动态链接器启动时填入）。
3. **第 2 个表项（`0x3fc0`）**：存放**做动态链接延迟绑定的函数（`_dl_runtime_resolve`）的入口地址**（初始为 `0`，由动态链接器填入）。
4. **第 3 个表项往后**：就是各个动态链接符号存放真实地址的地方！而且顺序也很有讲究：**紧接着第 2 项后面先填的是 `.rela.plt` 对应的函数 `.got` 地址（`0x3fc8`、`0x3fd0`），再往后填的才是 `.rela.dyn` 对应的 `.got` 地址（`0x3fd8 ~ 0x3ff8`）**。

```text
+-----------------------------------------------------------------------------+
|                    .got 段内存排布全貌 (0x3fb0 ~ 0x4000)                    |
+------------+----------------------+-----------------------------------------+
| 虚拟地址   | 文件中的初始值       | 表项含义与填充时机                      |
+------------+----------------------+-----------------------------------------+
| 0x3fb0 [0] | 0x0000000000003db0   | 指向 .dynamic 段首地址 (编译期已填好)   |
| 0x3fb8 [1] | 0x0000000000000000   | 当前模块的 link_map 指针 (ld.so 填入)   |
| 0x3fc0 [2] | 0x0000000000000000   | _dl_runtime_resolve 地址 (ld.so 填入)   |
+------------+----------------------+-----------------------------------------+
| 0x3fc8 [3] | 0x0000000000001030   | printf@got   <-- 归 .rela.plt 管辖      |
| 0x3fd0 [4] | 0x0000000000001040   | strlen@got   <-- 归 .rela.plt 管辖      |
+------------+----------------------+-----------------------------------------+
| 0x3fd8 [5] | 0x0000000000000000   | __libc_start_main@got   <-- 归.rela.dyn |
| 0x3fe0 [6] | 0x0000000000000000   | _ITM_deregisterTM...    <-- 归.rela.dyn |
| 0x3fe8 [7] | 0x0000000000000000   | __gmon_start__@got      <-- 归.rela.dyn |
| 0x3ff0 [8] | 0x0000000000000000   | _ITM_registerTM...      <-- 归.rela.dyn |
| 0x3ff8 [9] | 0x0000000000000000   | __cxa_finalize@got      <-- 归.rela.dyn |
+------------+----------------------+-----------------------------------------+
```

---

## 2. 难题二：立即绑定（`.rela.dyn`）与延迟绑定（`.rela.plt` + `.plt`）

我们先把主程序 `./dynamic` 的动态符号表 `.dynsym` 以及两个重定位段 `.rela.dyn`、`.rela.plt` 全部导出来放在一起对比：

```text
Symbol table '.dynsym' contains 9 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main@GLIBC_2.34 (2)
     2: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_deregisterTM[...]
     3: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND printf@GLIBC_2.2.5 (3)
     4: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __gmon_start__
     5: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND strlen
     6: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_registerTMC[...]
     7: 0000000000004010     4 OBJECT  GLOBAL DEFAULT   26 visit_time
     8: 0000000000000000     0 FUNC    WEAK   DEFAULT  UND __cxa_finalize@GLIBC_2.2.5 (3)

Relocation section '.rela.dyn' at offset 0x5a0 contains 9 entries:
  Offset          Info           Type           Sym. Value    Sym. Name + Addend
000000003da0  000000000008 R_X86_64_RELATIVE                    1160
000000003da8  000000000008 R_X86_64_RELATIVE                    1120
000000004008  000000000008 R_X86_64_RELATIVE                    4008
000000003fd8  000100000006 R_X86_64_GLOB_DAT 0000000000000000 __libc_start_main@GLIBC_2.34 + 0
000000003fe0  000200000006 R_X86_64_GLOB_DAT 0000000000000000 _ITM_deregisterTM[...] + 0
000000003fe8  000400000006 R_X86_64_GLOB_DAT 0000000000000000 __gmon_start__ + 0
000000003ff0  000600000006 R_X86_64_GLOB_DAT 0000000000000000 _ITM_registerTMCl[...] + 0
000000003ff8  000800000006 R_X86_64_GLOB_DAT 0000000000000000 __cxa_finalize@GLIBC_2.2.5 + 0
000000004010  000700000005 R_X86_64_COPY     0000000000004010 visit_time + 0

Relocation section '.rela.plt' at offset 0x678 contains 2 entries:
  Offset          Info           Type           Sym. Value    Sym. Name + Addend
000000003fc8  000300000007 R_X86_64_JUMP_SLO 0000000000000000 printf@GLIBC_2.2.5 + 0
000000003fd0  000500000007 R_X86_64_JUMP_SLO 0000000000000000 strlen + 0
```

### 2.1 为什么要把 `.dynsym`、`.rela.dyn` 和 `.rela.plt` 分成三个表？
看到这里，很多同学心里肯定会冒出一个大大的问号：**`.dynsym` 里不就已经列出了所有要链接的 8 个符号的信息了吗？为什么还要额外搞出 `.rela.dyn` 和 `.rela.plt` 两个表，为什么不干脆全部做在一张表里呢？**

这里有两个层面的原因：
1. **`.dynsym` 的信息不够用**：`.dynsym` 里面记录的主要是每个连接符号自身的属性（比如它是函数 `FUNC` 还是全局变量 `OBJECT`、名字叫什么、弱符号还是全局符号）。实际连接的时候，动态链接器确实需要根据这里的名字去配对、根据这里的类型采用对应的连接方法；但光有这些数据是不够的，动态链接器还需要知道**“查到这个符号的地址后，我该把它填到 `.got` 表的哪个地址去（`Offset`）”**！
2. **那为什么重定位表还要拆成 `.rela.dyn` 和 `.rela.plt` 两张表呢？**
   一个核心原因就是：**我们要处理的动态链接符号被分为了两类！**
   * **第一类（保存在 `.rela.dyn` 中）：在 `main` 函数运行之前必须全部动态链接完的数据（立即绑定）**。比如 Glibc 启动代码立刻就要调用的 `__libc_start_main`、初始化/退出钩子 `__gmon_start__` / `__cxa_finalize`、以及全局数据 `visit_time`。像 `__libc_start_main` 这种在 `_start` 里一定会被立刻访问的函数，如果不绑定根本无法启动，做成延迟绑定毫无意义，不如在启动时（`_dl_relocate_object` 函数中）一起绑定掉，分摊额外开销。
     * *顺便说个细节*：细心的小伙伴会发现，`__libc_start_main`、`_ITM_registerTMCloneTable` 等函数在 `.plt.got` / `.plt.sec` 里甚至都没有对应的跳板条目！因为它们在整个程序里往往只被启动/退出代码访问一次，直接用 `call *0x3fd8(%rip)` 读 GOT 表调用写死就好了，根本没必要额外搞一个 PLT 跳板封装一下。
   * **第二类（保存在 `.rela.plt` 中）：可以延迟绑定的普通业务函数（Lazy Binding）**。比如 C 代码 `main` 里调用的 `printf` 和 `strlen`。它们在程序启动时不一定会被马上调用（甚至如果处于某个没走到的 `if` 分支里，可能整个运行期都不会被调用），所以把它们单独放在 `.rela.plt` 中，等真正被调用的那一刻再去做动态链接！

我们看重定位表项的格式：
* **`Offset`**：需要填入真实地址的目标内存偏移地址（比如 `__libc_start_main` 是 `0x3fd8`，`strlen` 是 `0x3fd0`）。
* **`Info`**：前 4 个字节（高 32 位）是对应的 **`.dynsym` 的索引号**，指示去 `.dynsym` 的第几项找符号名字等进一步信息；后 4 个字节（低 32 位）是**处理的重定位事件类型**。
  * 比如要绑定 `__libc_start_main` 那一行，`Info` 是 `000100000006`：高 32 位是 `1`，就去查 `.dynsym` 的第 1 项得到符号名 `"__libc_start_main"`，然后开始在全局 `link_map` 链表里搜这个字符串，如果找到了就把对应的地址载入到 `Offset` `0x3fd8` 处！
  * 而对于 `0x4010` 的 `visit_time`（`Info = 000700000005`，类型 `R_X86_64_COPY`），在把初始值拷过来的同时，对面的 `mylib.so` 在重定位自己的 `.rela.dyn` 时就会把主程序这个 `0x4010` 地址存到 `mylib.so` 自己的 `.got` 表当中！

### 2.2 延迟绑定（Lazy Binding）与 `.plt` 段的精妙配合
现在我们来揭晓 `.rela.plt` 里的 `printf`（`0x3fc8`）和 `strlen`（`0x3fd0`）到底是怎么实现延迟绑定的！
（注：在我们编译的这个 `./dynamic` 样本中，因为编译器默认打开了 `BIND_NOW` 标志，所以在启动调用 `_dl_relocate_object` 时会直接把 `.rela.plt` 也一次性绑定掉；但在未开启 `BIND_NOW` 的经典 Lazy Binding 模式下，`.plt` 段就会大显身手，文件里也完整保留了整套 Lazy Binding 机制的初始值和代码）。

我们回看一眼前面 `.got` 段在 `0x3fc8` 和 `0x3fd0` 处的初始十六进制数据：

```text
 3fc0 00000000 00000000 30100000 00000000  ........0.......
 3fd0 40100000 00000000 00000000 00000000  @...............
```

看见了吗？！不同于 `.rela.dyn` 对应的其他项目一开始全是 `00000000`，`.rela.plt` 对应的两个表项 `0x3fc8`（`printf@got`）和 `0x3fd0`（`strlen@got`）**在文件里是有初始值的，分别是 `0x1030` 和 `0x1040`！**

所以我们可以想见：当程序**第一次调用 `strlen@plt`（`0x1070`）**时，`bnd jmp *0x2f55(%rip)` 读取 `0x3fd0` 跳过去的并不是真正的 `strlen` 函数，而是跳进了 **`0x1040`** 这个地址当中！

那 `0x1030` 和 `0x1040` 是什么地方呢？我们把 **`.plt` 段（`0x1020 ~ 0x104f`）** 反汇编出来一看便知：

```text
Disassembly of section .plt:
0000000000001020 <.plt>:
    1020:   ff 35 92 2f 00 00       push   0x2f92(%rip)        # 3fb8 <_GLOBAL_OFFSET_TABLE_+0x8>
    1026:   f2 ff 25 93 2f 00 00    bnd jmp *0x2f93(%rip)      # 3fc0 <_GLOBAL_OFFSET_TABLE_+0x10>
    102d:   0f 1f 00                nopl   (%rax)
    1030:   f3 0f 1e fa             endbr64 
    1034:   68 00 00 00 00          push   $0x0
    1039:   f2 e9 e1 ff ff ff       bnd jmp 1020 <_init+0x20>
    103f:   90                      nop
    1040:   f3 0f 1e fa             endbr64 
    1044:   68 01 00 00 00          push   $0x1
    1049:   f2 e9 d1 ff ff ff       bnd jmp 1020 <_init+0x20>
    104f:   90                      nop
```

真相大白！原来跳入了 `.plt` 段！这个段就是专门负责处理 `.plt.sec` / `.plt.got` 当中函数的 Lazy Binding 问题的，所以 **`.plt`、`.plt.sec`（`.plt.got`）、`.got`、`.rela.plt` 四者是紧密结合的一套组合拳**！

此外，动态链接器在一开始做 `_dl_relocate_object` 初始化的时候，就已经预先把主程序的 **`main_map` 地址** 和 **`_dl_runtime_resolve` 函数的地址** 分别填入到了 `.got` 的第 1 项（`0x3fb8`）和第 2 项（`0x3fc0`）当中。于是第一次调用 `strlen@plt` 的完整过程如下：
1. 在 `strlen@plt`（`0x1070`）中读取 `0x3fd0` 的初始值，跳到 `.plt` 的 **`0x1040`** 处；
2. **`0x1040` 把立即数 `1`（`$0x1`）压入堆栈**（为什么压入 `1`？因为 `strlen` 在 `.rela.plt` 数组里的下标 `plt_index` 正好是第 `1` 项！而 `0x1030` 对应 `printf`，压入的就是下标 `$0x0`），然后 `bnd jmp 1020` 跳到 `.plt` 的开头 `0x1020`；
3. **`0x1020` 把 `.got` 的第 1 项（`0x3fb8`，即 `main_map` 结构体指针）压入堆栈**，然后 `bnd jmp *0x2f93(%rip)` 跳入 `.got` 第 2 项（`0x3fc0`）存放的函数 **`_dl_runtime_resolve`**！
4. `_dl_runtime_resolve` 严格来说也不能叫普通 C 函数，它其实是一段汇编过程，前面压入栈顶的两个参数 `link_map`（`main_map`）和 `plt_index`（`1`）也不是它自己要用——它首先把所有通用寄存器（`rax`, `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9` 等）保护起来防止破坏原函数的传参现场，然后调用真正的 C 函数 **`_dl_fixup(main_map, plt_index)`**！
5. 在 `_dl_fixup` 中：
   * 第一项参数是 `main_map`，我们可以顺着它找到本模块的 `.dynsym` 和 `.rela.plt`；
   * 第二项参数是 `1`，因为 `.rela.plt` 的第 1 项（从 0 开始数）正好就是 `strlen`，所以我们立刻拿到了 `strlen` 的重定位数据：知道了要回填的 `.got` 地址是 `0x3fd0`，对应的 `.dynsym` 条目是第 `5` 项（`"strlen"`）；
   * 顺着 `main_map` 所在的全局链表可以查到所有已载入的其他共享库的 `link_map`，只要找到哪个 `link_map` 包含 `"strlen"` 这个导出函数，查出它的真实内存地址，然后**把真实地址填入 `0x3fd0` 的 `.got` 表项中**即可！
6. 填好 `.got` 表后，`_dl_runtime_resolve` 恢复之前保护的寄存器，弹掉栈上的 `link_map` 和 `plt_index`，直接跳进刚刚查出来的 `strlen` 函数执行。**以后第二次、第三次再调用 `strlen@plt` 时，因为 `0x3fd0` 的 `.got` 表已经被填入了正确的值，就会直接一步跳进 `strlen` 函数，再也不用走 `.plt` 和 `_dl_runtime_resolve` 了！**

```text
+-----------------------------------------------------------------------------+
|                .plt / .got 延迟绑定 (Lazy Binding) 完整控制流               |
+-----------------------------------------------------------------------------+
|  [调用处] call 1070 <strlen@plt>                                            |
|              |                                                              |
|              v                                                              |
|  [.plt.sec (0x1070)]                                                        |
|    endbr64                                                                  |
|    bnd jmp *0x2f55(%rip)  ===> 读取 GOT[4] (0x3fd0)                         |
|                                  |                                          |
|         +------------------------+------------------------+                 |
|         | [第一次调用: 值为 0x1040]                       | [第二次及以后]  |
|         v                                                 v                 |
|  [.plt (0x1040)]                                  +-----------------------+ |
|    endbr64                                        | 直接跳入 libc/mylib   | |
|    push $0x1          (压入参数2: plt_index = 1)  | 中的 strlen 真实地址! | |
|    bnd jmp 1020                                   +-----------------------+ |
|         |                                                 ^                 |
|         v                                                 |                 |
|  [.plt 公共头 (0x1020)]                                   |                 |
|    push 0x2f92(%rip)  (压入参数1: GOT[1] = main_map)      |                 |
|    bnd jmp *0x2f93(%rip) (跳入 GOT[2] = _dl_runtime_resolve)                |
|         |                                                 |                 |
|         v                                                 |                 |
|  [_dl_runtime_resolve ---> _dl_fixup(main_map, 1)]        |                 |
|    1. 保护寄存器上下文                                    |                 |
|    2. 查 main_map 的 .rela.plt[1] 得到 Offset=0x3fd0, Sym=5 ("strlen")      |
|    3. 遍历全局 link_map 查 .gnu.hash 找到 strlen 真实地址 |                 |
|    4. 将真实地址回填写入 GOT[4] (0x3fd0) -----------------+                 |
|    5. 恢复寄存器并跳入 strlen 执行                                          |
+-----------------------------------------------------------------------------+
```

---

## 3. 难题三：如何在众多共享库中极速查找到符号？（`.dynstr`、`.dynsym` 与 `.gnu.hash`）

上面不管是启动时 `_dl_relocate_object` 绑定 `.rela.dyn`，还是运行时 `_dl_fixup` 绑定 `.rela.plt`，最核心的一步都是：**拿着符号名字符串（比如 `"strlen"`），遍历全局 `link_map` 链表，去各个共享库（比如 `mylib.so`、`libc.so.6`）里面找谁定义了这个函数！**

为了实现跨文件的动态符号查找，每个参与动态链接的 ELF 文件（包括可执行文件和 `.so` 共享库）都配备了三个紧密配合的段：**`.dynstr`**、**`.dynsym`** 和 **`.gnu.hash`**。我们以自己编写的共享库 **`mylib.so`** 为例，把这三个段导出来一看究竟。

### 3.1 `mylib.so` 的 `.dynstr` 与 `.dynsym`（“查别人” vs “被别人查”）
```text
Contents of section .dynstr:
 0400 005f5f67 6d6f6e5f 73746172 745f5f00  .__gmon_start__.
 0410 5f49544d 5f646572 65676973 74657254  _ITM_deregisterT
 0420 4d436c6f 6e655461 626c6500 5f49544d  MCloneTable._ITM
 0430 5f726567 69737465 72544d43 6c6f6e65  _registerTMClone
 0440 5461626c 65005f5f 6378615f 66696e61  Table.__cxa_fina
 0450 6c697a65 00766973 69745f74 696d6500  lize.visit_time.
 0460 75706461 74655f74 696d6500 7374726c  update_time.strl
 0470 656e0064 756d6d79 00                 en.dummy. 

Symbol table '.dynsym' contains 9 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __cxa_finalize
     2: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_registerTMC[...]
     3: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND _ITM_deregisterT[...]
     4: 0000000000000000     0 NOTYPE  WEAK   DEFAULT  UND __gmon_start__
     5: 000000000000118b    34 FUNC    GLOBAL DEFAULT   12 dummy
     6: 000000000000114d    62 FUNC    GLOBAL DEFAULT   12 strlen
     7: 0000000000001119    52 FUNC    GLOBAL DEFAULT   12 update_time
     8: 000000000000402c     4 OBJECT  GLOBAL DEFAULT   22 visit_time
```

* **`.dynstr`** 是 Dynamic String 的意思，专门存储 `.dynsym` 段里每个项目的名字字符串，`.dynsym` 的 `Name` 字段存的就是字符串在 `.dynstr` 段里的偏移量。
* **`.dynsym`** 定义了每个用于动态链接的符号的内容：`Value` 是数据的相对地址，`Size` 是数据的大小，`Type` 是数据的类型（如无类型 `NOTYPE`、函数 `FUNC`、数据变量 `OBJECT`），`Bind` 是绑定类型（局部 `LOCAL`、全局 `GLOBAL`、弱符号 `WEAK`）。
* 最核心的是 **`Ndx`（所在段索引）** 字段，它把 `.dynsym` 里的符号清晰地分成了两大阵营：
  * **前 5 项（索引 `0 ~ 4`）：`Ndx = UND`（Undefined）**。意思是这个数据在本文件里没有定义，是要从其他共享库文件那里绑定过来的——换句话说，**`UND` 是“查别人”**！
  * **后 4 项（索引 `5 ~ 8`）：`Ndx = 12`（`.text` 全局函数 `dummy`、`strlen`、`update_time`）和 `Ndx = 22`（`.data` 全局变量 `visit_time`）**。它们是本文件自己定义并导出的全局符号，是会被其他文件链接过去的——换句话说，**非 `UND` 是“被别人查”**！

### 3.2 `.gnu.hash` 段结构与手算查找全过程
既然其他文件来 `mylib.so` 里查符号时，只会查后面那 4 个“被别人查”的导出符号（索引 `5 ~ 8`），如果每次都从头到尾线性遍历比对字符串那就太慢了。于是 ELF 设计了 **`.gnu.hash` 段**，专门用来快速查找一个共享库导出的动态符号！

我们导出 `mylib.so` 的 `.gnu.hash` 段（共 `0x34` 字节，每 4 字节为一个 32 位小端序整数）：

```text
Contents of section .gnu.hash:
 02f0 03000000 05000000 01000000 06000000  ................
 0300 04004320 10440000 05000000 00000000  ..C .D..........
 0310 07000000 1099520f 9dbb931c 96faea7c  ......R........|
 0320 03945ba3                             ..[. 
```

麻雀虽小，五脏俱全！我们把这短短几行十六进制数逐字拆开看：
1. **开头 4 个 4 字节（`0x02f0 ~ 0x02ff`）是 Hash 表头**：
   * 第 1 个字（`03 00 00 00` = **`3`**）：使用的 Hash 表的**桶（Bucket）的个数** `nbuckets`，这里是 3 个桶。
   * 第 2 个字（`05 00 00 00` = **`5`**）：`.dynsym` 中**查不到的符号个数（即 `UND` 导入符号的个数 `symndx`）**，这里是 5 个！正如我们刚才所见，`.dynsym` 前面 `0 ~ 4` 一共 5 个是 `UND`（查别人的），所以不需要存在这个 Hash 表里面，只有从第 `5` 号索引开始余下的 4 个“被别人查”的导出符号才存在 Hash 表里！
   * 第 3 个字（`01 00 00 00` = **`1`**）：**布隆过滤器（Bloom Filter）的长度** `maskwords`，单位是 8 字节（64 位字），这里是 1 个 8 字节。
   * 第 4 个字（`06 00 00 00` = **`6`**）：计算布隆过滤器第二个比特位时，Hash 值需要**右移的位数 `shift2`**，这里是 6。
2. **随后的 `x` 个 8 字节（`0x0300 ~ 0x0307`）是布隆过滤器数组**：
   * 这里 `maskwords = 1`，所以只有 1 个 8 字节：`04 00 43 20 10 44 00 00`，按小端序还原成 64 位整数就是 **`0x0000441020430004`**。
3. **随后的 3 个 4 字节（`0x0308 ~ 0x0313`）是 3 个桶（Buckets）对应的 `.dynsym` 起始索引**：
   * 第 0 个桶（`05 00 00 00` = **`5`**）：说明 Hash 值落在第 0 号桶的那些符号，它们在 `.dynsym` 中是从**第 5 个索引**开始依次排列的；
   * 第 1 个桶（`00 00 00 00` = **`0`**）：说明第 1 号桶是**空的**；
   * 第 2 个桶（`07 00 00 00` = **`7`**）：说明 Hash 值落在第 2 号桶的那些符号，它们在 `.dynsym` 中是从**第 7 个索引**开始依次排列的。
   * *恍然大悟了吧！* 实际上 `.dynsym` 里导出符号的排序并不是随便排的，而是**严格按照 `.gnu.hash` 表里面桶散列的顺序排的**！首先是第 0 个桶开放散列对应的符号（`#5 dummy`、`#6 strlen`），第 1 个桶为空跳过，之后是第 2 个桶对应的符号（`#7 update_time`、`#8 visit_time`）！
4. **最后的 4 个 4 字节（`0x0314 ~ 0x0323`）是每个导出符号对应的 Hash 值数组（Chain）**：
   * 它们也严格按照上面 `.dynsym[5..8]` 的顺序排列：
     * 第 0 项（对应 `.dynsym[5] dummy`）：`10 99 52 0f` -> **`0x0f529910`**
     * 第 1 项（对应 `.dynsym[6] strlen`）：`9d bb 93 1c` -> **`0x1c93bb9d`**（最低位为 `1`，表示第 0 号桶到此结束）
     * 第 2 项（对应 `.dynsym[7] update_time`）：`96 fa ea 7c` -> **`0x7ceafa96`**
     * 第 3 项（对应 `.dynsym[8] visit_time`）：`03 94 5b a3` -> **`0xa35b9403`**（最低位为 `1`，表示第 2 号桶到此结束）

```text
+-----------------------------------------------------------------------------+
|                mylib.so 的 .gnu.hash 与 .dynsym 联动结构图                  |
+-----------------------------------------------------------------------------+
| [.gnu.hash Header (0x02f0)]                                                 |
|   nbuckets = 3  |  symndx = 5 (跳过前5个UND)  |  maskwords = 1  | shift = 6 |
+-----------------------------------------------------------------------------+
| [Bloom Filter (0x0300, 8字节)] : 0x0000441020430004                         |
+-----------------------------------------------------------------------------+
| [Buckets 桶数组 (0x0308)]                                                   |
|   bucket[0] = 5  -----------------+                                         |
|   bucket[1] = 0  (空桶)           |                                         |
|   bucket[2] = 7  -----------------+-----------------------------------+     |
+-----------------------------------|-----------------------------------|-----+
| [Chain Hash配列] <--- 一一对应 ---|---> [.dynsym 符号表]              |     |
|                                   v                                   |     |
|   chain[0]: 0x0f529910  <===>  dynsym[5]: dummy       (桶0)           |     |
|   chain[1]: 0x1c93bb9d  <===>  dynsym[6]: strlen      (桶0末尾,bit0=1)|     |
|   chain[2]: 0x7ceafa96  <===>  dynsym[7]: update_time (桶2) <---------+     |
|   chain[3]: 0xa35b9403  <===>  dynsym[8]: visit_time  (桶2末尾,bit0=1)      |
+-----------------------------------------------------------------------------+
```

#### 手算推演：动态链接器是如何查找 `"strlen"` 的？
假设现在主程序需要动态链接 `strlen` 函数，动态链接器会遍历全局 `link_map` 链表，依次在每个 `link_map` 的 `.gnu.hash` 表里查找有没有 `"strlen"` 这个符号（如果存在多个 `link_map` 都有 `strlen` 函数，那就先和哪个 `link_map` 匹配成功就用谁的函数）。

具体查找计算过程分为 4 步：

1. **第一步：调用 `dl_new_hash` 计算符号名的 Hash 值**
   Glibc 使用的 Hash 函数代码如下：
   ```c
   static uint_fast32_t
   dl_new_hash (const char *s)
   {
     uint_fast32_t h = 5381;
     for (unsigned char c = *s; c != '\0'; c = *++s)
       h = h * 33 + c;
     return h & 0xffffffff;
   }
   ```
   我们把 `"strlen"` 代入计算，得到的 32 位 Hash 值就是 **`0x1c93bb9d`**！

2. **第二步：用布隆过滤器（Bloom Filter）快速过滤**
   我们先用布隆过滤器测试一下，看看 `"strlen"` 是不是**一定不在**当前这个 `link_map` 里面。
   Hash 值转换到布隆过滤器比特位的公式是：
   * 要查看布隆过滤器的第几个 8 字节：`bytenum = (hash / 64) % maskwords`（这里 `maskwords = 1`，所以就是第 0 个 8 字节）；
   * 在该 8 字节上的两个比特位分别是：**`bit1 = hash % 64`**，**`bit2 = (hash >> shift) % 64`**（这里 `shift = 6`）。
   我们把 `mylib.so` 里的四个导出符号 `dummy`、`visit_time`、`update_time`、`strlen` 分别代入算出它们的 `(hash, (1<<bit1) | (1<<bit2))`：
   ```text
   ('dummy',       '0xf529911')    -> 掩码: '0x1000020000'
   ('visit_time',  '0xa35b9402')   -> 掩码: '0x10004'
   ('update_time', '0x7ceafa96L')  -> 掩码: '0x40000400000L'
   ('strlen',      '0x1c93bb9d')   -> 掩码: '0x400020000000'
   ---------------------------------------------------------
   四个掩码按位或的结果:                    0x441020430004L
   ```
   你看，四个符号算出来的掩码合起来正好就是刚才 `.gnu.hash` 里存的布隆过滤器值 **`0x0000441020430004`**！
   当我们查 `strlen` 时，检查 `0x441020430004` 在 `0x400020000000` 对应的两个 bit 位全都是 `1`，说明明显没被过滤掉，我们可以继续在这个 `link_map` 里往下查！（如果任一 bit 为 `0` 被过滤掉了，那就连桶都不用看，直接跳去搜下一个 `link_map`）。

3. **第三步：算哈希桶下标并扫描 Chain 哈希序列**
   我们计算桶下标：`hash % nbuckets = 0x1c93bb9d % 3 = 0`，所以落在**第 0 个 Hash 桶**里面！
   查表看到第 0 个桶的值 `bucket[0] = 5`，说明从 `.dynsym` 的第 `5` 个符号开始查；又因为前面 `UND` 的符号有 `symndx = 5` 个，所以对应到 `.gnu.hash` 末尾的 Chain 哈希序列就是从第 `5 - 5 = 0` 个开始往下比对：
   * 第 0 个（对应 `.dynsym[5]`）：`0x0f529910`，和 `0x1c93bb9d` 不一样，继续看下一个；
   * 第 1 个（对应 `.dynsym[6]`）：**`0x1c93bb9d`**，和 `strlen` 的 Hash 值一模一样！

4. **第四步：核对 `.dynstr` 字符串名字**
   为了防止不同字符串出现 Hash 冲突，我们最后去查 `.dynsym[6]` 在 `.dynstr` 里对应的 `Name` 字符串，看看是不是真的和 `"strlen"` 一模一样。一比对，果然是 `"strlen"`！这就说明找到了，可以把 `.dynsym[6]` 的地址（加上 `mylib.so` 的基址偏移 `bias`）拿去绑定了！

---

## 4. 动态链接的总指挥台：`.dynamic` 段全解析

到现在为止，我们已经认识了 `.init`/`.fini`/`.init_array`/`.fini_array`、`.gnu.hash`、`.dynsym`、`.dynstr`、`.got`、`.rela.plt`、`.rela.dyn` 等十几个段。但问题来了：动态链接器 `ld.so` 在运行时只有 Program Headers（Segment 视图），没有 Section Header Table（Section 视图），它怎么知道这些段都在内存的哪个地址、有多大呢？

这就是 **`.dynamic` 段**（对应 Program Headers 中的 `DYNAMIC` 段）存在的意义！`.dynamic` 段记录了动态链接所需要的全部导航信息。在下一篇我们将看到，当动态链接器的 `dl_main` 调用 `elf_get_dynamic_info` 函数之后，`.dynamic` 里的所有表项就会被解析保存到 `main_map` 的 `l_info`（`dynamic_info`）数组当中，每个固定下标位置存储对应类型的数据（位置对应的表项类型用枚举事先定义好了）。

我们把 `./dynamic` 程序的 `.dynamic` 段（共 28 个表项）按用途分为 6 组来逐一检阅：

### 4.1 依赖的共享库与搜索路径（`NEEDED` / `RPATH` / `RUNPATH`）
```text
Dynamic section at offset 0x2db0 contains 28 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [mylib.so]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
```
* **`NEEDED`**：指明当前程序依赖哪些共享库文件（文件名字符串实际存在 `.dynstr` 段中，`.dynamic` 表项仅存字符串的偏移量）。在做 `_dl_map_objects_dep` 时，动态链接器就是看 `NEEDED` 对应的文件名，然后用 `_dl_openaux` 去打开并加载这些依赖库。
* 此外如果编译时指定了 **`RPATH`** 或 **`RUNPATH`**，也会记录在这里，在 `do_preload` / `_dl_map_object` 搜索共享库文件时需要解析这两个目录。

### 4.2 初始化与析构函数位置（`INIT` / `FINI` / `INIT_ARRAY` / `FINI_ARRAY`）
```text
 0x000000000000000c (INIT)               0x1000
 0x000000000000000d (FINI)               0x11cc
 0x0000000000000019 (INIT_ARRAY)         0x3da0
 0x000000000000001b (INIT_ARRAYSZ)       8 (bytes)
 0x000000000000001a (FINI_ARRAY)         0x3da8
 0x000000000000001c (FINI_ARRAYSZ)       8 (bytes)
```
* `INIT` 和 `FINI` 分别记录了 `.init` 段和 `.fini` 段函数的起始地址；
* `INIT_ARRAY` / `INIT_ARRAYSZ` 记录了 `.init_array` 段的起始地址和数组字节大小；
* `FINI_ARRAY` / `FINI_ARRAYSZ` 记录了 `.fini_array` 段的起始地址和数组字节大小。
* 于是在 `_dl_init` 或者 `_dl_fini` 需要处理这些构造和析构函数的时候，就是直接查 `.dynamic` 的这几个域找到地址并开始执行的。

### 4.3 Hash 表位置（`GNU_HASH`）
```text
 0x000000006ffffef5 (GNU_HASH)           0x3b0
```
* 记录了 `.gnu.hash` 段的地址（`0x3b0`）。当动态链接器调用 `_dl_setup_hash` 的时候，就会使用这个数据找到 `.gnu.hash` 段的位置，进而构造出刚才我们手算的那套动态符号查找 Hash 表结构。

### 4.4 动态符号表与字符串表位置（`STRTAB` / `SYMTAB` / `DEBUG`）
```text
 0x0000000000000005 (STRTAB)             0x4b0
 0x0000000000000006 (SYMTAB)             0x3d8
 0x000000000000000a (STRSZ)              170 (bytes)
 0x000000000000000b (SYMENT)             24 (bytes)
 0x0000000000000015 (DEBUG)              0x0
```
* `STRTAB`、`STRSZ` 指示了 `.dynstr` 段的位置（`0x4b0`）和总大小（170 字节）；
* `SYMTAB`、`SYMENT` 指示了 `.dynsym` 符号表的位置（`0x3d8`）和每个表项的大小（24 字节）；
* `DEBUG` 则用于动态链接器填入调试结构指针，供 GDB 知晓当前加载了哪些共享库。

### 4.5 重定位与 GOT/PLT 位置（`PLTGOT` / `JMPREL` / `RELA`）
```text
 0x0000000000000003 (PLTGOT)             0x3fb0
 0x0000000000000002 (PLTRELSZ)           48 (bytes)
 0x0000000000000014 (PLTREL)             RELA
 0x0000000000000017 (JMPREL)             0x678
 0x0000000000000007 (RELA)               0x5a0
 0x0000000000000008 (RELASZ)             216 (bytes)
 0x0000000000000009 (RELAENT)            24 (bytes)
```
* `PLTGOT` 是 `.got` 段的首地址（`0x3fb0`）；
* `JMPREL` 和 `PLTRELSZ` 是 `.rela.plt` 段的首地址（`0x678`）和总大小（48 字节），记录了用于动态链接的函数重定位信息；
* `RELA`、`RELASZ`、`RELAENT` 是 `.rela.dyn` 段的首地址（`0x5a0`）、总大小（216 字节）和单个表项大小（24 字节），记录了用于动态链接的数据和立即绑定符号的信息。

### 4.6 绑定标志与版本信息（`FLAGS` / `VERNEED` / `VERSYM`）
```text
 0x000000000000001e (FLAGS)              BIND_NOW
 0x000000006ffffffb (FLAGS_1)            Flags: NOW PIE
 0x000000006ffffffe (VERNEED)            0x570
 0x000000006fffffff (VERNEEDNUM)         1
 0x000000006ffffff0 (VERSYM)             0x55a
 0x000000006ffffff9 (RELACOUNT)          3
 0x0000000000000000 (NULL)               0x0
```
* `BIND_NOW` 表示程序要求在一开始启动时（`_dl_relocate_object`）就立即绑定所有符号，不使用 Lazy Binding；
* `NOW PIE` 表示代码是位置无关的独立可执行代码；
* `VERNEED` 是 `.gnu.version_r` 段的地址（`0x570`），`VERSYM` 是 `.gnu.version` 段的地址（`0x55a`），在 `dl_main` 中会根据这些信息完成对应版本共享库与 `ld.so` 的校验载入。

## 小结
到这里，我们已经把动态链接 ELF 文件内部的静态结构、GOT/PLT 寻址机制、延迟绑定过程以及 `.gnu.hash` 查找算法全部摸透了。万事俱备，只欠东风——在下一篇（也是本系列的收官之篇）中，我们将进入动态链接器 `/lib64/ld-linux-x86-64.so.2` 的源码与单步流程，亲眼看看它是如何在启动时自举、搜索加载所有 `.so` 共享库、对循环依赖做拓扑排序并最终把控制权交给我们的 C 程序的！