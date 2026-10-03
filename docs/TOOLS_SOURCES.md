# 工具来源

`tools/` 下的第三方软件从哪来、怎么装。

> **为什么有这份文档**：原本这些信息记在根目录几个一次性下载脚本里
> （`download_looo.py` / `download_apps.py`）。那些脚本已完成使命、且硬编码的
> 路径（`dist_build\OpenClass`）早已不存在，留着只会误导 —— 所以删掉了脚本，
> 把其中**不可再生的知识**（镜像地址、绕行方案、解包方式）搬到这里。

## 办公套件（体积大、最容易下不动的一类）

### 为什么不用 PortableApps

`download_apps.py` 当年用的是 PortableApps 的 `.paf.exe` 发行包，
但 `test_reach.py` 的探测结论是：**downloads.portableapps.com 在本网络下不可达**。
所以改用下面的官方安装包 + 7-Zip 解包。

### LibreOffice

```
https://mirrors.aliyun.com/libreoffice/stable/24.8.3/win/x86_64/LibreOffice_24.8.3_Win_x86-64.msi
```

阿里云镜像。官方源（`download.documentfoundation.org`）在部分校园网下很慢，
镜像更稳。

### OpenOffice

```
https://downloads.sourceforge.net/project/openofficeorg.mirror/4.1.15/binaries/Apache_OpenOffice_4.1.15_Win_x86_install_en-US.exe
```

SourceForge 上的 `openofficeorg.mirror` 镜像。

### 解包方式

两个都是 **MSI / EXE 安装包**，用 7-Zip 直接解包，**不执行安装程序**：

```
7z.exe x <安装包> -o<目标目录> -y
```

这很重要：跑安装程序会写注册表、可能弹 UAC、装到 Program Files 去 ——
而这个软件是要**随包分发、装在 `tools/` 下**的，不该动系统状态。

解包后有效性判据（沿用原脚本的检查方式）：目录下能找到 `soffice.exe`。

```
tools/libreoffice/**/soffice.exe
tools/openoffice/**/soffice.exe
```

> 注意：`tools/libreoffice` 目前是空的（0 MB），实际可用的是 `tools/openoffice`。
> 应用的文件路由（`backend/core/app_locator.py`）已改成"按本机实际可用性挑"，
> 所以只装一个也能正常打开文档 —— 这正是当初修掉"点了没反应"的那个 bug。

## 已在 tools/ 中的其他组件

`tools/` 下还有 7zip、vlc、mpv、everything、password_generator、random-picker、
file_hash。**这些当初的下载地址没有被记录下来**（脚本里没有留档），
所以这份文档无法补全。如果需要重新下载，得回到各自官网获取。

## 下载时的两个注意点

1. **用 `curl -L --retry 3`**，不要用 PowerShell 的 `Invoke-WebRequest` ——
   大文件下载中断后没有断点续传，且 `-UseBasicParsing` 在旧版 PowerShell 上
   对某些重定向会失败。
2. **下载到临时文件再解包**，解包成功后删掉安装包 —— 几百 MB 的安装包留在
   `dist_build/` 里会跟着进安装包体积，白白让 A/B 包大一大截。
