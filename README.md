# Synnovator Code Submit Skill

用于本地代码编辑器和 AI 编程工具的一键安全上传 Skill。

**版本 2.0 起，默认且最高优先级使用 HTTPS + Git Credential Manager + Forgejo OAuth2。SSH 只作为显式备用方式。**

## 文件

- `SKILL.md`：完整工作流、认证优先级、风险规则和 AI 行为要求。
- `scripts/synnovator_submit.py`：本地交互式提交工具。

## 默认认证流程

```text
HTTPS 仓库
→ Git Credential Manager
→ 浏览器授权 Git Credential Manager OAuth2 应用
→ 系统凭据保险库
→ Git 拉取和推送
```

平台授权管理页：

```text
https://www.synnovator.com/user/settings/applications
```

脚本不会读取或打印 OAuth access token、refresh token、密码或 Authorization 请求头。

## 环境要求

- Git 2.30 或更高版本；
- Git Credential Manager 2.4.1 或更高版本；
- 可访问 `https://www.synnovator.com` 的 443 端口；
- 一个真实仓库的 HTTPS 克隆地址。

Windows 的 Git for Windows 通常包含 GCM：

```powershell
winget install --id Git.Git -e --source winget
```

macOS：

```bash
brew install --cask git-credential-manager
```

## 四个主要入口

### 1. 只读检查

```bash
python scripts/synnovator_submit.py check \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

只检查 Git、GCM 和仓库读取，不打开浏览器、不初始化项目、不提交、不推送。

### 2. 独立 OAuth2 授权

```bash
python scripts/synnovator_submit.py auth \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

先复用已有 Git Credential Manager OAuth2 凭据。没有有效凭据时，GCM 会打开浏览器，让用户授权平台中的 `Git Credential Manager` 应用。

授权完成后脚本会再次以非交互模式读取仓库，确认凭据能够复用。

### 3. 独立提交推送

```bash
python scripts/synnovator_submit.py push \
  --remote https://www.synnovator.com/<owner>/<repo>.git \
  --mode incremental
```

`push` 不会静默授权。OAuth2 访问未通过时会停止并要求先运行 `auth`。

如果当前文件夹已经 clone 或配置了 `origin`，脚本会显示仓库信息，并要求输入：

```text
继续同步
```

如果原 `origin` 是 SSH，会要求确认转换为：

```text
https://www.synnovator.com/<owner>/<repo>.git
```

### 4. 一键编排

```bash
python scripts/synnovator_submit.py run
```

不写子命令时也默认运行 `run`：

```text
检查
→ 复用或完成 OAuth2 授权
→ 确认仓库
→ 安全扫描
→ 二次确认
→ 提交
→ 推送 main
→ 验证远端提交
```

## 更新模式

普通增量更新：

```bash
python scripts/synnovator_submit.py push --mode incremental
```

用当前文件夹作为新的正式版本，并先保存原 `main`：

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

原远端版本会保存到：

```text
archive/main-YYYYMMDD-HHMMSS
```

## 新建仓库

没有目标仓库时，工具会询问：

- 仓库名称；
- `private` 或 `public`；
- 是否打开平台新建仓库页面。

创建完成后，粘贴页面显示的 HTTPS 克隆地址。工具不会猜测平台私有 API。

## 安全扫描

默认阻止：

- `.env`、`.env.*`；
- SSH/证书私钥；
- `.npmrc`、`.pypirc`、`.netrc`；
- 云凭证和服务账号文件；
- 疑似 Token、密码、访问密钥；
- 不小于 100 MiB 的文件。

允许作为模板上传：

```text
.env.example
.env.sample
```

## SSH 备用方式

只有 OAuth2 不可用且用户明确选择时使用：

```bash
python scripts/synnovator_submit.py ssh-bind \
  --remote <平台页面实际显示的 SSH 克隆地址>
```

旧命令仍兼容：

```bash
python scripts/synnovator_submit.py bind --remote <SSH_REMOTE>
```

SSH 备用流程不会假设 `synnovator.com:22` 一定是平台 Git SSH 服务。

## OAuth2 安全说明

Forgejo 的 Git Credential Manager 应用由平台预注册。GCM 把凭据保存在系统安全存储中，而不是仓库文件里。

不再使用某台电脑或应用时，可在平台的“已授权 OAuth2 应用程序”区域撤销 `Git Credential Manager`。部分 Forgejo 版本的 OAuth2 Token没有细粒度 scope，因此只在可信电脑上授权。
