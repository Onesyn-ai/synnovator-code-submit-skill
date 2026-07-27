---
name: synnovator-code-submit
version: 2.0.0
description: 在本地代码编辑 AI 工具中，最高优先级使用 Git Credential Manager 的 Forgejo OAuth2 授权，通过 HTTPS 安全检查、选择仓库、扫描文件并推送到 Synnovator main；SSH 只作为显式备用方式。
---

# Synnovator 代码提交 Skill

## 1. 最终目标

让用户在本地代码编辑器、Codex、Copilot、Cursor、Claude Code 等 AI 编程环境中，把当前文件夹安全上传到 Synnovator/Forgejo 代码平台。

本 Skill 的默认认证方式必须是：

```text
HTTPS 仓库地址
→ Git Credential Manager
→ Forgejo 预注册的 Git Credential Manager OAuth2 应用
→ 浏览器授权或复用已有授权
→ 系统凭据保险库安全保存令牌
→ git fetch / git push
```

认证优先级固定为：

1. **最高优先级：复用已授权的 Git Credential Manager OAuth2 应用；**
2. 没有有效授权、授权已撤销或令牌失效时，通过 HTTPS Git 请求触发浏览器 OAuth2 授权；
3. 用户明确要求时，才允许使用个人访问令牌；
4. 用户明确选择 `--auth ssh` 时，才进入 SSH 备用流程。

不得因为 SSH 22 端口、主机指纹、公钥同步或 SSH 端口映射异常，阻塞默认的 HTTPS OAuth2 上传流程。

平台基地址：

```text
https://www.synnovator.com
```

OAuth2 授权管理页：

```text
https://www.synnovator.com/user/settings/applications
```

Forgejo 默认预注册的 Git Credential Manager OAuth2 客户端 ID：

```text
e90ee53c-94e2-48ac-9358-a874fb9e0662
```

此 ID 用于核对平台是否启用了 Forgejo/Gitea 的内置 Git Credential Manager OAuth 应用。普通用户不需要自行创建 OAuth2 应用，也不需要复制 Client Secret。

---

## 2. 核心原则

### 2.1 只使用 `www.synnovator.com` 的 HTTPS Git 地址

默认远端必须形如：

```text
https://www.synnovator.com/<owner>/<repository>.git
```

若用户提供：

```text
https://synnovator.com/<owner>/<repository>.git
```

应规范化为：

```text
https://www.synnovator.com/<owner>/<repository>.git
```

若当前仓库使用 SSH：

```text
git@synnovator.com:<owner>/<repository>.git
```

默认流程应解析仓库路径，提出明确确认后转换为：

```text
https://www.synnovator.com/<owner>/<repository>.git
```

不得把用户名、密码、Token 或 OAuth access token嵌入远端 URL。

### 2.2 不直接处理 OAuth Token

Skill 和脚本不得：

- 从 Git Credential Manager 输出中读取或打印 `password=`；
- 打印 access token、refresh token 或 Authorization 请求头；
- 把 Token 写入项目文件、日志、命令行、URL 或 `.git/config`；
- 使用 `credential.helper store` 将凭据明文保存到磁盘；
- 要求用户手工复制 OAuth Token。

Git Credential Manager 应负责：

- 打开浏览器授权；
- 接收本机回调；
- 保存 OAuth 凭据；
- 自动刷新或重新获取凭据；
- 在 Git HTTPS 请求时提供凭据。

### 2.3 认证和推送仍然分阶段

本 Skill 仍是一个工具，但内部保持独立阶段：

```text
check：只读检查

auth：独立 OAuth2 授权

push：独立扫描、提交和推送

run：check → 必要时 auth → push
```

`push` 认证失败时必须停止并要求运行 `auth`，不得在提交推送阶段静默生成 SSH 密钥。

---

## 3. 不可违反的安全规则

1. 禁止上传私钥，包括 `id_rsa`、`id_ed25519`、`*.pem`、`*.key`、`*.p12`、`*.pfx`。
2. 默认禁止上传 `.env`、`.env.*`、`.npmrc`、`.pypirc`、`.netrc`、云凭证和服务账号文件；允许 `.env.example`、`.env.sample`。
3. 禁止在日志中打印 OAuth Token、密码、Cookie、私钥或完整认证头。
4. 禁止使用：

   ```bash
   git config --global http.sslVerify false
   ```

5. 禁止使用裸 `git push --force`。
6. 改写远端 `main` 前，必须使用远端当前提交值创建历史分支并验证成功。
7. 不得未经确认修改全局 Git 配置。GCM 配置优先使用当前命令参数或当前仓库 `.git/config`。
8. 新仓库默认建议 `private`；只有用户明确选择后才能创建 `public`。
9. 不删除用户本地敏感文件，只停止提交、追加 `.gitignore` 或从 Git 索引中移除。
10. Forgejo OAuth2 在部分版本中没有细粒度 scope。授权 Git Credential Manager 可能代表用户执行超出单一仓库 Git 操作的其他动作。必须提醒用户可在授权应用页面撤销不再使用的授权。

---

## 4. 阶段 A：只读检查 `check`

命令：

```bash
python scripts/synnovator_submit.py check \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

只允许执行：

1. 检查 Git；
2. 检查 Git Credential Manager；
3. 检查 GCM 版本是否不低于 2.4.1；
4. 使用当前命令临时指定 GCM；
5. 禁止交互式登录，尝试 `git ls-remote --heads`；
6. 输出 DNS、443、TLS、仓库不存在、401、403 等分类结果。

本阶段禁止：

- 初始化 Git 仓库；
- 修改 `origin`；
- 修改全局凭据助手；
- 打开浏览器授权；
- 创建提交；
- 推送；
- 生成 SSH 密钥。

对于公开仓库，`git ls-remote` 可能无需身份认证即可成功。因此，“仓库可读”不一定等于“OAuth2 写权限已验证”。真正写权限在推送时由服务端再次验证。

---

## 5. 阶段 B：独立 OAuth2 授权 `auth`

命令：

```bash
python scripts/synnovator_submit.py auth \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

必须使用一个真实仓库的 HTTPS 克隆地址触发认证。原因是 Git 会先访问仓库并收到 Forgejo/Gitea 的 HTTP 认证挑战，然后 Git Credential Manager 才能识别通用 Forgejo OAuth2 配置。

流程：

1. 检查 GCM；
2. 先以非交互模式执行：

   ```bash
   git ls-remote --heads <HTTPS_REMOTE>
   ```

3. 若现有 OAuth2 凭据仍有效，直接复用并跳过授权；
4. 若失败，展示原因；
5. 要求用户输入 `授权 OAuth2`；
6. 再次执行真实的 HTTPS 仓库访问，允许 GCM 打开浏览器；
7. 用户在网页登录正确账号并授权 `Git Credential Manager`；
8. 授权完成后，以非交互模式重新执行仓库读取验证；
9. 验证通过才结束。

不得使用独立的 `curl` 命令读取 GCM Token，也不得捕获 `git credential fill` 的凭据输出。

### 5.1 已授权应用的处理

若平台设置页已经显示：

```text
Git Credential Manager
```

说明用户曾完成 OAuth2 授权。Skill 应先尝试复用系统凭据库中的现有凭据，不得要求用户重复授权。

若平台仍显示应用，但本机凭据已丢失、过期或属于另一个系统用户，则 GCM 可能重新打开浏览器。平台授权记录和本机凭据存储是两个不同状态，两边都必须有效。

### 5.2 授权失败分类

- DNS 失败：检查 `www.synnovator.com` 解析；
- 443 超时或拒绝：检查网络、代理和防火墙；
- TLS 失败：检查系统时间、证书链和代理，不得关闭 SSL 校验；
- 401：授权已撤销、凭据失效或 OAuth2 未完成；
- 403：账号没有仓库权限；
- repository not found：地址错误、仓库不存在或账号不可见；
- 浏览器授权成功但非交互复验失败：平台 OAuth2、GCM 凭据库或账号选择存在问题，停止推送。

---

## 6. 阶段 C：提交推送 `push`

命令：

```bash
python scripts/synnovator_submit.py push \
  --remote https://www.synnovator.com/<owner>/<repo>.git \
  --mode incremental
```

认证失败时立即停止，提示先运行 `auth`。

### 6.1 已 clone 仓库必须确认

若当前文件夹已有 `.git` 和 `origin`，必须展示：

- 仓库根目录；
- 当前 `origin`；
- 当前分支；
- 跟踪分支；
- 最近提交。

然后要求用户准确输入：

```text
继续同步
```

没有该确认不得沿用当前仓库。

若现有 `origin` 是 SSH，OAuth2 默认流程必须展示转换后的 HTTPS 地址，并要求输入：

```text
转换 HTTPS
```

### 6.2 新建仓库

没有目标仓库时，询问：

1. 仓库名称；
2. `private` 或 `public`；
3. 打开平台新建仓库页；
4. 用户创建完成后粘贴平台实际显示的 HTTPS 克隆地址。

没有明确 API 文档或连接器时，不得编造仓库创建 API。

### 6.3 当前仓库的 GCM 配置

推送前可在当前仓库 `.git/config` 中设置：

```ini
[credential]
    helper =
    helper = manager

[credential "https://www.synnovator.com"]
    provider = generic
    useHttpPath = false
    oauthClientId = e90ee53c-94e2-48ac-9358-a874fb9e0662
    oauthRedirectUri = http://127.0.0.1/
    oauthAuthorizeEndpoint = /login/oauth/authorize
    oauthTokenEndpoint = /login/oauth/access_token
    oauthDefaultUserName = OAUTH_USER
    oauthUseClientAuthHeader = true
```

这只影响当前项目，不修改全局 Git 配置。空的 `helper` 用于清除从全局继承的其他 helper 链，随后只使用 Git Credential Manager。

### 6.4 风险扫描

必须检查：

- `.env` 和其他环境文件；
- SSH 私钥和证书私钥；
- Token、密码、云访问密钥；
- `.ssh/`、`.aws/`；
- `node_modules/`、虚拟环境、构建目录；
- 20 MiB 以上文件；
- 50 MiB 以上高风险文件；
- 100 MiB 以上阻止文件。

扫描只报告匹配类型和文件位置，不打印秘密内容。

### 6.5 二次确认

推送前必须展示：

- 目标仓库和 HTTPS 地址；
- 认证方式：GCM OAuth2 / HTTPS；
- 目标分支 `main`；
- 模式；
- 远端 `main` 当前提交；
- 文件数量和总体积；
- 风险目录和大文件；
- 已阻止的敏感文件类型。

普通推送要求输入：

```text
确认推送
```

快照替换要求输入仓库名称。

### 6.6 后续更新和历史保存

普通更新：

```bash
python scripts/synnovator_submit.py push --mode incremental
```

若用户要用当前文件夹作为全新正式版本：

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

远端原 `main` 必须先保存为：

```text
archive/main-YYYYMMDD-HHMMSS
```

若不能快进，只允许：

```bash
git push --force-with-lease=refs/heads/main:<EXPECTED_OID> origin HEAD:main
```

---

## 7. 一键编排 `run`

命令：

```bash
python scripts/synnovator_submit.py run
```

不写子命令时也进入 `run`。

顺序：

```text
确定项目和目标仓库
→ 检查 Git/GCM
→ 复用现有 OAuth2 授权
→ 必要时浏览器授权
→ 再次只读验证
→ 确认已有 clone/origin
→ 配置当前仓库 GCM 和 HTTPS origin
→ 安全扫描
→ 二次确认
→ 提交
→ 必要时备份远端 main
→ 推送 main
→ 对比本地和远端提交哈希
```

---

## 8. SSH 备用流程

SSH 不再是默认方式。只有用户明确执行以下命令时启用：

```bash
python scripts/synnovator_submit.py ssh-bind \
  --remote <平台实际显示的 SSH 克隆地址>
```

兼容旧命令：

```bash
python scripts/synnovator_submit.py bind --remote <SSH_REMOTE>
```

SSH 流程必须：

- 使用仓库页面提供的实际 SSH host 和 port；
- 不假设 `synnovator.com:22` 一定是 Forgejo Git SSH 后端；
- 生成后完整展示 `.pub` 公钥；
- 指纹只供核验，不得代替公钥；
- 禁止密码回退；
- 失败后建议回到 OAuth2 HTTPS。

---

## 9. 中国区环境处理

### 9.1 软件安装

优先使用系统已有包管理器和当前镜像配置。Linux 包管理器下载失败时，可按发行版、版本代号和架构使用清华大学 TUNA 镜像。不得把不匹配的源写入系统。

修改软件源前：

1. 检查原配置；
2. 备份原配置；
3. 只替换确认匹配的仓库；
4. 镜像失败时恢复；
5. 不关闭签名或 TLS 校验。

Windows 优先更新 Git for Windows，因为它通常包含 Git Credential Manager：

```powershell
winget install --id Git.Git -e --source winget
```

macOS：

```bash
brew install --cask git-credential-manager
```

Linux 安装 GCM 时优先使用用户已有软件源、企业软件仓库或官方发布包。不得猜测带版本号的下载 URL。

### 9.2 代理

检查：

```bash
git config --global --get http.proxy
git config --global --get https.proxy
```

只对当前命令使用代理时，优先临时环境变量：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 \
python scripts/synnovator_submit.py auth \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

只有用户明确要求时才设置全局代理。

不得执行：

```bash
git config --global http.sslVerify false
```

---

## 10. AI 工具的回复规范

AI 执行本 Skill 时必须：

- 明确显示当前阶段；
- OAuth2 成功时只说授权和仓库访问通过，不显示 Token；
- 看到浏览器授权时告诉用户核对账号和应用名称 `Git Credential Manager`；
- 不把平台“已授权 OAuth2 应用”和本机 GCM 凭据混为同一状态；
- 不再优先排查 SSH 端口；
- 不提示用户输入 `git` 系统账号密码；
- 不把 OAuth2 access token当成普通密码要求用户复制；
- 失败时给出 DNS、443、TLS、401、403、仓库路径或 GCM 版本的分类结论。
