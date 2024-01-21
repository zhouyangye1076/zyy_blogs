# ELF 的一些探索（三）：

这一节我们讲如何把一个 C 语法的程序转化为一个真实的汇编，进而转换为真实的可以使用的内存布局和硬盘存储。但是这个过程还是有点漫长，我们还需要一个循序渐进的过程。我们先从一个简单的 C 语法的程序开始吧，因为我们对于函数的细节还没有描述过，我们给一个没有函数的 C 语法的程序，所有定义的变量都认为在全局空间，也没有函数调用。这当然不能被一个真实的 C 编译器编译，只是我们为了方便表述和作为切入点的简化模型而已。

## 简单的例子
我们给出如下的例子，如果是真实的 C 程序那么就是代码被一个 main 函数包裹起来的，然后程序从 main 函数开始运行，然后最后离开 main 函数，变量 i、base 都被保存在局部空间。但是我们没讲过函数和局部空间。所以我们就认为我们程序一开始就直接从 main 标号开始运行，然后不断执行，最后停在死循环里面。最后加一个死循环是为了分析方便，如果不然按照顺序执行的规则就会一直向后执行，但是后面没有代码了，就没法分析执行了，就用一个循环让他一直停留在有代码的区域。i、base 这些变量当作全局变量处理处理。
```C
unsigned long long dest[4];
unsigned long long src[4]={
    0x9812736482919283,
    0x9876987698769876,
    0x0000123412341234,
    0x1234000012340000};

main:
    int i;
    unsigned long long base;
    base=0x1234000012340000;
    for(i=0;i<4;i++){
        if(src[i]>base){
            dest[i]=base;
        }else{
            dest[i]=src[i];
        }
    }
    while(1);
```

然后我们按照之前的文章讲的转换规则，将 C 语句转换为 对应的RISCV 的 asm 语句，这里我们额外加一些语法，使他成为可以被真实的汇编器编译的真实合法的汇编。（我们之前的汇编多多少少缺少了一些内容，或者有一些语法不合法） 

```
.global main

.section .text
main:
        #base=0x1234000012340000;
    la x1, other
    ld x1, 0(x1)
    la x2, base
    sd x1, 0(x2)
        #for(i=0;i<4;i++){
    la x1, i
    sw x0, 0(x1)
    j cond
loop:
        #x1=base
    la x1, base
    ld x1, 0(x1)
        #x2=src[i]
    la x2, src
    la x3, i
    lw x3, 0(x3)
    slli x3, x3, 2
    add x2, x2, x3
    ld x2, 0(x2)
        #if(src[i]>base)
    blt x1, x2, else
        #x3=&dest[i]
    la x3, src
    la x4, i
    lw x4, 0(x4)
    slli x4, x4, 2
    add x3, x3, x4
        #dest[i]=base;
    sd x1, 0(x3)
    j else_end
else:
    #x3=&dest[i]
    la x3, src
    la x4, i
    lw x4, 0(x4)
    slli x4, x4, 2
    add x3, x3, x4
        #dest[i]=base;
    sd x2, 0(x3)
else_end:
            #i++
    la x2, i
    lw x2, 0(x2)
    addi x2, x2, 1
    la x1, i
    sw x2, 0(x1)
cond:
            #i<4
    la x1, i
    lw x1, 0(x1)
    li x2, 4
    blt x1, x2, loop
        #}
exit:
    j exit

.section .data
src:
    .word 0x82919283
    .word 0x98127364
    .word 0x98769876
    .word 0x98769876
    .word 0x12341234
    .word 0x00001234
    .word 0x12340000
    .word 0x12340000
dest:
    .space 0x20
i:
    .space 0x4
base:
    .space 0x8
other:
    .word 0x12340000
    .word 0x12340000
```

我们来描述几个新的语法。

### .space 伪指令和 .word 伪指令
首先是 .space 伪指令，格式为`.space imm`，这个指令告诉汇编器我要在这个位置空出 imm 个字节，至于里面的数据是多少，我们不 care。汇编器在计算地址的时候如果遇到了 .space 就直接把之后下一条指令的地址作为当前地址加上掠过的字节数，然后继续计算地址。

我们为 dest、i 和 base 分配空间的时候就是在各自标号后面用 space 0x20, .space 0x4 和 .space 0x8 分别分配了 32 个字节、4 个字节和 8 个字节。

```
dest:
    .space 0x20
i:
    .space 0x4
base:
    .space 0x8
```

而`.word imm`则是分配 4 个字节，然后将 imm 的内容保存在这四个字节当中，全局变量的初始化就是一开始内存当中就保留了这个需要初始化的数据。另外规范的汇编注释请使用 #，而不是 C 的 //。

## .section 分段
第二就是 .section。我们可以看到我们的汇编指令到目前为止已经分为了至少指令部分和数据部分两部分，虽然在目前内存眼里他们是一视同仁的，但是我们可能希望对他们进行分开管理，让指令的归指令、数据的归数据，如果将来我们将有指令和代码分开进行不同形式的管理的需求，那么这个区分将会有很大的作用。即使从纯粹编程的角度出发，将代码和数据分开管理，也可以降低管理的难度。

我们在第一篇文章讲到，在 core 眼里内存是不分数据和指令的，它们的区别仅仅在于 core 是用 PC 访问他们还是 load-store 指令访问他们，所以每个内存既可以被读（load 操作访问）写（store 操作访问），也可以被执行（PC 访问）。这样恶意的用户就可以通过一些漏洞在本来应该是数据的部分写入一些恶意程序，然后跳入这段写入的恶意程序，从而达到劫持控制流、执行恶意程序的目的。

所以科学家们应用了一些安全机制来让不同的内存有不同的访问权限，例如将数据部分进分配可读可写权限、不可执行，这样即使被恶意注入了代码，对方跳入数据部分开始执行的时候也会因为这部分内容无法执行而出错，那么至少后续的攻击不会进展下去了。

所以我们可以用`.section .text`定义**代码段**，将所有要执行的指令放在这个段里，然后分配只可执行的权限，防止被恶意用户读写篡改执行内容；用`.section .data`定义**数据段**，将所有要读写的数据放在这个段里，然后分配仅能读写的权限，防止被恶意用户注入代码。

分段的设计不但在管理上，将汇编指令进一步细分管理做了汇编语法支持的剖分。我们之后还会介绍各种各样新奇的段，他们各自有自己的妙用（这个系列文章的本意其实只想介绍这些东西）。

同时也在安全机制上提供了有针对性的分段表示基础能力。毕竟我们原来指令在前、数据在后的编程方法只是人为地将内存划分为了指令、数据两部分，但是汇编器并不知道，那之后执行安全保护的时候也会因为不知道哪部分是指令、哪部分数据，而直接被迫给与 RWX 的最高权限，无法起到分段针对性保护的效果。

所以如果我们想要分配一个新的段，我们可以用`.section sectionname`的方法定义一个叫做 sectionname 的新的段。实际上代码段不一定要交 .text，叫 .code、.inst 甚至反直觉的 .data 也亦无不可，数据段也是一样，但是因为 C 语言的汇编器做了这样的选择，大家就普遍达成了这样的共识。

整个程序也并不是只能有一个代码段，只要它是被设计者用来存放指令的就是天生的代码段，所以可以根据而我们的心意有多个，这也为代码的进一步管理带来了便利。例如我们有很多的汇编代码，一部分用来执行初始化，一部分用来执行事务主逻辑，一部分用于执行杂项，那么就可以考虑自定义三个代码段，然后可以考虑取名为`.section .text.init`、`.section .text.task`、`.section .text.misc`，数据段也是一样。语法很多时候是自由的，语法的使用是需求而定，我们很多时候的规范只是方便编程、提高效率的范式。

### 分段原则
我们刚刚提到，指令和数据无论在设计上还是实现上，都没必要一股脑的都塞入 .text 段、.data 段，所以我们之前给的汇编的例子就可以根据一些分段的原则进一步细分。

1. 如果它对于编程者而言，在程序功能目的有所不同，那么就可以考虑分段
2. 如果它对于执行环境而言，可以在安全机制、执行机制等方面有所不同，那么就可以考虑分段

对于原则一，比如，我可能觉得 .text 一开始给 i 和 base 初始化这个过程太复杂了，后面这个数组变量取上限的过程也太复杂了，那么我们可以将两部分分别独立开来作为一个单独的段。例如初始化的部分叫做`.text.init`，取上限的部分叫做`.text.ceil`，于是我们把`.text`改写为如下：
```
.section .text.init
main:
        #base=0x1234000012340000;
    la x1, other
    ld x1, 0(x1)
    ...
    la x1, i
    sw x0, 0(x1)
    j cond

.section .text.ceil
loop:
        #x1=base
    la x1, base
    ld x1, 0(x1)
    ...
exit:
    j exit
```

对于原则二，我们可以来看 .data 段的数据组成。虽然我们一股脑地将所有数据都放入了 .data 段，但是我们会发现这些数据其实各有特点，我们也可以将不同类型的数据放到不同的段，来做不同机制的管理。

### rodata 段

像数据 other 并不是变量需要的数据，而是配合指令工作而产生的额外数据。这里我们为了快速载入立即数，所以生成了 other 变量（可以参看 li 伪指令那一节）。除此之外，诸如 switch-case 语句的跳转表、la 伪指令的地址存储等等也都属于这一类。这部分数据的特点是它的值在编译结束后是确定的，并且是不会被修改、也不能被修改的。所以我们可以这部分数据是只会被读，且只能被读的。

所以这些数据我们可以单独将他们分配到`.rodata`段（read only data 只读数据）当中，并且安全机制设置这一部分的数据是只读的。而其他数据作为变量是可读可写的，那么就需要同时具备读写的权限，那么就保存在`.data`段中。所以我们可以将数据段进一步划分为`rodata`和`data`两部分：
```
.section .data
src:
    .word 0x82919283
    .word 0x98127364
    .word 0x98769876
    .word 0x98769876
    .word 0x12341234
    .word 0x00001234
    .word 0x12340000
    .word 0x12340000
dest:
    .space 0x8
i:
    .space 0x4
base:
    .space 0x8

.section .rodata
other:
    .word 0x12340000
    .word 0x12340000
```
既然只读的数据放到`rodata`段，我们来看一个特例。const 类型的变量也是制度的，比如`const int x=0x100;`中的变量 x，虽然编译器确保了不能写直接写变量 x 的语句，但是我们依然可以用比如指针等其他隐蔽的方式改写 x，所以 C 编译器会选择将 const 类型的变量也放入`rodata`段，让硬件的权限机制、安全机制确保变量 x 在编译器静态分析的时候不会被修改，在动态执行的时候也真的不会被修改。

## 链接脚本

链接分很多种，有多文件的、单文件，有静态的、动态的，我们现在只讲单文件的汇编文件怎么静态链接得到对应的可执行程序。当然我们按惯例先不讲技术，先讲需求。

我们继续以之前那个进一步分段之后的汇编代码为例子，现在我们要将他转化为真实的内存布局，毕竟汇编的目的是成为二进制，然后躺在内存中分配的位置，等待被 CPU 按部就班的执行。那么我们看看之前的汇编还缺什么吗？指令有了、数据有了，地址还没有！每个段的起始地址还没有，进而符号的地址还没有，没有办法把符号相关的跳转和访存操作转化为对应的绝对编码指令。

### 汇编
虽然上面提到会有部分汇编指令无法被编译，但是还有很多指令时可以被编译：

- 没有起始地址和符号地址，但是每个段内所有不涉及符号的指令都可以转换为对应的二进制，比如算术指令等
- 如果跳转地址的符号和相关的指令都是在一个段内的，因为跳转使用的是相对地址，所以已知偏移量也可以编译
- 如果变量地址的符号和相关的指令都在一个段内，且使用 la 指令得到地址，那么偏移量已知，会自动使用 PC 相对寻址来生成指令

下面这些汇编指令就没法被编译了：

- 使用 %hi 和 %lo 计算符号值的，因为绝对地址不知道，所以 %hi 和 %lo 无法被使用（因此使用 la 计算地址而不是 %lo 和 %hi）
- 使用符号参与计算的，如果不能转换为段内符号的偏移量问题也无法被编译得到
- 跳转和访存的符号和指令是在不同的段的，无法知道段与段之间的相对位置，无法做相对跳转和 PC 相对寻址

我们可以使用 gcc 对汇编文件做编译，gcc 会自动把这些可以得到指令数据二进制的部分先编译掉，对于无法编译的部分可以先编译一部分，然后额外保留没有编译完的指令位置和依赖的符号信息，等后续链接的时候确定符号地址，进而将没有编译完的部分编译完。我们可以执行如下指令编译一个 .S 汇编文件：
```
    riscv64-linux-gnu-as 1.S -o 1.o
```
riscv64-linux64-gnu-as（以下简称 as）是 riscv64-linux64-gnu-gcc（以下简称 gcc）的一个组件，gcc 负责将 C 程序转换为 RISCV64 的汇编，然后调用 as 将汇编编译为二进制。所以虽然很多时候我们用 gcc 可以将一个 .c 或者 .S 一步到位编译为可执行文件，但是其实 gcc 暗中调用了很多组建，比如 as 来执行一些中间过程。

as 本身也是一个 C 可执行程序，他可以和 C 一样传入命令行参数，比如上面就将`1.S`、`-o`、`1.o`三个字符串传入作为参数，然后 as 内部解析这些命令开始执行特定操作。`-o`是要输出的文件，后面跟着的字符串会被认为是要输出的文件的文件名，这里是`1.o`，所以输出的目标文件是`1.o`。其他的 .S 文件被输入会被当作要编译为的汇编文件，所以 as 在该命令下做的事就是：将 1.S 这个汇编文件编译得到等待被链接的中间文件 1.o。

如果没有`-o`参数，as 会一步到位生成可执行文件，缺失的符号地址它可以用自己的一些内置配置来生成，但是如果符号太复杂它 handle 不了，那就不能得到可执行文件了。也可以用 gcc 来编译得到中间文件 obj，例如使用如下指令：
```
    riscv64-linux-gnu-as -c 1.S -o 1.o
```
`-c`说明是要生成 obj 中间文件，而不是可执行文件，不然它也会自动尝试 handle 符号得到可执行文件。

我们可以用 riscv64-linux-gnu-objdump（以下简称 objdump）来反汇编 obj 内部的汇编编码情况。执行如下指令即可：
```
    riscv64-linux-gnu-objdump -d 1.o > 1.asm
    riscv64-linux-gnu-objdump -D 1.o > 1.asm
```
`-d`只会输出代码段的汇编，但是`-D`可以得到所有段的汇编，所以方便起见可以用`-D`。我们来看一下此时内部的部分汇编：
```
Disassembly of section .text.init:

0000000000000000 <main>:
   0:	00000097          	auipc	ra,0x0
   4:	00008093          	mv	ra,ra
   8:	0000b083          	ld	ra,0(ra) # 0 <main>
   ...
  24:	06c0006f          	j	90 <cond>

Disassembly of section .text.ceil:

0000000000000000 <loop>:
   0:	00000097          	auipc	ra,0x0
   4:	00008093          	mv	ra,ra
   8:	0000b083          	ld	ra,0(ra) # 0 <loop>
   ...
  20:	00219193          	slli	gp,gp,0x2
  24:	00310133          	add	sp,sp,gp
  28:	00013103          	ld	sp,0(sp) # c <loop+0xc>
  2c:	0220c463          	blt	ra,sp,54 <else>
  ...
0000000000000090 <cond>:

```
可以看到 slli、add 这些符号无关的指令都被编译成了对应的二进制；blt 这种段内相对偏移量跳转的指令也被编译完全。所以可以不依赖绝对地址的指令都可以直接被汇编器编译得到最终的二进制。

对于每个段，我们可以看到每个段的起始地址都是 000000000，因为这些段的起始地址无法确定，故而只能先不与填写。我们来看原来的访存操作的伪代码是如何转换的：
```
    la x1, other
    ld x1, 0(x1)

    ->

0:	00000097          	auipc	ra,0x0
4:	00008093          	mv	ra,ra
8:	0000b083          	ld	ra,0(ra) # 0 <main>
```
可以看到这里试图进行 PC 相对寻址，但是因为我们这里是 .text.init 段访问 .data 段，是跨段的符号访问，所以无法确定需要加上的偏移量的值。所以我们的 auipc 指令和立即数和 ld 指令的立即数都暂时是 0，后续链接的时候如果知道了这个偏移量得知，后续就可以将这两个立即数填充，然后得到可以正确执行的代码。

我们再看跨段落的跳转语句是如何转换：
```
    j cond

    ->

24:	06c0006f          	j	90 <cond>

Disassembly of section .text.ceil:
  ...
0000000000000090 <cond>:
```
我们可以看到，虽然生成了跳转指令，而且似乎对应的跳转地址 90 也是正确的 cond 的地址，但是我们可以看到 90 是 cond 在 .text.ceil 的段内偏移，并不是全局的偏移，所以它只是暂时用符号的段内偏移作为跳转地址的计算，实际上的偏移量还有待于之后得到确定的偏移地址之后再行计算。

### 链接脚本
之所以我们之前的编译只是半成品，就是因为我们没有办法确定每个段的起始地址，如果我们得到了起始地址，那么自然也就可以得到完整确定的内存布局，得到最终的编译结果。这里我们使用`.ld`文件作为链接脚本，这个脚本给出了每个段的起始地址、顺序、组合方式等等信息，利用这个脚本就可以计算得到需要的决定地址，完成符号的进一步编译。

我们给出一个 ld 脚本的实例：
```
    OUTPUT_ARCH( "riscv" )
    ENTRY(main)

    SECTIONS
    {
    . = 0x10000
    .text : { 
        *(.text.init)
        *(.text.ceil)
    }
    . = ALIGN(0x10);
    .data : { *(.data) }
    }
```
好了我们来逐行分析一下这个脚本是干什么的。

首先是`OUTPUT_ARCH( "RISCV" )`，这个部分说明当前的汇编架构为 RISCV 架构。

`ENTRY(main)`我们先略过，这是用来实现另一个问题的。

#### SECTIONS 段落排布
后面的`SECTIONS{}`列表记录了每一个段的布局顺序、起始地址和组合方式，以及其他的它的信息。

`.`是当前开始排布段落的地址，我们的 SECTIONS 默认的起始地址如果不规定默认是 0x0 开始的，那么我们之后开始排布段落就是从 0x0 地址开始。我们可以用`. = 0x10000`来将当前的地址变为 0x10000，然后我们排布段落的起始地址就是 0x10000。

然后我们开始排布第一个段，这里我们声明链接后的文件的第一个段`.text`，所以从 0x10000 开始排布了 target 的`.text`段，然后是`*(.text.init)`，这里的格式是`文件名(段名)`，* 是通配符说明可以是任何字符、也就是可以是任何文件，所以所有参与链接的文件的 .text.init 段都会被放置在这个位置，作为 target 的 .text 的第一部分。然后`*(.text.ceil)`就是将参与文件的 .text.ceil 作为 .text 的第二部分。
```
.text : { 
    *(.text.init)
    *(.text.ceil)
}
```
如此我们就将 .text 的内容排布完毕了。因为第一个段的其实排布地址已经有了，所以之后的第一个段内部标号地址也可以依次计算出来了。

那么如果有多个文件都有 .text.init 段呢？那么这些文件的段会被按照任意次序组装在这里。这是常见的，我们在 C 做多文件编程的时候，就会把多个 .c 分别编译为 .o，然后链接得到可执行文件，所有 .o 的代码部分也就是 .text 就会被链接组装为一个大型的 .text。如果想对不同文件的 .text.init 做一个排序，那么可以把文件名特意写出来，比如如下：
```
.text : { 
    1.o(.text.init)
    2.o(.text.init)
    ...
    *(.text.ceil)
}
```

之后我们渐次排放之后的段落，声明要给目标文件排列的段的名字，和有哪些被链接文件的哪些段按什么顺序组成这些段。例如`.data : { *(.data) }`将所有文件的 .data 按任意次序组装为自己的 .data。段与段的声明如果是直接相邻的，那么上一个段的结束位置，就是下一个段的起始位置。
```
. = ALIGN(0x10);
.data : { *(.data) }
```
如果我们不希望下一个段直接从上一个段的末尾开始排布，我们可以用`. = imm`的方式重新设置开始排列的地址，用`. = imm`的方法修改排布段落的起始地址时候需要注意 imm 的地址不能比上一个段的结束位置小，不然会使得段落之间重叠的，即使不重叠，也会违背段落从低地址向高地址排布的规则。所以除非明确知道自己想要排布的内存地址，不然一般不使用`. = imm`。比如说我有一块专用内存物理地址是从 0x20000000 开始的，而我下面这个段就必须从这个物理内存开始排布，那么我们可以用`. = 0x20000000`，这一般只在启动代码或者嵌入式编程会遇到，平时是用不到的。

第二种，希望和上一段之间可以间隔某个特定的距离，比如 0x1000，那么可以用`. = . + interval`的方式，这样排布地址就会变为上个段结束地址，加上指定的偏移。

第三种，希望段的起始地址做一些地址对齐操作。比如数据段当中的第一个变量可能是 double word，它的访存是要求 8 字节对齐，所以需要起始地址也是 8 字节对齐。有时候一些数组为了满足和 cache line 对齐，会是自己是 16 字节对齐的，所以出于访存时候效率的考量一般段起始地址至少 16 字节对齐，所以可以用`. = ALIGN(0X10)`来得到 16 字节对齐的地址。此外有的时候因为分页管理的要求，段起始地址需要是 0x1000 对齐的，那么可以用`. = ALIGN(0X1000)`来实现页对齐。

分段之间可以这么做，段内部组装一个段的时候也可以这么做。比如：
```
.text : { 
    *(.text.init)
    . = ALIGN(0X10);
    *(.text.ceil)
}
```
特别注意，`. = exp`这些语句必须用`;`结尾，不然会语法错误。这次如何将所有待链接文件的各个段组装起来，确定符号地址的任务已经可以实现了。

#### ENTRY 程序入口
现在还有最后一个问题，程序需要执行的第一条指令的位置还没有确定，我们得到了内存布局，但是还没有 PC 寄存器的值。所以 ld 脚本提供了`ENTRY(main)`，ENTRY 后面填入的 label 就是 PC 开始执行的位置。比如我们希望从 main 标号开始执行，就填入 main 标号。

如果我们直接链接会出现提示说`main`这个标号不存在，因为我们每个文件里面的标号不加以特别说明都是文件内部可见的，这样就可以防止不同文件因为符号名相同而出错的问题。所以当我们的链接文件去 1.o 里面找 main 标号的时候会找不到，所以我们需要将这个符号修改为外部文件可见，这种外部文件可见的符号就是用于文件之间相互定义、调用，然后被链接的。外部声明的方法如下，在文件开头用 .global 伪指令修饰即可：
```
    .global main
```

#### 执行链接
我们使用 riscv64-linux-gnu-ld（之后简称 ld）来执行链接操作，可以执行如下的指令：
```
    riscv64-linux-gnu-ld -T link.ld 1.o -o 1.out
```
`-T`后面指示的是用来链接的 ld 脚本的文件名，这里的链接脚本是`link.ld`；`-o`指示的是链接生成的可执行文件的文件名，这里生成的文件是`1.out`；然后其他的文件名就是被链接的 .o 文件。

#### 链接前后对比
现在我们用 objdump 看看链接之后的汇编有没有什么区别。
```
Disassembly of section .text:

0000000000010000 <main>:
   10000:	00000097          	auipc	ra,0x0
   10004:	0d008093          	addi	ra,ra,208 # 100d0 <other>
   10008:	0000b083          	ld	ra,0(ra)
   1000c:	00000117          	auipc	sp,0x0
   10010:	10010113          	addi	sp,sp,256 # 1010c <base>
   10014:	00113023          	sd	ra,0(sp)
   10018:	00000097          	auipc	ra,0x0
   1001c:	0f008093          	addi	ra,ra,240 # 10108 <i>
   10020:	0000a023          	sw	zero,0(ra)
   10024:	0940006f          	j	100b8 <cond>
```
对于每个段都有了自己的起始地址，而不是像原来一样从 0 开始编号：
```
0000000000000000 <main>:
    ->
0000000000010000 <main>:
```
对于跨段的内存访问的部分，PC 相对寻址的偏移量已经被计算出来，然后填入指令当中了：
```
   0:	00000097          	auipc	ra,0x0
   4:	00008093          	mv	ra,ra
   8:	0000b083          	ld	ra,0(ra) # 0 <main>
    ->
   10000:	00000097          	auipc	ra,0x0
   10004:	0d008093          	addi	ra,ra,208 # 100d0 <other>
   10008:	0000b083          	ld	ra,0(ra)
```
跨段跳转的地址也已经被确定了，而不是把标号的段内偏移当作临时的目标地址：
```
  24:	06c0006f          	j	90 <cond>
    ->
  10024:	0940006f          	j	100b8 <cond>
```
