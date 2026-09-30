# 代码签名与供应链验证

本文说明 OpenClass-Box 的签名方案、用户如何验证下载到的安装包，以及申请
**SignPath Foundation** 免费代码签名的准备情况与接入步骤。

---

## 一、为什么需要签名

Windows 对没有有效 Authenticode 签名的程序会做两件事：

1. **SmartScreen 拦一刀**：「Windows 已保护你的电脑 / 未知发布者」，需要用户点「仍要运行」；
2. 部分安全软件按「无签名 + 下载量少」直接报毒。

对一个要装进教室一体机的工具箱来说，这两件事都会让电教委员在老师面前很尴尬。
所以签名不是"锦上添花"，而是分发环节的刚需。

### 当前状态

`pack.py` 在打包时会生成一份**自签名证书**（`OpenClass-Box.cer`）并随包分发。
它只解决"文件来源可追溯"这一层，**不能消除 SmartScreen 警告** —— 自签名证书
不在微软信任链里。

### 目标方案（两条腿）

| 方案 | 作用 | 状态 |
|---|---|---|
| **sigstore 无密钥签名** | 供应链证明：证明「这个文件由本仓库的 CI 产出且未被篡改」，签名记入 Rekor 透明日志，任何人可验证 | **已接入**（`.github/workflows/sign-release.yml`） |
| **SignPath Foundation 代码签名** | Windows Authenticode 签名：消除 SmartScreen「未知发布者」 | **准备申请**（见第四节） |

两者互补：sigstore 解决"代码从哪来、有没有被改"，SignPath 解决"Windows 信不信它"。

---

## 二、sigstore 签名（已接入）

### 自动流程

每次在 GitHub 上发布 Release（或手动触发 workflow）时，`Sign Release Artifacts` 会：

1. 下载该 Release 里的安装包；
2. 生成 `SHA256SUMS.txt`（校验值清单）；
3. 用 **sigstore keyless** 方式签名 —— 不需要自备证书，签名时用 GitHub 的
   OIDC 身份换取短期证书，签名与证书一起写入 Rekor 透明日志；
4. 通过 sigstore 自带的验证器**当场回验一次**；
5. 把 `*.sigstore.json`、`SHA256SUMS.txt`、`VERIFY.md` 回传到该 Release。

### 用户怎么验证

```bash
# 1) 校验文件完整性（可选，但很便宜）
sha256sum -c SHA256SUMS.txt

# 2) 验证签名来源
pip install sigstore
sigstore verify identity \
  --cert-identity 'https://github.com/HMUG12/OpenClass-Box/.github/workflows/sign-release.yml@refs/heads/main' \
  --cert-oidc-issuer 'https://token.actions.githubusercontent.com' \
  --bundle OpenClass-Box-B_Setup.exe.sigstore.json \
  OpenClass-Box-B_Setup.exe
```

验证通过即表示：该文件确实由本仓库的 GitHub Actions 流程签名，且签名已记入
公开透明日志（可审计、不可悄悄替换）。

---

## 三、仓库内的其他签名相关文件

| 文件 | 说明 |
|---|---|
| `version_info.txt` | exe 的版本资源（发布者 / 产品名 / 版本 / 版权），有完整版本信息的程序被启发式误报的概率更低 |
| `OpenClass-Box.cer` | 打包时生成的自签名证书，随包提供，便于用户核对来源 |
| `.github/workflows/sign-release.yml` | sigstore 签名与验证 |

---

## 四、SignPath Foundation 申请准备

**SignPath Foundation** 为符合条件的开源项目提供**免费**的代码签名服务
（私钥保存在云端 HSM，签名通过 SignPath.io 完成）。通过审核后，签名可以
集成进 CI，做到"每次发版自动签"。

> 下面的流程依据其公开说明整理，具体条款以 SignPath 官方要求为准；
> 提交前请再核对一遍当前政策。

### 4.1 项目资质（我们这边的情况）

| 要求 | 我们的情况 |
|---|---|
| 使用 OSI 认可的开源许可证 | ✅ MIT（见 `LICENSE`），项目完全开源 |
| 公开可访问的代码仓库 | ✅ https://github.com/HMUG12/OpenClass-Box |
| 活跃维护 | ✅ 持续发布（见 Releases 与提交历史） |
| 非商业用途 | ✅ 面向学校与电教委员免费使用 |
| 安装程序附带许可协议 | ✅ `license.txt` 在安装向导中展示，需勾选同意 |
| 用户数据与隐私说明 | ✅ 数据全部本地保存，不上传（`data/` 目录，含说明） |

### 4.2 审核方通常会关注的信息

申请时建议一并提供（本项目已具备）：

1. **项目简介与用途** —— 课堂教学一体机的开源运维工具箱；
2. **构建方式** —— `pack.py`（PyInstaller）+ `OpenClass.iss`（Inno Setup 6），
   全部脚本在仓库内，可复现；
3. **第三方组件清单与许可证** —— 见 4.3；
4. **签名对象** —— 两个安装包（`OpenClass-Box-A_Setup.exe` / `OpenClass-Box-B_Setup.exe`）
   以及其中的主程序 `OpenClass-Box.exe`；
5. **发布流程** —— 版本 tag（如 `v0.1.5-beta`）→ Release → 附件上传安装包 → sigstore 签名。

### 4.3 第三方组件（随包分发，各自保留原许可证）

| 组件 | 用途 | 许可证 |
|---|---|---|
| Apache OpenOffice | 文档编辑（Office 组件） | Apache License 2.0 |
| VLC | 音视频播放 | GPLv2+ |
| mpv | 动态壁纸渲染 | GPLv2+ |
| Python / PyInstaller / pywebview | 运行时与打包 | PSF / GPL 例外条款 / BSD |
| WebView2 Runtime | 界面渲染（随安装包分发固定的 Evergreen 运行时） | Microsoft 许可条款 |

> 各组件在 `tools/<组件>/` 下自带许可证文件，未做任何修改（除按需裁剪无关语言包）。
> 若审核需要，可提供每个组件的版本与来源 URL 清单。

### 4.4 通过后的接入方式（预留）

审核通过后，在 SignPath.io 里配置：

1. 创建 Project（指向本仓库）与 Artifact Configuration（`*.exe`）；
2. 建立 Signing Policy（一般要求从受信构建（trusted build）发起）；
3. 在 GitHub Actions 中加入签名步骤：

```yaml
      - name: 上传待签名产物
        uses: actions/upload-artifact@v4
        with:
          name: unsigned
          path: installer/*.exe

      - name: 提交 SignPath 签名请求
        uses: signpath/github-action-submit-signing-request@v1
        with:
          api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
          organization-id: ${{ secrets.SIGNPATH_ORG_ID }}
          project-slug: openclass-box
          signing-policy-slug: release-signing
          artifact-configuration-slug: installer
          github-artifact-id: ${{ steps.upload.outputs.artifact-id }}
          wait-for-completion: true
          output-artifact-directory: signed
```

4. 签名完成后把 `signed/` 里的安装包上传到 Release（替换未签名版本）。

> 说明：本仓库的发布包内含第三方二进制（OpenOffice / VLC / mpv），体积较大，
> 因此当前 Release 流程是「本地构建 → 上传 → sigstore 签名」。若 SignPath 的
> 策略要求全程在 CI 构建，我们可以补一个 `windows-latest` 的构建 workflow
> （脚本已具备，只是需要把第三方组件的获取步骤纳入 CI）。

---

## 五、验证与反馈

- 发现签名异常、校验值不符、或收到可疑的"OpenClass-Box 安装包"，请开 Issue 反馈；
- 请只从本项目 Releases 页面下载安装包：https://github.com/HMUG12/OpenClass-Box/releases
