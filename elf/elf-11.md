# ELF 的一些探索（十一）：动态链接器 `ld.so` 与共享库载入全流程

在上一篇中，我们彻底拆解了动态链接 ELF 文件里的 `.dynamic`、`.got`、`.plt`、`.rela.dyn`、`.rela.plt` 和 `.gnu.hash` 段。我们知道，只要有人能在程序运行前帮我们把 `.dynamic` 里声明依赖的 `mylib.so` 和 `libc.so.6` 从硬盘里找出来映射进内存，再算出符号地址填进 `.got` 表，整套动态链接机器就能严丝合缝地运转起来。

那么，这个在幕后干了所有脏活累活的“大管家”是谁呢？它就是记录在 ELF 文件 `.interp` 段中的**动态链接器（Dynamic Linker / Interpreter，在 64 位 x86 Linux 下就是 `/lib64/ld-linux-x86-64.so.2`）**。

这一篇我们就顺着内核 `load_elf_binary` 的动态链接分支，一路单步跟踪进 `ld.so` 的 `_start`、`_dl_start`、`dl_main`、`_dl_init`，直到 `main` 函数执行并通过 `exit` / `_dl_fini` 卸载所有共享库退出，拼上 ELF 探索之旅的最后一块拼图！

---

## 1. 内核的“偷梁换柱”：`.interp` 段与 `load_elf_interp`

我们先回看一眼 `./dynamic` 程序中的 `.interp` 段（对应 Program Headers 中的 `INTERP` 段）：

```text
Hex dump of section '.interp':
  0x00000318 2f6c6962 36342f6c 642d6c69 6e75782d /lib64/ld-linux-
  0x00000328 7838362d 36342e73 6f2e3200          x86-64.so.2.
```

`.interp` 段里只存了一个字符串：`/lib64/ld-linux-x86-64.so.2`。

现在我们回到第七篇讲过的内核加载函数 **`load_elf_binary`**，看看当它遇到一个包含 `INTERP` 段的动态链接 ELF 文件时，会多做哪些操作：
1. **读取并打开 `ld.so`**：
   `load_elf_binary` 遍历主程序的每个 Program Header，找到类型为 `PT_INTERP` 的段。内核首先为 `interp` 段分配内核空间，读入里面的内容（即解释器文件名 `/lib64/ld-linux-x86-64.so.2`），然后检查这个文件名是否合法。合法的话就调用 **`open_exec`** 打开这个 `ld.so` 文件，之后读入 `ld.so` 的前 128 字节文件头，检查它是不是一个合法的 ELF 文件（因为动态链接器自身也是一个 ELF 共享库文件！），并载入 interpreter 的 Program Headers。
2. **映射主程序的 `LOAD` 段**：
   如同第九篇所讲，将主程序所有的 `LOAD` 段加上一个随机偏移量 `bias`，调用 `elf_map` 映射到虚拟地址空间中。
3. **载入解释器并“偷梁换柱”修改入口点（`load_elf_interp`）**：
   接着检查发现当前程序是动态链接的（存在 `interp`），于是调用 **`load_elf_interp`** 将动态链接器 `ld.so` 对应的 `LOAD` 段也映射到进程的虚拟内存中！
   最关键的一步来了：如果是静态链接，内核直接将主程序 C 代码的 `_start` 作为返回用户态的函数入口；但**因为是动态链接，内核会将 interpreter（`ld.so`）的 `_start` 作为之后返回用户态的函数调用入口**（同时把主程序 C 代码真正的 `_start` 地址保存在用户栈顶 `auxv` 序列的 `AT_ENTRY` / `user_entry` 中）！

这样一来，一旦 `execve` 系统调用返回用户态开始运行，CPU 就会**直接进入动态链接器 `ld.so` 的 `_start`**，开始做一系列初始化和动态链接的操作！

---

## 2. 动态链接器的自举（Bootstrap）：从 `_start` 到 `_dl_sysdep_start`

因为一开始的启动位置是 interpreter（`ld.so`）的 `_start`，所以我们就从这个位置开始跟踪。`_start` 本身只有几行汇编，是一个跳板，它把当前栈顶指针 `%rsp` 作为参数直接调用 **`_dl_start`** 开始运行。

### 2.1 `_dl_start`：动态链接器自身的重定位（自举）
`_dl_start` 负责动态链接器的启动，因此首先需要将动态链接器自身的代码做一个初始化。

这里有一个非常有意思的问题：**动态链接器是来帮别人做重定位的，可动态链接器自己的代码在被内核 `load_elf_interp` 载入内存时，也是被重定向过的（即也被加上了一个随机偏移量 `bias`）！如果它自己都没重定位好，它怎么去运行代码帮主程序重定位呢？**

所以，动态链接器必须先给自己做重定位（这个过程叫做 **Bootstrap 自举**）：
1. `_dl_start` 首先在栈上构造一个临时的 `bootstrap`（`link_map`）数据结构用来设置一系列自身的数据；
2. 算出动态链接器自身的加载偏移 `bias`，然后将自己需要的段地址全部加上 `bias` 的偏移；
3. 对于动态链接器自身的 `.rela.dyn`、`.rela.plt` 等数据段当中的重定位地址，也全部加上这个 `bias`，使得动态链接器所有段落当中的数据都变为加上 `bias` 之后的正确数据。
4. 完成动态链接器自身的重定位之后，调用 **`_dl_start_final`** 开始下一部分操作。

注意，`_dl_start` 传入的参数是当时 `%rsp` 的值，也就是用户堆栈的栈顶。正如我们在第九篇画过的栈顶排布图那样，此时如果我们查看栈顶内存的数据，就会看到栈顶依次躺着内核在之前阶段压入的 `argc`、`argv[]`、`envp[]`、`auxv[]` 等数据的值。

### 2.2 `_dl_start_final` 与 `_dl_sysdep_start`
1. **`_dl_start_final(arg)`**：
   接收栈顶参数 `arg`，然后对管理动态链接器自身的全局 `link_map`（即 **`_dl_rtld_map`**）设置一些变量，设置好 `hash_table`，然后调用 **`_dl_sysdep_start(arg, dl_main)`**。
2. **`_dl_sysdep_start(arg, dl_main)`**：
   传入的 `arg` 是栈顶地址（参数序列），`dl_main` 是之后要运行的主函数指针：
   * 首先调用宏 **`DL_FIND_ARG_COMPONENTS`** 将 `arg` 栈顶里面的变量读出来，分别写入到 **`_dl_argc`**、**`_dl_argv`**、**`_environ`**、**`_dl_auxv`** 四个全局变量当中（栈顶布局回顾第九篇：第 1 个 8 字节是 `argc=1`，接着是 `argv` 字符串指针数组并以 `NULL` 结尾，接着是 `envp` 字符串指针数组并以 `NULL` 结尾，最后是 `_dl_auxv` 指向的辅助向量序列，而 `argv` 和 `envp` 的具体字符串内容依次排在 `auxv` 的后面）。
   * 之后遍历 `auxv` 序列：在这个序列中内核压入了一系列的 `(type, value)` 变量，我们解析参数的类型，将它们填入对应的管理变量当中——其中**最要紧的就是 `AT_ENTRY`，也就是动态链接完成后要跳入的 C 程序主程序的开始地址 `user_entry`（即主程序的 `_start`）**！
   * 等所有的参数解析设置完毕之后，正式调用 **`dl_main`** 开始下一步核心操作！
   * 当 `dl_main` 执行完毕返回之后，代码会直接把堆栈修改回 **`_dl_start_user`** 的地方继续下一步操作（之前调的那几个函数的栈帧直接就被抹掉没有了）。

---

## 3. `dl_main` 的十二步核心工作流全解

`dl_main` 是整个动态链接器的心脏，主程序的解析、环境变量的处理、所有依赖共享库（`.so`）的搜索与装载、依赖顺序的拓扑重排以及全局符号重定位，全部都在 `dl_main` 的这 12 步里完成。我们一步一步来拆解：

### 第 1 ~ 7 步：初始化全局结构、解析环境变量与主程序 `main_map`
1. **初始化全局变量**：对动态链接器的一些全局变量进行初始化，主要就是 `GL` 系列的全局结构体成员。
2. **解析 `LD_` 环境变量（`process_envvar`）**：
   调用 `process_envvar` 函数处理环境变量，中间最主要的是处理以 **`LD_`** 开头的特殊环境变量（比如大家熟知的 **`LD_PRELOAD`**、**`LD_LIBRARY_PATH`** 等等）。它通过循环调用 `_dl_next_ld_env_entry` 函数，在环境变量数组中依次寻找 `LD_` 开头的环境变量，如果是指定的特殊环境变量就将它的值保存起来（比如把 `LD_PRELOAD` 保存到 `preloadlist`，把 `LD_LIBRARY_PATH` 保存到 `library_path`）。
3. **创建主程序的 `main_map`（`_dl_new_object`）**：
   因为每个被加载的 ELF 文件都需要有一个 `link_map` 结构来表示和管理它的全局数据信息，因此调用 `_dl_new_object` 为我们的 C 主程序生成一个新的 `link_map` 变量并做适当初始化，然后调用 `_dl_add_to_namespace_list` 将这个 `link_map` 加入到全局的命名空间链表（`_dl_ns[LM_ID_BASE]._ns_loaded`）当中。
   * *注意区分两个核心 `link_map`*：我们前面在 `_dl_start` 自举时用的那个 `link_map` 叫做 **`_dl_rtld_map`**，它是**动态链接器（`ld.so`）文件自身的 `link_map`**；而这里新建的 `link_map` 叫做 **`main_map`**，它是**我们编写的 C 主程序的 `link_map`**！
4. **遍历主程序的 Program Headers**：
   遍历主程序的每一个 Program Header 来记录每个段需要的特殊数据的值：
   * 如果是 **`PT_PHDR`**（第一个程序头），正如我们在第九篇讲的，计算一下它记录的相对起始位置和此时这个程序头在内存中的真实位置，**两者的差值就是主程序的随机加载偏移量 `bias`**！将它记录在 `main_map` 的 **`l_addr`** 中。
   * 之后计算 `DYNAMIC`、`INTERP`、`LOAD` 等段的起始和结束位置的时候，都需要加上这个 `l_addr` 偏移：`l_ld` 记录 `.dynamic` 段的真实地址、`l_map_start` 记录 `LOAD` 段们的开始位置、`l_map_end` 记录 `LOAD` 段们的结束位置、`l_text_end` 记录代码段的结束位置等等。
   * 其他段如 `TLS`、`STACK`、`RELRO` 也各自载入需要的数据，最后对上面载入的所有数据做一个检查并各自处理。
5. **解析主程序的 `.dynamic` 与 Hash 表**：
   调用 **`elf_get_dynamic_info`** 和 **`_dl_setup_hash`**，解析得到 C 主程序的 `.dynamic` 段信息（存入 `main_map->l_info`）和 `.gnu.hash` 符号哈希表信息，就像之前在 `_dl_start` 里对 `_dl_rtld_map` 做的那样。
6. **初始化共享库搜索路径（`_dl_init_paths`）**：
   调用 `_dl_init_paths` 来处理 `.dynamic` 段的 `DT_RPATH`、`DT_RUNPATH`，将它们分别保存到 `link_map` 的 **`l_rpath_dirs`** 和 **`l_runpath_dirs`** 当中；然后解析第 2 步 `process_envvar` 得到的 `LD_LIBRARY_PATH` 变量（里面多个路径是用冒号 `:` 隔开的），做好切片之后，将路径存入对应的数据结构，保存到 **`env_path_list`** 中。
7. **将 `_dl_rtld_map` 挂载入链表**：
   设置 `_dl_rtld_map` 的 `l_name`，将 `_dl_rtld_map` 加入到 `main_map` 所在的全局链表中，并设置好 `_dl_rtld_map` 的 `l_phdr`、`l_phnum`、`l_relro_addr`、`l_relro_size`、`l_tls_modid` 等字段。

### 第 8 ~ 9 步：预加载（`LD_PRELOAD` 与 `/etc/ld.so.preload`）及六级搜库优先级
8. **载入 `LD_PRELOAD` 指定的共享库**：
   第 2 步的 `process_envvar` 函数已经将 `LD_PRELOAD` 的字符串保存到了 `preloadlist` 当中。现在将该字符串拷贝出来，用 `strsep` 函数依次切片，得到需要预先载入的 `.so` 文件名，然后依次调用 **`do_preload`** 函数把这些文件载入进来。

   我们顺着 **`do_preload`** 的调用链往里看，看看一个 `.so` 共享库到底是怎么被找到并装载进内存的：
   * **`do_preload` -> `_dl_catch_error` -> `map_doit` -> `_dl_map_object`**：
     `do_preload` 主要就是调用 `_dl_catch_error` 函数来实现带错误捕获的共享库载入。`_dl_catch_error` 的前三个参数都是用于返回 error 信息的变量，第四个参数是要执行的操作函数指针（这里是 `map_doit`），第五个参数是传给 `map_doit` 的参数 `args`（包含文件名和 `main_map`）。`map_doit` 其实仅仅是一个为了把 `args` 参数解包打开、让 **`_dl_map_object`** 可以在 `_dl_catch_error` 中运行而做的外壳（shell）而已。如果载入成功，新文件的 `link_map` 会被保存到全局链表 `_dl_ns[LM_ID_BASE]._ns_loaded` 当中，我们检查有无错误以及 `_ns_loaded` 是否加入了新的 `link_map`，就知道载入是否成功。

   * **`_dl_map_object` 的六级共享库搜索路径**：
     当 `_dl_map_object` 要根据一个文件名（比如 `"mylib.so"`）去寻找并打开文件时，它严格遵循以下 **6 级搜索优先级**：
     1. **第 1 级（检查是否已载入内存）**：首先遍历全局 `_dl_ns[LM_ID_BASE]._ns_loaded` 链表（该链表记录了所有已经载入的共享库的 `link_map`）。如果某个节点的共享库已经被载入了（`l_removed` 和 `l_faked` 没有置位），我们就检查它的名字（`l_name` 和 `l_libname`）是不是我们要找的 name，如果是的话直接返回该 `link_map` 结束，不需要重复加载！
     2. **第 2 级（搜索 `DT_RPATH` 路径链）**：如果没找到，则打开自己的加载者（loader）的 `l_rpath_dirs`（即编译时通过 `RPATH` 设置的文件路径），在这些路径下使用 `open_path` 依次查询有没有这个文件（成功就返回有效的文件描述符 `fd`）。
        * *注意嵌套加载的情况*：因为载入文件可能是层层嵌套的（比如主程序载入 `libA.so`，而 `libA.so` 又需要载入 `libB.so`），当 `libB.so` 在自己的 `DT_RPATH` 查找失败时，会顺着父共享库（`libA.so`）的 `l_rpath_dirs` 不断往上找；如果都没找到，最后还会找主程序 `main_map` 的 `l_rpath_dirs`！
     3. **第 3 级（搜索 `LD_LIBRARY_PATH` 环境变量）**：如果所有的 `DT_RPATH` 都没有找到，就查找 `env_path_list` 中的目录（即环境变量 `LD_LIBRARY_PATH` 指定的目录），用 `open_path` 检查有没有这个文件。
     4. **第 4 级（搜索 `DT_RUNPATH` 路径）**：如果还没有，就用当前加载者 `l_runpath_dirs` 中的目录（即 `.dynamic` 的 `DT_RUNPATH` 字段指定的目录），用 `open_path` 检查有没有这个文件。
     5. **第 5 级（搜索 `/etc/ld.so.cache` 缓存）**：如果还没有，就调用 `_dl_load_cache_lookup` 检查 `/etc/ld.so.cache` 缓存文件，查看里面记录的文件路径有没有该文件。
     6. **第 6 级（搜索系统默认目录 `rtld_search_dirs`）**：如果还是没有，最后进入系统默认目录 `rtld_search_dirs`（比如 `/lib64`、`/usr/lib` 等）检查有没有这个文件。如果还没有就报错退出；如果找到了，就拿到有效的文件描述符 `fd`，调用 **`_dl_map_object_from_fd`** 载入该共享库对象！

```text
+-----------------------------------------------------------------------------+
|                _dl_map_object 查找共享库 (.so) 的六级优先级                 |
+-----------------------------------------------------------------------------+
|  [输入目标库名: 如 "mylib.so"]                                              |
|        |                                                                    |
|        v                                                                    |
|  (1) 遍历 _dl_ns[LM_ID_BASE]._ns_loaded 已加载链表 (比对 l_name/l_libname)  |
|        |-- 已存在 --> 直接返回该 link_map (无需再找)                        |
|        v 未载入                                                             |
|  (2) 查 DT_RPATH (l_rpath_dirs): 自身 -> 父共享库链逐级向上 -> main_map     |
|        |-- 找到文件 -> 返回 fd                                              |
|        v 未找到                                                             |
|  (3) 查环境变量 LD_LIBRARY_PATH (env_path_list)                             |
|        |-- 找到文件 -> 返回 fd                                              |
|        v 未找到                                                             |
|  (4) 查 DT_RUNPATH (l_runpath_dirs)                                         |
|        |-- 找到文件 -> 返回 fd                                              |
|        v 未找到                                                             |
|  (5) 查缓存文件 /etc/ld.so.cache (_dl_load_cache_lookup)                    |
|        |-- 找到文件 -> 返回 fd                                              |
|        v 未找到                                                             |
|  (6) 查系统默认目录 rtld_search_dirs (如 /lib64, /usr/lib)                  |
|        |-- 找到文件 -> 返回 fd ===> 调用 _dl_map_object_from_fd(fd) 映射!   |
|        +-- 仍未找到 -> 报错退出 (cannot open shared object file)            |
+-----------------------------------------------------------------------------+
```

   * **`_dl_map_object_from_fd`（防重校验与映射入链）**：
     该函数负责根据打开的 `fd` 将共享库文件内容映射到内存中（同样只是建立 VMA 映射，做好数据管理，物理页读入等缺页中断）：
     1. **文件号与设备号防重检查**：首先调用 `sys_fstat` 函数得到该 `fd` 的**文件号（inode）和设备号（dev）**，然后再次遍历全局 `link_map` 链表！为什么要再查一次呢？因为同一个 `.so` 文件可能通过软链接或不同路径名取了不同的名字，前面第 1 级按名字没拦住，这里通过比对底层物理文件的设备号和文件号，一旦发现有匹配的，说明该文件其实已经载入过了，直接返回已有的 `link_map` 即可！
     2. **分配并映射 `link_map`**：如果确认没载入过，就新建该共享库的 `link_map`，用 `open_verify` 验证文件头，计算各个 `LOAD` 段的装载地址范围并加上随机偏移量 `bias` 完成内存映射。
     3. **解析 `.dynamic` 与 Hash 表并入链**：之后调用 `elf_get_dynamic_info` 把该 `.so` 的 `.dynamic` 内容存入它的 `l_info`，调用 `_dl_setup_hash` 产生它的符号查找 Hash 表，最后调用 `_dl_add_to_namespace_list` 将这个新 `link_map` 加入到 `_ns_loaded` 全局链表当中！

9. **载入 `/etc/ld.so.preload` 文件中的共享库**：
   处理完环境变量 `LD_PRELOAD` 后，第 9 步会尝试打开系统级的 `/etc/ld.so.preload` 文件，用 `__mmap` 将该配置文件载入内存，然后对里面配置的每一个 preload 文件名同样调用 `do_preload` 依次载入。
   至此，我们的 `_ns_loaded` 全局链表中依次排布的就是：`main_map` -> `_dl_rtld_map` -> 其他各个 preload 文件的 `link_map`。

### 第 10 步：解析所有依赖关系（`_dl_map_objects_dep`）与拓扑重排破环算法
前面第 8、9 步只载入了预加载库，主程序和预加载库自身 `.dynamic` 里声明的那些 `DT_NEEDED` 依赖库（比如 `mylib.so` 和 `libc.so.6`）还没载入呢！这就是第 10 步要干的大事：

1. **广度优先载入所有 `DT_NEEDED` 与 `FILTER` 依赖库**：
   我们首先构造一个 `preloads` 数组，将主程序 `main_map` 和刚才所有的 `preload_map` 载入这个数组，然后调用 **`_dl_map_objects_dep`** 来处理所有文件的依赖关系：
   * `_dl_map_objects_dep` 将数组构造成一个待处理队列链表，开始從头遍历每一个 `map`，查看它的 `.dynamic` 字段：
   * 如果是 **`DT_NEEDED`** 字段（普通依赖库），就把这个文件用 `_dl_catch_error` 调用 `_dl_openaux`（内部就是运行 `_dl_map_object`）打开并加载得到新的 `link_map`，然后将它**追加保存到 `preload` 待处理链表和 `_ns_loaded` 全局链表的末尾**（这样后续遍历到链表尾部时，又会继续把新库依赖的子依赖库也加载进来！）；
   * 如果是 **`FILTER`** 字段（过滤器共享库），则将 `_dl_openaux` 得到的新 `link_map` **插入到当前正在处理的表项之前**，然后优先处理新插入的 `link_map`。

2. **依赖库拓扑重排与破环算法（为什么需要排序？怎么排序？）**：
   当所有层级的依赖库都加载进链表后，我们还面临一个极其重要的问题：**待会儿在进入 `main` 之前，我们要依次执行每个共享库的 `.init` / `.init_array` 初始化函数。如果库 `A` 依赖库 `B`（即 `A` 的初始化代码里可能会调用 `B` 的函数），那我们必须先初始化被依赖的 `B`，再初始化 `A`！**
   也就是说，我们需要根据依赖顺序对链表里的所有依赖库做一次**拓扑排序**，使得**所有的“被依赖项”都排在“依赖项”的后面**（这样从后往前逆序调用 `.init` 时，被依赖项就会先完成初始化！）。如果遇到 `C -> F -> E -> C` 这种**循环依赖（环）**，算法还得能自动打破环路，不能死循环！

   我们来看 Glibc 设计的这个非常精妙的重排与破环算法：
   * **核心规则**：从前往后扫描序列，如果发现**排在后面的某个库 `X` 竟然被排在它前面的某个库 `Y` 所依赖**，就把前面那个被依赖的库抽出来，**插到后面那个依赖它的库的后面去**！这样只要扫描调整的次数够多，所有被依赖项最终都会被推到依赖项的后面。
   * **如何破环？** 如果存在循环依赖的环（比如 `C -> F -> E -> C`），环里的节点就会一直被互相往后挪、反复出现移位。算法为每个节点维护一个移位计数（`[count]`），**一旦某个节点的移位次数达到 `2`（或准备超过 2）时，说明它是环的一员，此时直接将它后面节点的计数清 `0` 不再挪动它**，这样就相当于强行切断了环的一条边（打破了环），让后面的排序可以继续正常收敛！

   我们用一个包含 7 个库 `A B C D E F G`（且刻意包含 `C -> F -> E -> C` 环）的复杂例子，完整推演一遍该算法的 14 步重排过程：
   * **假设依赖关系如下**：
     `A -> B, C, D`  |  `B -> E`  |  `C -> F`  |  `D -> (无)`  |  `E -> C`  |  `F -> E`  |  `G -> F`
   * **初始序列（方括号内为移位次数）**：
     `A[0]  B[0]  C[0]  D[0]  E[0]  F[0]  G[0]`
   * **逐步重排推演表**：
     ```text
     步骤          当前序列状态 (节点[移位次数])                  本步触发的调整原因
     -------------------------------------------------------------------------------------------------
     i=2, k=1 :  A[1]  B[0]  C[0]  D[0]  E[0]  F[0]  G[0]   (A 依赖后面的 B,C,D，A 原地标记 [1])
     i=3, k=2 :  A[1]  B[1]  C[0]  D[0]  E[0]  F[0]  G[0]   (B 依赖后面的 E，B 标记 [1])
     i=3, k=5 :  A[1]  B[1]  D[0]  E[0]  C[1]  F[0]  G[0]   (后面的 E 依赖前面的 C -> 把 C 移到 E 后面，C[1])
     i=4, k=3 :  A[1]  B[1]  D[1]  E[0]  C[0]  F[0]  G[0]   (考察 D，D 无后向被依赖)
     i=4, k=6 :  A[1]  B[1]  D[1]  C[0]  F[0]  E[1]  G[0]   (后面的 F 依赖前面的 E -> 把 E 移到 F 后面，E[1])
     i=4, k=6 :  A[1]  B[1]  D[1]  F[0]  E[1]  C[1]  G[0]   (移动后的 E 又依赖前面的 C -> 把 C 移到 E 后面，C[1])
     i=4, k=7 :  A[1]  B[1]  D[1]  E[1]  C[1]  G[0]  F[1]   (后面的 C 和 G 都依赖前面的 F -> 把 F 移到最后，F[1])
     i=4, k=7 :  A[1]  B[1]  D[1]  C[1]  G[0]  F[1]  E[2]   (末尾的 F 依赖前面的 E -> 把 E 移到 F 后面，E 次数变为 [2]!)
     i=4, k=7 :  A[1]  B[1]  D[1]  G[0]  F[1]  E[2]  C[2]   (末尾的 E 依赖前面的 C -> 把 C 移到 E 后面，C 次数变为 [2]!)
     i=4, k=7 :  A[1]  B[1]  D[1]  F[1]  E[2]  C[2]  G[1]   (整理 G 的位置，移后 G[1])
     i=5, k=7 :  A[1]  B[1]  D[1]  E[2]  C[0]  G[0]  F[0]   (★破环! E 次数已达 [2]，发现环 C-F-E，将 E 之后的 C,G,F 计数清 0!)
     i=6, k=5 :  A[1]  B[1]  D[1]  E[2]  C[1]  G[0]  F[0]   (破环后继续正常推进 i=6: 确认 C[1])
     i=7, k=6 :  A[1]  B[1]  D[1]  E[2]  C[1]  G[1]  F[0]   (推进 i=7: G 依赖后面的 F，位置已满足，G[1])
     最终结果 :  A[1]  B[1]  D[1]  E[2]  C[1]  G[1]  F[1]   (排序完成! 除被打破的环边外，所有被依赖项均在依赖项之后)
     ```

### 第 11 ~ 12 步：版本校验与全局重定位（`_dl_relocate_object`）
11. **载入并检查版本对应的 `ld.so` 与共享库符号版本**（基于 `.gnu.version` 和 `.gnu.version_r`）。
12. **全局重定位（`_dl_relocate_object`）**：
    万事俱备！调用 **`_dl_relocate_object`** 遍历完所有已加载共享库和主程序的 `link_map`，然后像一开始 `_dl_start` 重定位 interpreter 自身那样，根据每个模块的 `.rela.dyn`（以及开启了 `BIND_NOW` 时的 `.rela.plt`）搭配我们在第十篇讲的 `.gnu.hash` 查找算法，重定位所有的共享库和主程序！那些 `.got` 表项（包括 `GOT[1]` 的 `link_map`、`GOT[2]` 的 `_dl_runtime_resolve` 以及各个外部函数和变量的地址）在这个阶段也全部被填好了！

我们把从 `ld.so` 的 `_start` 到 `dl_main` 执行完毕的整个前半段动态链接过程，汇总为一张完整的字符流程图（即**【Glibc 过程 1】**）：

```text
+-----------------------------------------------------------------------------------+
|                 动态链接器启动之【Glibc 过程 1】(_start -> dl_main)               |
+-----------------------------------------------------------------------------------+

  [interpreter (ld.so) 入口: _start]
    |-- 以栈顶指针 %rsp 作为参数调用 _dl_start
    v
  [_dl_start(rsp)]
    |-- 1. 构造 bootstrap 结构初始化动态链接器数据
    |-- 2. 自举重定位: 将动态链接器自身的 .rela.dyn / .rela.plt 等地址全部加上 bias 偏移
    |-- 3. 调用 _dl_start_final(arg)
    v
  [_dl_start_final(arg)]
    |-- 设置动态链接器自身的 _dl_rtld_map 变量与 hash_table
    |-- 调用 _dl_sysdep_start(arg, dl_main)
    v
  [_dl_sysdep_start(arg, dl_main)]
    |-- 1. DL_FIND_ARG_COMPONENTS(arg) 读出栈顶数据写入:
    |        _dl_argc, _dl_argv, _environ, _dl_auxv
    |-- 2. 遍历 auxv 序列，解析参数填入变量 (最核心: 提取 AT_ENTRY 保存为 user_entry)
    |-- 3. 调用核心函数 dl_main() (见下方 12 步展开)
    +-- 4. dl_main 返回后，直接将堆栈修改回 _dl_start_user 继续下一步
              |
              v
        [dl_main() 十二步核心流程]
          |-- (1) 初始化 GL 系列全局变量
          |-- (2) process_envvar() -> _dl_next_ld_env_entry() 解析保存 LD_PRELOAD, LD_LIBRARY_PATH 等
          |-- (3) _dl_new_object() 生成 C 主程序的 main_map，加入全局链表 (_ns_loaded)
          |-- (4) 遍历主程序 Program Headers:
          |         由 PT_PHDR 内存位置与记录位置差值算出偏移 bias 存入 main_map->l_addr，
          |         计算 DYNAMIC(l_ld)、INTERP、LOAD(l_map_start/end)、TLS、STACK、RELRO 等
          |-- (5) elf_get_dynamic_info() 与 _dl_setup_hash() 解析主程序 .dynamic 与 hash 表
          |-- (6) _dl_init_paths() 初始化路径:
          |         DT_RPATH -> l_rpath_dirs | DT_RUNPATH -> l_runpath_dirs | LD_LIBRARY_PATH -> env_path_list
          |-- (7) 设置 _dl_rtld_map 的 l_name/l_phdr/l_relro_* 等，与 main_map 所在链表汇合
          |-- (8) 切片 preloadlist (LD_PRELOAD)，调用 do_preload() 预加载:
          |         do_preload -> _dl_catch_error -> map_doit -> _dl_map_object (6级搜库路径:
          |         _ns_loaded -> DT_RPATH链 -> LD_LIBRARY_PATH -> DT_RUNPATH -> ld.so.cache -> 默认目录)
          |         -> _dl_map_object_from_fd (sys_fstat 校验文件号/设备号防重, 映射段, 解析dynamic/hash, 入链)
          |-- (9) 打开并 __mmap 载入 /etc/ld.so.preload，依次调用 do_preload() 载入
          |-- (10) _dl_map_objects_dep() 解析所有依赖库并做拓扑排序:
          |         +-- 遍历 dynamic 字段: DT_NEEDED 调用 _dl_openaux 追加到末尾; FILTER 插到当前项之前
          |         +-- 依赖顺序拓扑重排与破环算法 (被依赖项移到后面，移位次数 > 2 时清零后面计数破环)
          |-- (11) 载入并校验版本对应的 ld.so 与符号版本
          +-- (12) _dl_relocate_object() 遍历所有 link_map 完成全部共享库与主程序的重定位，填好所有 GOT 表!
```

---

## 4. 控制权移交、初始化与程序退出：从 `_dl_start_user` 到 `_dl_fini`

当 `dl_main` 的第 12 步 `_dl_relocate_object` 执行完毕并返回后，所有共享库都已经躺在内存里，所有的 `.got` 表都已经填上了正确的地址。接下来就是执行各个共享库的初始化函数，并正式把 CPU 控制权移交给我们写的 C 程序了！

### 4.1 `_dl_start_user` 与 `_dl_init`（共享库初始化）
1. **`_dl_start_user` 汇编过程**：
   * 该函数首先检查启动的是不是链接器自身，如果是的话读出 `arg` 中的 `skip` 参数，如果环境变量中从第 `k` 个开始的 `n` 个需要被忽略，就移动 `envp` 数组的值来略过这几个变量，消除这些无用环境变量的效果。
   * 然后将参数 **`main_map`、`argc`、`argv`、`envp`** 四个变量放入传参寄存器，调用 **`_dl_init`** 函数（其实就是用汇编写了 `_dl_init(main_map, argc, argv, envp)`）。
   * 等 `_dl_init` 运行结束返回之后，恢复 `r13` 寄存器，**将动态链接器的退出清理函数 `_dl_fini` 的地址存入 `%rdx` 寄存器**（传给主程序的 `_start`），然后**正式跳入 `user_entry`（即主程序 C 代码的 `_start` 过程）**！
2. **`_dl_init` 与 `_call_init`**：
   * `_dl_init` 首先看有没有 `_dl_initfirst`，有的话优先调用 `_call_init` 初始化这个 `link_map`。
   * 接着检查主程序的 `.dynamic` 有没有 **`.preinit_array`（`DT_PREINIT_ARRAY`）** 段，如果有的话，这里定义了在所有共享库初始化之前就要最先运行的 C 初始化代码：从 `.preinit_array` 载入内存后，里面每个表项就是一个 `init` 函数指针，计算动态链接之后的地址并依次调用即可。
   * 之后，按照前面第 10 步拓扑排序排好的 **`l_initfini` 数组**中的顺序（从后往前，即从最底层的被依赖库开始，最后到主程序 `main_map`），对每一个共享依赖库的 `link_map` 依次调用 **`_call_init`** 进行初始化！
   * **`_call_init` 的具体操作**：
     1. 对于传入的 `link_map`，首先检查它的 **`l_init_called`** 标志位是否已经被置位。如果已经置位了，说明这个库之前已经被 `_call_init` 初始化过了，直接返回即可（防止重复初始化）；不然的话置位该标志并继续。
     2. 检查该模块 `.dynamic` 中间的 **`DT_INIT`（即 `.init` 段）**，如果存在，加上基址偏移计算出对应的真实地址后，运行 `_init` 函数做第一次初始化（在我们的简单 C 程序中，`.init` 做的操作就是检查 `__gmon_start__` 函数是否被载入，如果地址不为空就调用 `__gmon_start__`）。
     3. 然后检查该模块 `.dynamic` 的 **`DT_INIT_ARRAY` 和 `DT_INIT_ARRAYSZ`（即 `.init_array` 段）** 参数，遍历运行数组中的每一个 `init` 函数指针做之后的初始化，然后返回（在我们的简单 C 程序中，`.init_array` 保存在 `__frame_dummy_init_array_entry`，执行时跳入 `frame_dummy` 函数 -> 进入 `register_tm_clones` 过程 -> 检查 `_ITM_registerTMCloneTable` 地址是否不为空，是的话跳入运行，为 `NULL` 则略过）。

### 4.2 主程序 `_start` -> `__libc_start_main` -> `main` -> `exit` -> `_dl_fini`
当 `_dl_start_user` 跳入主程序的 `_start` 后，就和我们在第七篇第三节讲的 C 运行时流程完全接轨了，而且现在有了动态链接的视角，最后几个细节也彻底清晰了：

1. **`_start` 与 `__libc_start_main`**：
   * 主程序的 `_start` 把刚才 `_dl_start_user` 放在 `%rdx` 寄存器里的 **`_dl_fini` 函数地址**，连同 `main` 函数地址、`argc`、`argv` 等参数一起传给 **`__libc_start_main`**。
   * 在现代 Glibc 的 `__libc_start_main` 中，首先调用 **`__cxa_atexit` 注册了传入的 `_dl_fini` 函数**（这样等程序退出时就会自动调用 `_dl_fini` 来卸载所有共享库！），随后执行初始化检查，调用 `_dl_audit_preinit`，最后通过 **`__libc_start_call_main`** 正式调用我们的 **`main(argc, argv, envp)`** 函数！
2. **动态链接下的退出流程（`exit` -> `__run_exit_handlers` -> `_dl_fini`）**：
   * `main` 函数运行完毕返回后，调用 **`exit`** 函数退出，该函数运行 **`__run_exit_handlers`**，依次执行之前通过 `atexit` / `__cxa_atexit` 注册的退出处理链表 `list`，其中最核心的就是运行我们刚才注册的 **`_dl_fini`** 函数！
   * 在 **`_dl_fini`** 中，因为之前载入的每一个文件（主程序和所有共享库）在退出时都需要被妥善关闭和清理，所以它会遍历 `_dl_ns` 的每一个命名空间 entry 的 **`_ns_loaded` 数组**：
     * 对于每个 `link_map`，如果它的 `.dynamic` 存在 **`DT_FINI_ARRAY`（即 `.fini_array` 段）**，就按逆序遍历数组中的每一个函数指针并运行；
     * 如果之后还存在 **`DT_FINI`（即 `.fini` 段）**，就运行该模块的 `_fini` 函数。
   * 在我们的本 C 程序中：
     * 根据 `.fini_array` 会进入 **`__do_global_dtors_aux`** 函数；
     * 它先运行 **`__cxa_finalize`** 函数做一些关闭析构操作——注意，**`__cxa_finalize` 是一个 `libc.so.6` 里的函数调用，所以此时需要用我们在第十篇讲的动态链接方法，走 `.plt.got` -> `.got`（`0x3ff8`）调用过去！**
     * 然后运行 **`deregister_tm_clones`** 函数来做之前 `register_tm_clones` 的反操作（检查 `_ITM_deregisterTMCloneTable` 是否存在，存在的话运行其中的函数）。
     * 最后运行 **`_dl_audit_objectclose`** 关闭每一个共享库 object，大功告成！

我们把从 `_dl_start_user` 到 `main` 运行再到 `exit` / `_dl_fini` 退出的整个后半段流程，绘制成最后一张完整的字符流程图（即**【Glibc 过程 2 + C 过程 + 退出过程】**）：

```text
+-----------------------------------------------------------------------------------+
|            动态链接器与程序运行之【Glibc 过程 2 + C 过程 + 退出过程】             |
+-----------------------------------------------------------------------------------+

  [_dl_start_user (dl_main 返回后的汇合点)]
    |-- 1. 检查是否直接启动链接器，若是则读出 skip 参数移动 envp 略过需忽略的环境变量
    |-- 2. 将 (main_map, argc, argv, envp) 放入寄存器，调用 _dl_init()
    |-- 3. 恢复 r13，将退出函数 _dl_fini 的地址存入 %rdx 寄存器
    +-- 4. 正式跳入 user_entry (主程序 C 代码的 _start 入口)!
              |
              +---> [_dl_init(main_map, argc, argv, envp)]
                      |-- 1. 若有 _dl_initfirst，优先调用 _call_init 初始化它
                      |-- 2. 检查 .dynamic 有无 .preinit_array 段，有则依次调用其中的 init 函数
                      +-- 3. 遍历拓扑排序好的 l_initfini 数组，对每个依赖库调用 _call_init():
                              +-- 检查 l_init_called 防止重复初始化
                              +-- 若存在 DT_INIT (.init 段): 运行 _init (检查并调用 __gmon_start__)
                              +-- 若存在 DT_INIT_ARRAY (.init_array 段): 遍历运行每个 init 函数
                                  (__frame_dummy_init_array_entry -> frame_dummy -> register_tm_clones
                                   -> 检查 _ITM_registerTMCloneTable)
    |
    v  (控制权移交至主程序 _start)
  [主程序 _start]
    |-- 设置传参寄存器 (含 %rdx 中的 _dl_fini)，调用 __libc_start_main (若返回则执行 hlt)
    v
  [__libc_start_main]
    |-- 1. 调用 __cxa_atexit 注册动态链接器退出清理函数 _dl_fini
    |-- 2. 执行初始化检查 (_init / init_array_entry) 与 _dl_audit_preinit
    |-- 3. 调用 __libc_start_call_main()
    v
  [__libc_start_call_main]
    |
    +===> ★ 调用用户编写的 main(argc, argv, envp) 执行核心业务逻辑! ★
    |        (期间调用 printf / strlen 等外部函数走 .plt.sec -> .got / _dl_runtime_resolve)
    v
  [main 返回 -> 调用 exit() 退出]
    |
    v
  [__run_exit_handlers]
    |-- 依次运行 atexit / __cxa_atexit 注册的 list 链表函数 ===> 触发执行 _dl_fini()!
    v
  [_dl_fini()]
    |-- 遍历 _dl_ns 每个 entry 的 _ns_loaded 数组，依次关闭和清理每一个载入的 ELF 文件:
    |     |-- 1. 若 link_map 存在 DT_FINI_ARRAY (.fini_array 段)，遍历运行每个 fini 函数:
    |     |        +--> 进入 __do_global_dtors_aux():
    |     |               |-- 调用 __cxa_finalize() (libc 函数，走 .plt.got -> .got[0x3ff8] 动态调用)
    |     |               +-- 调用 deregister_tm_clones() (检查 _ITM_deregisterTMCloneTable)
    |     |-- 2. 若 link_map 存在 DT_FINI (.fini 段)，运行 C 代码的 _fini
    |     +-- 3. 运行 _dl_audit_objectclose 关闭 object
    v
  [系统调用 exit_group 陷入内核，进程彻底消亡]
```

---

## 全系列总结

从第一篇《C 的模型和顺序语句对应的 asm 模型》里最简单的 `a = b + c` 寄存器与内存交互，到自己手写 RISC-V 汇编与 `link.ld` 链接脚本、认识 ELF 的 Section 与 Segment、探索裸机 ROM/DDR 的 BIN/HEX 加载与函数调用栈帧；再到后五篇走入真实的 Linux 内核 `execve` / `load_elf_binary`、看懂 `.eh_frame` 堆栈回溯与 `.debug_*` DWARF 调试语法树、剖析 PIE 位置无关代码、GOT/PLT 延迟绑定、`.gnu.hash` 布隆过滤器查找，直到今天单步走完动态链接器 `ld.so` 与 Glibc 从自举、拓扑排序到退出的每一行关键逻辑。

至此，一个 C 程序从源码文本变成二进制 ELF 文件躺进硬盘、再被操作系统和动态链接器加载进内存运行直至消亡的完整闭环，就全部呈现在我们眼前了！