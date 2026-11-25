# ELF 的一些探索（七）：GCC 静态编译链接、静态库与程序加载运行

在前六篇文章中，为了不被复杂的操作系统和运行时环境干扰，我们一直自己手写 RISC-V 的 `.S` 汇编、自己写 `link.ld` 链接脚本，假设程序一上电 PC 寄存器就直奔 `main`（或者只有两行初始化 `sp` 栈底的 `_start`），并且在第五篇讲 ELF 加载时，我们假设有一个“全知全能的神秘力量”把躺在硬盘里的 ELF 文件按页搬进了内存。

从这一篇开始，我们终于要走出裸机理想国，迈入真实的 Linux + Glibc 世界（后续实验我们以 x86-64 平台下编译的 C 程序为例）。我们按惯例先不急着上最复杂的动态链接，而是先迈出第一步：当我们用 `gcc` 编译一个调用了 `printf` 等库函数的真实 C 程序，并把库代码打包进程序时，生成的 ELF 文件多了哪些东西？那个把 ELF 载入内存的“神秘力量”（Linux 内核）到底是怎么工作的？程序在进入我们的 `main` 函数之前和退出 `main` 函数之后，底层究竟干了些什么？

---

## 1. GCC 编译链接与静态库：为什么只写了一个 `main` 却多出这么多段？

平时我们写一个最简单的 C 程序，往往只写了一个 `main` 函数，然后调了一个 `printf`。但在第三、四篇我们学过，如果调用了外部函数却没有提供它的定义，链接器 `ld` 是会报 `undefined reference to 'printf'` 的。之所以 `gcc` 没有报错，是因为 `gcc` 在背后默默帮我们把 **Glibc 的标准库代码** 以及 **C 运行时启动代码（CRT, C Runtime）** 一起链接进了最终的 ELF 文件里。

如果是静态链接（`gcc -static`），`gcc` 会把静态库 `libc.a`（本质上就是一堆编译好的 `.o` 文件的打包集合）里用到的 `printf.o`、`exit.o`，连同负责初始化 C 环境的 `Scrt1.o`（或 `crt1.o`）、`crtstuff.c` 等目标文件，像我们在第四篇讲的多文件静态链接那样，和我们自己的 `main.o` 组装在一起。

这就带来了一个直接的变化：既然塞进了这么多用于支撑 C 语言运行环境的辅助代码，我们在第四篇看到的只有 8 个 Section、2 个 Program Header 的极简 ELF 结构，自然也要跟着升级了。

### 1.1 再探真实 C 程序的 Section Headers 与符号表
我们用 `readelf -S` 和 `readelf -s` 来观察一下真实 C 程序编译出的 Section Headers 和 `.symtab` 符号表（这里截取和 C 运行时与基础结构相关的核心部分，动态链接特有的段我们留到第十篇再专门拆解）：

```text
Section Headers:
  [Nr] Name              Type             Address           Offset
       Size              EntSize          Flags  Link  Info  Align
  [ 0]                   NULL             0000000000000000  00000000
       0000000000000000  0000000000000000           0     0     0
  ...
  [ 2] .note.gnu.pr[...] NOTE             0000000000000338  00000338
       0000000000000030  0000000000000000   A       0     0     8
  [ 3] .note.gnu.bu[...] NOTE             0000000000000368  00000368
       0000000000000024  0000000000000000   A       0     0     4
  [ 4] .note.ABI-tag     NOTE             000000000000038c  0000038c
       0000000000000020  0000000000000000   A       0     0     4
  ...
  [12] .init             PROGBITS         0000000000001000  00001000
       000000000000001b  0000000000000000  AX       0     0     4
  ...
  [16] .text             PROGBITS         0000000000001080  00001080
       000000000000014a  0000000000000000  AX       0     0     16
  [17] .fini             PROGBITS         00000000000011cc  000011cc
       000000000000000d  0000000000000000  AX       0     0     4
  [18] .rodata           PROGBITS         0000000000002000  00002000
       0000000000000018  0000000000000000   A       0     0     4
  ...
  [21] .init_array       INIT_ARRAY       0000000000003da0  00002da0
       0000000000000008  0000000000000008  WA       0     0     8
  [22] .fini_array       FINI_ARRAY       0000000000003da8  00002da8
       0000000000000008  0000000000000008  WA       0     0     8
  ...
  [25] .data             PROGBITS         0000000000004000  00003000
       0000000000000010  0000000000000000  WA       0     0     8
  [26] .bss              NOBITS           0000000000004010  00003010
       0000000000000008  0000000000000000  WA       0     0     4
  [27] .comment          PROGBITS         0000000000000000  00003010
       000000000000002b  0000000000000001  MS       0     0     1
  [28] .symtab           SYMTAB           0000000000000000  00003040
       0000000000000390  0000000000000018          29    18     8
  [29] .strtab           STRTAB           0000000000000000  000033d0
       00000000000001f1  0000000000000000           0     0     1
  [30] .shstrtab         STRTAB           0000000000000000  000035c1
       000000000000011a  0000000000000000           0     0     1
```

再看一眼 `.strtab` 和 `.symtab` 里多出来的那些我们没写过的符号：

```text
Hex dump of section '.strtab':
  0x00000000 00536372 74312e6f 005f5f61 62695f74 .Scrt1.o.__abi_t
  0x00000010 61670063 72747374 7566662e 63006465 ag.crtstuff.c.de
  0x00000020 72656769 73746572 5f746d5f 636c6f6e register_tm_clon
  0x00000030 6573005f 5f646f5f 676c6f62 616c5f64 es.__do_global_d
  0x00000040 746f7273 5f617578 00636f6d 706c6574 tors_aux.complet
  0x00000050 65642e30 005f5f64 6f5f676c 6f62616c ed.0.__do_global
  0x00000060 5f64746f 72735f61 75785f66 696e695f _dtors_aux_fini_
............

Symbol table '.symtab' contains 38 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND 
     1: 0000000000000000     0 FILE    LOCAL  DEFAULT  ABS Scrt1.o
     2: 000000000000038c    32 OBJECT  LOCAL  DEFAULT    4 __abi_tag
     3: 0000000000000000     0 FILE    LOCAL  DEFAULT  ABS crtstuff.c
     4: 00000000000010b0     0 FUNC    LOCAL  DEFAULT   16 deregister_tm_clones
     5: 00000000000010e0     0 FUNC    LOCAL  DEFAULT   16 register_tm_clones
     6: 0000000000001120     0 FUNC    LOCAL  DEFAULT   16 __do_global_dtors_aux
     7: 0000000000004014     1 OBJECT  LOCAL  DEFAULT   26 completed.0
```

看到 `Scrt1.o` 和 `crtstuff.c` 了吗？它们就是 GCC 自动帮我们链接进来的 C 运行时辅助文件！为了支撑起一个标准 C 程序的生命周期，它们引入了四个专门用于**初始化**和**退出清理**的段：
1. **`.init` 段**：用于初始化的代码段（属性 `AX`，可执行）。在调用 `main` 函数之前（运行 `_call_init` 和 `__libc_start_main` 时），运行时库会先执行 `.init` 段里的指令做第一波初始化。
2. **`.init_array` 段**：这是一个存放**初始化函数指针的数组**（属性 `WA`），在符号表里对应 `__frame_dummy_init_array_entry`。在进入 `main` 之前，启动代码会遍历这个数组，依次调用里面的每一个函数指针（比如 `frame_dummy` -> `register_tm_clones`，又或者我们在 C 语言里用 `__attribute__((constructor))` 定义的构造函数）。
3. **`.fini_array` 段**：这是一个存放**退出析构函数指针的数组**，在符号表里对应 `__do_global_dtors_aux_fini_array_entry`。当 `main` 执行完毕退出、运行 `_dl_fini` 时，就会遍历这个数组，依次调用里面的函数（比如 `__do_global_dtors_aux` -> `deregister_tm_clones`）做退出清理。
4. **`.fini` 段**：用于结束收尾的代码段（属性 `AX`），在 `_dl_fini` 的最后阶段会执行这段代码。

此外，还有几个记录元信息的段：
* **`NOTE` 类型段**（`.note.gnu.property`、`.note.gnu.build-id`、`.note.ABI-tag`）和 **`.comment` 段**：记录了程序的 ABI 版本、编译 ID 和 GCC 编译器版本注释信息。
* **`GNU_STACK`（位于 Program Headers 中）**：还记得我们在第六篇说的吗？*“C 程序的堆栈段并不会被保存在 ELF 文件中，而是操作系统载入这个程序的时候自动分配的。”* 操作系统怎么知道该给堆栈分配什么权限呢？就是看 Program Headers 里的 `GNU_STACK` 表项：
  ```text
    GNU_STACK      0x0000000000000000 0x0000000000000000 0x0000000000000000
                   0x0000000000000000 0x0000000000000000  RW     0x10
  ```
  可以看到它的 `FileSiz` 和 `MemSiz` 都是 `0`，权限是 `RW`（可读可写但**不可执行**），用来告诉内核：给这个进程分配堆栈时千万别给 `E` 执行权限，防止黑客通过栈溢出往栈里写代码执行！

---

## 2. 揭秘第五篇的“神秘力量”：Linux 内核加载 ELF 的完整过程

在第五篇中，我们假设有一个“全知全能的神秘力量”能读懂硬盘里的 ELF Header 和 Program Headers，并把 `LOAD` 段搬进内存。在真实的 Linux 系统里，这个神秘力量就是**操作系统内核的 `execve` 系统调用**。我们来看看当我们在终端敲下命令时，内核到底是怎么把一个 ELF 程序跑起来的。

### 2.1 从 `bash` 输入命令到 `__do_execve_file`
首先，当我们在 `bash` 命令行当中输入某个 ELF 的文件名和对应的参数时：
1. `bash` 会先对这个输入的字符串进行解析，然后调用 `fork()` 得到一个子进程。
2. 子进程将解析得到的文件名 `filename` 作为参数 1，将解析得到的参数们构造成 `argv[]` 字符串指针数组作为参数 2，将 shell 变量当中的外部环境变量和 `export` 变量构造为 `envp[]` 数组作为参数 3，然后调用 **`execve(filename, argv, envp)`**，就可以将子进程替换为新的进程，开始运行我们的 ELF 程序。
3. `execve` 是一个 libc 的封装函数，之后它会触发系统调用进入内核态的 **`sys_execve`**：
   * `sys_execve` 先把文件路径名从用户态复制到内核态，然后调用 `do_execve` 执行，返回后释放之前复制的路径名。
   * `do_execve` 设置一下 `argv` 和 `envp` 指针结构，然后调用 `do_execveat_common(AT_FDCWD, filename, argv, envp, 0)`。这里的 `AT_FDCWD` 表示当前工作目录的文件句柄，`filename` 是之前的文件名，`argv` 和 `envp` 是稍作处理的参数和环境变量，`0` 是属性标志 `flags`。
   * `do_execveat_common` 直接调用核心函数 **`__do_execve_file(fd, filename, argv, envp, flags, NULL)`**（最后一个 `NULL` 是文件结构指针）。

### 2.2 `__do_execve_file` 的九步准备工作
`__do_execve_file` 负责统筹执行这个二进制文件，它的主要操作流程可以分为 9 步：

1. **拷贝文件表**：调用 `unshare_files` 函数将父进程打开的文件表拷贝并独立到子进程中。
2. **分配 `bprm` 结构**：设置一个 `bprm` 结构（`struct linux_binprm`，即 binary program）用于保存二进制程序加载过程中的全部信息，并调用 `kzalloc` 函数为 `bprm` 分配内核内存空间。
3. **打开目标可执行文件**：用 `do_open_execat` 函数打开需要执行的文件，将得到的文件结构 `struct file *` 保存到 `bprm->file` 当中；同时设置 `bprm->filename` 为对应的文件名（如果没有文件名就是 `"none"`；如果是从根目录 `/` 开始路径完整的文件名那就直接设置，如果不是就去 `/dev/fd` 里面查一下）；另外把 `bprm->interp`（载入可执行程序的解释器的文件目录）暂时也设置为这个 `filename`。
4. **初始化进程虚拟内存（`bprm_mm_init`）**：调用 `mm_alloc` 给 `bprm->mm` 提供一个全新的空 `mm_struct`，调用 `vm_area_alloc` 给 `bprm->vma` 提供一个空的 `vm_area_struct`（初始用户栈的 VMA），调用 `insert_vm_struct` 将这个空的 `vma_struct` 同时加入到 `mm_struct->mmap` 当中。
5. **准备参数页**：调用 `prepare_arg_pages`，该函数统计并检查 `argv` 和 `envp`，将计数设置到 `bprm->argc` 和 `bprm->envc` 中。
6. **设置权限并预读 128 字节文件头（`prepare_bprm`）**：调用 `bprm_fill_uid` 设置程序的 `uid`、`gid` 等权限信息，然后调用 **`kernel_read` 将可执行文件开头的前 128 个字节的文件头存到 `bprm->buf` 里面**（记住这 128 个字节，马上就要立大功了！）。
7. **拷贝参数字符串到新栈顶**：调用 `copy_strings` 将 `argv` 和 `envp` 的字符串内容拷贝到 `bprm->p` 指向的新用户堆栈栈顶。
8. **载入并执行二进制文件**：调用 **`exec_binprm(bprm)`** 执行载入和启动这个二进制文件（之后的核心就是这个函数！）。
9. **扫尾操作**：释放 `bprm` 等临时结构。

### 2.3 `search_binary_handler` 与 `linux_binfmt` 插件链表设计
`exec_binprm(bprm)` 主要就是调用里面的一个 **`search_binary_handler(bprm)`**，由它进一步去载入和执行程序。

这里我们先停下来思考一个架构设计问题：**我们的 Linux 系统可以执行多种多样的 binary 程序（比如 ELF 程序、`#!` 开头的脚本文件、甚至其他平台的二进制），不同的程序需要截然不同的方式进行加载，那我们要怎么用同一个 `search_binary_handler` 函数来载入不同的程序呢？**

诚然，我们可以用一系列的 `if-else` 来对 binary 文件的类型进行判断，但是这样当有新的 binary 文件格式出现的时候，就无法在不重新编译内核的情况下解决这个扩展性问题！因此 Linux 采用了更优雅的方案。

Linux 定义了一个叫做 **`linux_binfmt`** 的数据结构，结构如下：

```c
struct linux_binfmt {
    struct list_head lh;
    struct module *module;
    int (*load_binary)(struct linux_binprm *);    
    int (*load_shlib)(struct file *);
    int (*core_dump)(struct coredump_params *cprm);
    unsigned long min_coredump;     /* minimal dump size */
};
```

可以看到，这里的 `load_binary`、`load_shlib`、`core_dump` 分别是三个函数指针！针对不同的二进制文件类型，我们只需要分别为它们构造新的 `load_binary`（载入二进制程序）、`load_shlib`（载入动态共享库）、`core_dump`（出现异常崩溃的时候 copy 转储内存）函数，然后用 **LKM（可加载内核模块）** 的方式加入到全局链表即可。

这样我们每次载入程序的时候，只需要遍历 `linux_binfmt` 链表，然后用每个节点对应的 `load_binary` 函数尝试加载：
* 如果当前二进制程序正好就是这个 `binfmt` 的类型，那么 `load_binary` 在执行的时候就可以成功载入并正确返回，后面正常退出即可；
* 如果 `load_binary` 检查魔数发现不满足自己的类型，就返回错误的值，`search_binary_handler` 继续往后遍历查找下一个 `binary loader` 即可（如果全部遍历完都失败，就运行失败）。

注意，在遍历 `binfmt` 链表的时候**需要加读者锁（`read_lock`）**，以防止在打开和尝试加载文件的过程中链表被别人并发修改（比如正好有人加载新的 LKM 模块往链表里插入新的 `binfmt`）。

因为我们要运行的是 ELF 文件，所以顺利的话，当遍历找到 ELF 对应的 `binfmt`（即 `elf_format`）时，它的 `load_binary` 函数指针指向的就是 **`load_elf_binary`**！

### 2.4 `load_elf_binary`（静态固定地址加载流程）
现在接力棒交到了 `load_elf_binary(bprm)` 手上：

1. **解析 ELF Header 与 Program Headers**：
   因为在前面的 `prepare_bprm` 第 6 步中，我们已经把文件前 128 个字节读出来存在 `bprm->buf` 当中了（而 64 位 ELF Header 只有 64 字节），所以我们把 `bprm->buf` 指针类型强转改一改，直接就得到了 ELF Header 的数据！
   我们首先检查 image 满不满足要求（前 4 字节魔数是不是 `\x7fELF`），如果不是 ELF 就可以直接报错退出了；如果是 ELF，就根据 ELF Header 里的 `e_phoff`（offset）找到 `Program Headers` 并把它们全部读入内存。
2. **检查是否有 `INTERP` 段**：
   遍历每个 Program Header 的段，看看有没有类型为 `PT_INTERP` 的段（动态链接器路径）。对于我们本篇讨论的**静态链接程序**而言，它不需要动态链接器，所以没有 `INTERP` 段（包含 `INTERP` 段的动态加载分支我们留到第十一篇再详述）。
3. **映射所有的 `PT_LOAD` 程序段到虚拟内存**：
   接下来将所有的程序段映射到进程虚拟内存中（注意：这里只是建立虚拟内存映射，真正把文件内容读入物理内存要等到程序运行时触发 **Page Fault 缺页异常** 的时候按页载入，完全符合我们在第五篇讲的按页加载模型！）。
   我们遍历每一个 Program Header 的段，如果这个段的属性是 **`LOAD`**，那么它就是要被载入内存的段：
   * **权限设置**：检查 Program Header 的 `p_flags` 的 `W`、`R`、`X` 权限，对应设置内存段的 `X`、`W`、`R` 权限。
   * **地址映射（静态固定地址）**：如果程序的属性是静态固定地址的（即 ELF Header 中 `Type` 为 `EXEC`），那么直接根据 Program Header 规定的虚拟地址范围（`VirtAddr`），将对应的 `文件偏移 (Offset) + 文件大小 (FileSiz)` 的数据映射载入对应的虚拟地址范围即可，后面不足的地方（即 `MemSiz > FileSiz` 的 `.bss` 段部分）全部补 `0`。此时因为段地址是静态固定死的，需要给映射加上 **`MAP_FIXED`（fixed）标志**，于是 **`W`、`X`、`R`、`fixed` 四个标志**都被设置好了！（至于动态链接 / PIE 为什么要加随机 `bias`，我们在第九篇马上会讲）。
4. **设置返回用户态的程序入口**：
   最后检查是不是动态链接：如果是静态链接的话，没有 interpreter，那么就**直接将 C 代码编译得到的 `_start` 地址作为之后返回用户态的函数入口**，直接开始运行 C 代码得到的程序！

我们把从 `bash` 到 `load_elf_binary` 的整个内核加载流程画成一张完整的字符流程图（即**【内核过程】**）：

```text
+-----------------------------------------------------------------------------------+
|                            ELF 启动之【内核过程】全流程图                         |
+-----------------------------------------------------------------------------------+

  [用户态 bash]
    |-- 解析输入命令字符串，调用 fork() 创建子进程
    |-- 子进程构造参数1: filename, 参数2: argv[], 参数3: envp[]
    |-- 调用 libc 函数: execve(filename, argv, envp)
    v
======================== (系统调用陷入 Linux 内核态) ========================
    |
    v
  [sys_execve]
    |-- 复制路径名 -> 调用 do_execve() -> 返回后释放复制的路径名
    v
  [do_execve]
    |-- 设置 argv 和 envp -> 调用 do_execveat_common(AT_FDCWD, filename, argv, envp, 0)
    v
  [__do_execve_file(fd, filename, argv, envp, flags, NULL)]
    |-- 1. unshare_files()        : 将父进程打开的文件表拷贝到子进程中
    |-- 2. kzalloc()              : 分配 bprm (linux_binprm) 结构保存二进制程序信息
    |-- 3. do_open_execat()       : 打开执行文件存至 bprm->file，设置 filename 与 interp
    |-- 4. bprm_mm_init()         : mm_alloc(空mm_struct) -> vm_area_alloc(空vma) -> insert_vm_struct
    |-- 5. prepare_arg_pages()    : 将 argv 和 envp 计数信息设置到 bprm 中
    |-- 6. prepare_bprm()         : bprm_fill_uid 设置 uid/gid + kernel_read 读前128字节至 bprm->buf
    |-- 7. copy_strings()         : 将 argv 和 envp 字符串拷贝到 bprm->p 指向的堆栈栈顶
    |-- 8. exec_binprm(bprm)      : 核心载入与执行函数 (见下方)
    +-- 9. 扫尾释放操作
              |
              v
        [exec_binprm(bprm)] ---> [search_binary_handler(bprm)]
              |-- 加读者锁 (防止遍历时他人通过 LKM 修改链表)
              |-- 遍历 linux_binfmt 链表，调用节点的函数指针 load_binary(bprm)
              |     +-----------------------------------------------+
              |     | struct linux_binfmt {                         |
              |     |   struct list_head lh; struct module *module; |
              |     |   int (*load_binary)(struct linux_binprm *);  |
              |     |   int (*load_shlib)(struct file *);           |
              |     |   int (*core_dump)(struct coredump_params *); |
              |     | };                                            |
              |     +-----------------------------------------------+
              |-- 匹配到 ELF 的 binfmt，调用 load_elf_binary(bprm)：
              v
        [load_elf_binary(bprm)]
              |-- 1. 直接利用 bprm->buf 的前 128 字节解析 ELF Header，检查魔数
              |-- 2. 根据 offset 读取 Program Headers
              |-- 3. 扫描是否存在 INTERP 段 (若存在则 open_exec 打开 ld.so 并读头校验)
              |-- 4. 遍历所有 LOAD 段映射到虚拟内存 (物理页读盘延后至 Page Fault)：
              |       |-- 根据 Program Header 的 WRX 设置段权限 XWR
              |       |-- [静态固定程序] : 按 VirtAddr 映射 Offset+FileSiz，不足补0，加 FIXED 标志
              |       +-- [动态/PIE程序] : 加入随机偏移量 bias，调用 elf_map 映射
              +-- 5. 设置用户态入口点 (_start)：
                      |-- [静态链接] : 直接将 C 代码的 _start 作为之后的函数入口
                      +-- [动态链接] : load_elf_interp 载入 ld.so，将 ld.so 的 _start 作为入口
```

---

## 3. C 代码的启动与退出：从 `_start` 到 `main` 再到 `exit`

内核 `load_elf_binary` 跑完返回用户态后，CPU 终于跳到了 C 程序的入口 `_start`（对应我们在 ELF Header 里看到的 `Entry point address: 0x1080`）。那么从 `_start` 到我们的 `main` 函数，中间又经历了什么呢？

### 3.1 `_start` 与 `__libc_start_main`
1. **`_start` 函数**：
   这段汇编代码非常简单，它主要根据当前栈顶情况对传参寄存器进行设置，然后直接调用 **`__libc_start_main`** 函数开始运行即可；如果 `__libc_start_main` 运行返回了（正常情况下不会返回），就执行一条 `hlt` 指令触发异常结束程序。
2. **`__libc_start_main` 函数**：
   * 该函数传入的参数包括：`argc`、`argv`、`envp`、`auxvec`（辅助向量），动态链接器的结束函数 `rtld_fini` 地址，程序自身信息 `main`、`libc_csu_init`、`libc_csu_fini` 的地址，以及进入时的堆栈栈顶 `stack_on_entry`。
   * **早期 Glibc 的实现**：根据 `stack_on_entry` 的情况提取并设置 `argc`、`argv`、`envp` 等参数，然后调用 `atexit` 注册 `rtld_fini` 和 `_dl_fini`，接着运行 `_init`（调用 `.init` 和 `.init_array`）进行初始化，最后运行 `main(argc, argv, envp)`，并在 `main` 返回后调用 `exit` 退出。
   * **现代 Glibc（如 2.34+）的单步跟踪实测**：早期把初始化放在 `libc_csu_init` 的实现现在已经改变了。通过 GDB 单步跟踪，我们可以看到现代 `__libc_start_main` 的真实执行流程大致如下：
     1. 首先调用 `__cxa_atexit` 注册了退出清理函数 `_dl_fini`；
     2. 调用 `_init` 进行初始化，再遍历 `init_array_entry` 的函数做初始化（注：如果是动态链接程序，在前面的 `_dl_init` 阶段动态链接器已经调用过 `_call_init` 执行过 `.init` 和 `.init_array` 了）：
        * 在我们编写的简单 C 程序中，`.init` 段做的操作是检查 `__gmon_start__` 函数（用于 `gprof` 性能分析）是否被载入，如果地址不为空就调用 `__gmon_start__`；
        * 之后的 `.init_array` 保存在 `__frame_dummy_init_array_entry` 中，执行时会跳入 `frame_dummy` 函数，该函数直接进入 `register_tm_clones` 过程（用于 GCC 的事务内存 Transactional Memory 克隆表注册），该过程检查 `_ITM_registerTMCloneTable` 地址是否不为空，是的话就跳入运行，不然的话就略过。在我们的简单程序里这些弱符号都是 `NULL`，所以都直接略过了。
     3. 调用 `_dl_audit_preinit` 函数做一个审计相关的初始化通知；
     4. 最后调用 **`__libc_start_call_main`**，由该函数正式调用我们写的 **`main(argc, argv, envp)`** 函数！

### 3.2 C 代码的退出过程（`exit` -> `__run_exit_handlers`）
当我们的 `main` 函数运行完毕返回到 `__libc_start_call_main` 后，紧接着就会调用 **`exit`** 函数退出：
1. `exit` 函数内部核心是运行 **`__run_exit_handlers`**，它会取出我们之前通过 `atexit` / `__cxa_atexit` 注册的退出函数链表 `list`，开始依次运行里面的清理函数（其中最核心的就是之前注册的 `_dl_fini`）。
2. 对于每一个载入的文件（包括主程序自身以及所有的共享库），在退出时都需要按逆序执行析构清理：
   * 如果存在 `DT_FINI_ARRAY`（即 **`.fini_array` 段**），就遍历数组中的每一个函数指针并运行；
   * 如果之后还存在 `DT_FINI`（即 **`.fini` 段**），就运行 C 代码的 `_fini` 函数。
3. 在我们的简单 C 程序中：
   * 根据 `.fini_array`（`__do_global_dtors_aux_fini_array_entry`）会进入 **`__do_global_dtors_aux`** 函数；
   * 它首先运行 **`__cxa_finalize`** 函数做一些 C++ / Glibc 局部静态对象的关闭清理操作；
   * 然后运行 **`deregister_tm_clones`** 函数来做之前启动时 `register_tm_clones` 的反操作——检查 `_ITM_deregisterTMCloneTable` 是否存在，存在的话运行其中的函数；
   * 最后运行 `_dl_audit_objectclose` 关闭对象，最终调用内核的 `exit_group` 系统调用彻底终结进程。

## 小结
这一篇我们走完了真实 C 程序从静态编译、内核 `execve` / `load_elf_binary` 装载，再到 `__libc_start_main` 初始化、`main` 执行以及 `exit` 退出的完整生命周期。现在我们的 C 程序已经能在 Linux 上欢快地跑起来了。那么如果程序跑着跑着崩溃了，或者我们想用 GDB 单步调试它，ELF 文件又是怎么配合调试器工作的呢？下一篇我们就来探索 ELF 中专门用于堆栈回溯和调试的段。