# 配置 ubuntu 24.04 环境

## 杂七杂八

* git
* vim
* htop


## 安装通讯软件

### 安装微信

```
wget -c -O atzlinux-v12-archive-keyring_lastest_all.deb https://www.atzlinux.com/atzlinux/pool/main/a/atzlinux-archive-keyring/atzlinux-v12-archive-keyring_lastest_all.deb

sudo apt -y install ./atzlinux-v12-archive-keyring_lastest_all.deb

sudo apt -y install wechat
```

功能和 windows 的微信高度一致，甚至可以看朋友圈。但是界面的字体、美术风格、布局等比较生硬，没那么好看。

进入“设置->通用”，将字体修改到“大”

### 安装QQ

* 进入 [QQ 官网](https://im.qq.com/index/)下载安装包，选择 下载->Linux->x86->.deb。下载得到`QQ_3.2.16_250307_amd64_01.deb`。
* 之后执行`sudo dpkg -i QQ_3.2.16_250307_amd64_01.deb`进行安装。

图形界面和 windows 桌面版不太一样，需要重新适应一下。

### 安装钉钉

* 进入 [钉钉 官网](https://www.dingtalk.com/download#/)下载安装包，选择 Linux 当中的 AMD 版本。下载得到`com.alibabainc.dingtalk_7.6.25.4122001_amd64.deb`。
* 之后执行`sudo dpkg -i com.alibabainc.dingtalk_7.6.25.4122001_amd64.deb`进行安装。

和 windows 的钉钉界面排布差不多。

## 安装 clash 梯子

* 进入[魔戒梯子官网](https://mojie.app/)，选择 “下载客户端” 中的 ubuntu，然后下载 Clash.Verge__amd64.deb
* 执行`sudo dpkg -i Clash.Verge__amd64.deb`安装 clash
* 根据网站教程执行后续操作
* 选择“订阅”，将网站的订单号复制到边框，然后导入 config.yaml
* 选择“设置”，将“系统代理”打开，将“ipv6”关闭，即可使用

## 安装 google 浏览器

### 安装

谷歌浏览器有 chromium 和 chrome，后者是我们常用的 google，前者是 chrome 的一种替代实现。请注意 chromium 不能登陆 google 账号。

对于 chromium：

* `sudo apt update`
* `sudo apt install chromium-browser`

对于 chrome：

* `wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb`
* `sudo dpkg -i google-chrome-stable_current_amd64.deb`

之后就可以在 ubuntu 软件栏中看到 google 的快捷键图标。之后右键图标，点击“固定到快捷栏”，就可以将打开快捷方式显示在左侧快捷栏。

![固定到快捷栏](./img/fix_quick.png)
![固定到快捷栏](./img/to_quick.png)

### 简单配置

* 点击右上角进入设置，然后选择“默认浏览器”，将 google 设置为默认浏览器；
<!-- ![默认浏览器](./img/default_browser.png)
* 点击“外观”，本人不喜欢看小字，所以将字号设置为“大”，将页面大小设置为“125%” -->
![外观设置](./img/outlook_set.png)
* 点击“起始页面”，本人喜欢用必应浏览器（国内不需要装梯子），选择“打开特定页面或一组页面”，新增页面“cn.bing.com”
![起始页面设置](./img/default_open.png)

### 登陆账号

登陆 google 浏览器账号，这一部需要挂梯子

## 安装 vscode 开发环境

### 安装

* 访问[vscode官网](https://code.visualstudio.com)
* 点击下载 .deb 文件，会被下载到`~/下载`路径中
* 进入对应路径执行`sudo dpkg -i code_1.75.0-1675266613_amd64.deb`即可安装
* 将 vscode 固定到快捷边栏

### 简单配置

感觉字体太小了，可以将 File->preference->settings->windows 的 zoom level 修改到合适的结果。数字 1 表示放大 20%，-1 表示缩小 20%，以此类推。

### 基本插件

* Python:
    * Python
    * Python Debugger
    * Pylance
* C/C++:
    * C/C++
    * C/C++ themes
* Verilog:
    * Verilog-HDL/SystemVerilog/Bluespec SystemVerilog

## 配置 ssh 连接

### 生成 ssh 密钥

执行`ssh-keygen -t rsa`生成 rsa 公私密钥对，复制`~/.ssh/id_rsa.pub`的公钥值

### 设置 github

设置 github 的 ssh 公私钥连接

* 登陆 github
* 选择 settings->SSH and GPG Keys->New SSH key
* 填入 key 名称，rsa.pub 内容，创建密钥即可
* 尝试用 git@github:xxx.git 的方式进行 clone，检查是否私钥连接成功

前几次 clone 如果失败，也许只是设备没有同步到位，可以重启 sshd 之后再尝试 clone

### 设置远程连接

把原来 pc 设备的 ssh 配置拷贝过来，作必要的整理。用原来的设备，将本机器的 id_rsa.pub 依次加入到需要远程连接的机器的 .ssh/authorized_keys 中。之后就可以 ssh 远程连接这些设备了。

第一次连接的时候会因为设备不认识而提示，输入 yes 即可。如果一开始显示密钥验证不通过，多试几次或者重启 sshd 即可。

### vscode 远程连接

安装插件：

* Remote Explorer
* Remote-SSH: Editing Configuration FIles
* Remote-SSH

之后就可以用 vscode ssh 连接、编辑、执行远程设备的文件了 




