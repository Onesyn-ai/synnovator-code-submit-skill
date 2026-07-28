---
name: synnovator-code-submit
version: 3.1.0
description: 在本地代码编辑 AI 工具中，优先使用 Forgejo 细粒度访问令牌和 Synnovator API 完成账号验证、仓库读取/创建，再通过 HTTPS 安全推送 main；没有可用 API 令牌时才使用 Git Credential Manager OAuth2 浏览器授权，SSH 仅为显式备用。
---

# Synnovator 代码提交 Skill

## 1. 目标

将当前本地文件夹安全上传到 Synnovator/Forgejo，并在后续更新时继续同步到正式 `main`。

本 Skill 是一个工具，但内部严格拆分为：

```text
check：只读检查 Git、GCM、Forgejo API 和仓库访问

auth：独立认证

repos：使用 Forgejo API 读取或创建仓库

push：只使用已有凭据扫描、提交和推送

run：一键编排 check → auth → repos → push
```

平台固定入口：

```text
Web/API 基地址：https://www.synnovator.com
API Swagger：https://www.synnovator.com/api/swagger
Forgejo API：https://www.synnovator.com/api/v1
访问令牌/OAuth2 管理：https://www.synnovator.com/user/settings/applications
```

不得默认使用无 `www` 的网页地址。HTTPS Git 远端统一规范化为：

```text
https://www.synnovator.com/<owner>/<repository>.git
```

---

## 2. 认证优先级

### 2.1 第一优先：Forgejo 访问令牌 + API + Git Credential Manager

必须先尝试从 Git Credential Manager 的安全凭据存储中复用现有访问令牌。验证接口必须根据任务选择，不能强迫最小权限令牌访问无关路由：

```text
已知目标仓库、只需同步/推送：
GET /api/v1/repos/{owner}/{repo}

需要识别账号、列出仓库或创建仓库：
GET /api/v1/user
GET /api/v1/user/repos
POST /api/v1/user/repos
```

已知目标仓库时，允许使用“特定仓库 + `write:repository`”令牌。此类令牌按 Forgejo 规则只能拥有 repository/issue scopes，调用 `/api/v1/user` 可能返回 `403`；这不是令牌失效，工具必须改用目标仓库 API 验证，不能删除该令牌或强迫用户扩大权限。

访问令牌可用于：

```text
GET  /api/v1/repos/{owner}/{repo} 验证指定仓库
GET  /api/v1/user                 验证账号（需要 user scope）
GET  /api/v1/user/repos           读取当前账号可见仓库（需要 user scope）
POST /api/v1/user/repos           新建个人仓库（需要 write:user）
HTTPS Git                         fetch / push
```

不得把令牌写入：

- 远端 URL；
- 命令行参数；
- 项目文件；
- `.git/config`；
- 日志；
- AI 回复。

令牌只能：

1. 通过隐藏输入读取；
2. 在当前进程内短暂保留；
3. 交给 Git Credential Manager 写入系统安全凭据存储；
4. 作为 HTTP `Authorization` 请求头在内存中发送。

### 2.2 第二优先：Git Credential Manager OAuth2

只有以下情况才进入 OAuth2：

- 未发现可用访问令牌；
- 用户拒绝创建访问令牌；
- 访问令牌方式被平台策略禁用；
- 用户明确执行 `auth --auth oauth`。

OAuth2 流程必须使用系统默认浏览器。真正的授权 URL、PKCE、state 和本机回调由 Git Credential Manager 生成，不得由 AI 手工拼接。

Forgejo 预注册的 Git Credential Manager OAuth2 客户端：

```text
Client ID：e90ee53c-94e2-48ac-9358-a874fb9e0662
Authorization Endpoint：/login/oauth/authorize
Token Endpoint：/login/oauth/access_token
Redirect URI：http://127.0.0.1
```

OAuth2 token 当前不具备 Forgejo 访问令牌的细粒度 scope。授权可能允许第三方应用代表账号执行超出单仓库 Git 推送范围的动作。因此：

- API 访问令牌优先；
- OAuth2 只作为备用；
- 必须提示用户核对应用名称 `Git Credential Manager`；
- 不再使用时，应在授权应用页面撤销访问。

### 2.3 第三优先：SSH 显式备用

SSH 不参与默认流程。只有用户明确执行：

```bash
python scripts/synnovator_submit.py ssh-bind --remote <平台实际 SSH 地址>
```

才进入 SSH 备用诊断。

不得假设：

```text
synnovator.com:22
```

就是平台 Git SSH 服务，也不得因 SSH 端口、公钥或 `authorized_keys` 故障阻塞 HTTPS 上传。

---

## 3. Forgejo 访问令牌权限

权限参考：

```text
https://forgejo.org/docs/latest/user/authentication/token-scope/
```

API 操作按 URL 路由分组。令牌必须选择满足任务的最小权限。

### 3.1 只推送已有仓库

建议：

```text
仓库访问范围：特定仓库
权限：write:repository
```

`write:repository` 包含对应仓库的读取能力。

### 3.2 读取当前用户和仓库列表

在需要调用 `/user/*` API 时增加：

```text
read:user
```

推荐组合：

```text
read:user
write:repository
```

### 3.3 通过 API 新建个人仓库

`POST /api/v1/user/repos` 属于 `/user/*` 路由。需要增加：

```text
write:user
```

推荐组合：

```text
read:user
write:user
write:repository
```

创建完成后，长期不再需要 `write:user` 时，应重新生成权限更小的令牌，并删除旧令牌。

### 3.4 禁止无关权限

默认不得要求：

```text
admin
organization
package
notification
activitypub
write:issue
```

除非用户的实际任务明确需要。

### 3.5 仓库访问范围

优先级：

1. 特定仓库；
2. 公共仓库；
3. 所有仓库。

需要列出账号下所有仓库或创建新仓库时，特定仓库令牌可能无法调用 `/user/repos`，此时应说明限制，而不是误判为平台故障。

---

## 4. 浏览器打开规则

### 4.1 系统默认浏览器

CLI 可通过操作系统 URL 关联打开默认浏览器：

```bash
# macOS
open "https://www.synnovator.com/user/settings/applications"

# Linux 桌面
xdg-open "https://www.synnovator.com/user/settings/applications"

# Windows CMD
start "" "https://www.synnovator.com/user/settings/applications"

# Windows PowerShell
Start-Process "https://www.synnovator.com/user/settings/applications"

# WSL
cmd.exe /c start "" "https://www.synnovator.com/user/settings/applications"
```

脚本优先调用 Python `webbrowser`，失败时再调用上述系统命令。

### 4.2 能打开不代表能控制

调用系统浏览器只负责打开页面。除非 AI 工具有 Browser/Playwright/MCP 等能力，否则不得声称能够：

- 读取用户浏览器中的页面；
- 自动点击按钮；
- 自动填写令牌；
- 确认当前登录账号。

AI 必须等待用户完成页面操作。

### 4.3 无图形环境

在 SSH 服务器、容器、CI 或无桌面的环境中：

- 自动打开浏览器可能失败；
- 必须打印完整 URL；
- OAuth2 可能无法完成本机浏览器回调；
- 此时优先使用由用户在另一台可信设备创建的细粒度访问令牌，再通过隐藏输入写入 GCM。

---

## 5. 阶段 A：只读检查 `check`

命令：

```bash
python scripts/synnovator_submit.py check \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

只允许：

1. 检查 Git；
2. 检查 Git Credential Manager；
3. 检查 GCM 版本不低于 `2.4.1`；
4. 调用 `GET /api/v1/version`；
5. 显示 Swagger 地址；
6. 非交互读取系统凭据库；
7. 若发现疑似 PAT：已知远端时优先以 `GET /api/v1/repos/{owner}/{repo}` 验证；需要账号/仓库列表时再以 `GET /api/v1/user` 验证；
8. 若提供远端，以非交互 `git ls-remote` 检查。

禁止：

- 打开浏览器；
- 要求用户输入令牌；
- 初始化仓库；
- 修改 `origin`；
- 创建提交；
- 推送；
- 生成 SSH 密钥。

错误必须分类为：

```text
DNS
443/TCP
TLS
401
403
repository not found
GCM 不可用或版本过低
```

不得执行：

```bash
git config --global http.sslVerify false
```

---

## 6. 阶段 B：独立认证 `auth`

### 6.1 自动认证

```bash
python scripts/synnovator_submit.py auth \
  --auth auto \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

顺序：

```text
检查 API 与 Swagger
→ 从参数、已有 origin 或上次配置识别目标仓库
→ 尝试复用 GCM 中的访问令牌
→ 已知仓库时用 GET /api/v1/repos/{owner}/{repo} 验证最小权限令牌
→ 需要列表/创建时用 GET /api/v1/user 验证
→ 没有令牌时打开访问令牌页面
→ 隐藏输入令牌
→ API 验证并交给 GCM 安全保存
→ 验证 HTTPS Git 仓库访问
→ PAT 流程取消或不可用时才进入 OAuth2
```

### 6.2 只使用访问令牌

```bash
python scripts/synnovator_submit.py auth --auth pat
```

脚本应：

1. 显示最小 scope 建议；
2. 显示 Swagger 和 Forgejo scope 文档；
3. 要求准确输入 `创建 API 令牌`；
4. 打开：

   ```text
   https://www.synnovator.com/user/settings/applications
   ```

5. 使用 `getpass` 隐藏读取令牌；
6. 已提供目标仓库时，先调用 `GET /api/v1/repos/{owner}/{repo}` 验证特定仓库令牌；需要列表/创建能力时才调用 `GET /api/v1/user`；
7. 用真实用户名和令牌执行 `git credential approve`；
8. 不显示令牌的长度、前缀、后缀或哈希；
9. 若特定仓库令牌访问 `/user` 返回 `403`，不得误判为失效，也不得自动撤销。

### 6.3 只使用 OAuth2

```bash
python scripts/synnovator_submit.py auth \
  --auth oauth \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

OAuth2 必须有真实仓库 HTTPS 地址，因为 GCM 需要通过实际 Git HTTP 认证挑战触发授权。

顺序：

1. 非交互执行 `git ls-remote`，尝试复用现有 OAuth2；
2. 若失败，提示 OAuth2 权限范围较宽；
3. 要求输入 `使用 OAuth2`；
4. 先打开登录页，方便用户选择正确账号；
5. 再由 GCM 启动真正 OAuth2 授权页；
6. 用户授权应用 `Git Credential Manager`；
7. 授权完成后再次以非交互方式验证；
8. 非交互验证失败则停止。

不得手工生成 OAuth2 `state`、PKCE verifier、authorization code 或 access token。

---

## 7. 阶段 C：仓库读取和创建 `repos`

命令：

```bash
python scripts/synnovator_submit.py repos
```

仅在 PAT/API 模式下使用。

### 7.1 读取仓库

使用：

```http
GET /api/v1/user/repos?page=<N>&limit=50
```

展示：

- `full_name`；
- `private/public`；
- 最近更新时间排序；
- 最多先显示 50 个供选择。

不得用网页抓取或猜测仓库列表。

### 7.2 新建仓库

询问：

1. 仓库名称；
2. `private` 或 `public`；
3. 描述；
4. 二次确认。

新仓库默认建议：

```text
private
```

API：

```http
POST /api/v1/user/repos
Content-Type: application/json

{
  "name": "<name>",
  "private": true,
  "description": "<description>",
  "auto_init": false
}
```

创建 `public` 前必须输入：

```text
创建公开仓库
```

若 API 返回 403：

- 说明令牌可能缺少 `write:user`；
- 不得要求 admin 权限；
- 可打开网页新建仓库；
- 用户创建后粘贴 HTTPS 克隆地址。

---

## 8. 已 clone 仓库处理

若当前文件夹已有 `.git` 和 `origin`，必须展示：

- 仓库根目录；
- 当前 `origin`；
- 当前分支；
- 跟踪分支；
- 最近提交。

然后要求准确输入：

```text
继续同步
```

没有该确认，不能继续使用该仓库。

若 `origin` 是 SSH，必须展示转换后的 HTTPS 地址，并要求：

```text
转换 HTTPS
```

不得静默修改远端。

---

## 9. 阶段 D：提交推送 `push`

命令：

```bash
python scripts/synnovator_submit.py push \
  --remote https://www.synnovator.com/<owner>/<repo>.git \
  --mode incremental
```

`push` 不得打开浏览器、要求新令牌或静默授权。没有可非交互复用的 PAT/OAuth2 时必须停止，并提示运行 `auth` 或 `run`。

### 9.1 安全扫描

必须检查：

- `.env`、`.env.*`；
- SSH/证书私钥；
- `.npmrc`、`.pypirc`、`.netrc`；
- 云凭据和服务账号文件；
- `.ssh/`、`.aws/`；
- Token、密码、访问密钥模式；
- `.synnovator-submit-skill/` 等放在项目内的工具副本；
- `node_modules/`、虚拟环境和构建目录；
- 20 MiB 以上文件；
- 50 MiB 以上高风险文件；
- 100 MiB 以上阻止文件。

允许模板：

```text
.env.example
.env.sample
```

扫描报告只显示文件路径、行号和匹配类型，不打印秘密值。

### 9.2 推送前二次确认

必须展示：

- 目标仓库；
- HTTPS 地址；
- 认证方式；
- 目标分支 `main`；
- 发布模式；
- 远端 `main` 当前提交；
- 文件数量和总体积；
- 风险目录与大文件；
- 已阻止的敏感文件类型。

普通推送要求：

```text
确认推送
```

### 9.3 正式 main

普通更新：

```bash
python scripts/synnovator_submit.py push --mode incremental
```

只有远端 `main` 能快进到当前提交时才允许。

### 9.4 当前文件夹作为新正式版本

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

必须先把原远端 `main` 保存为：

```text
archive/main-YYYYMMDD-HHMMSS
```

验证历史分支存在后，才能替换 `main`。

非快进时只允许：

```bash
git push --force-with-lease=refs/heads/main:<EXPECTED_OID> origin HEAD:main
```

禁止：

```bash
git push --force
```

---

## 10. 一键流程 `run`

命令：

```bash
python scripts/synnovator_submit.py run
```

不写子命令时也默认执行 `run`。

顺序：

```text
检查项目目录
→ 检查 Git/GCM
→ GET /api/v1/version
→ 第一优先复用 PAT
→ 没有 PAT 时打开令牌页面并验证
→ PAT 可用时通过 API 读取/创建仓库
→ 确认已有 clone/origin
→ PAT 取消或不可用时才进入 OAuth2 浏览器授权
→ 验证 HTTPS 仓库访问
→ 初始化 Git（如需）
→ 配置提交身份（当前仓库）
→ 配置 HTTPS origin
→ 追加安全 .gitignore
→ 风险扫描
→ 二次确认
→ 提交
→ 必要时备份远端 main
→ 推送 main
→ 对比本地和远端提交哈希
```

---

## 11. 中国区环境处理

### 11.1 Git/GCM 下载

不得自动把系统源替换成固定镜像。

优先级：

1. 系统已有 Git/GCM；
2. 用户当前包管理器和镜像配置；
3. 企业软件仓库或企业代理；
4. 用户明确同意后，使用与发行版、版本代号和架构匹配的清华 TUNA 镜像；
5. 官方发布包。

Windows 优先：

```powershell
winget install --id Git.Git -e --source winget
```

Git for Windows 通常包含 GCM。

macOS：

```bash
brew install --cask git-credential-manager
```

Linux 包管理器源变更前必须：

- 检查发行版和版本代号；
- 备份原源；
- 保留签名验证；
- 失败时恢复；
- 不写入不匹配的镜像地址。

### 11.2 代理

检查：

```bash
git config --global --get http.proxy
git config --global --get https.proxy
```

临时代理优先：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 \
python scripts/synnovator_submit.py run
```

只有用户明确要求时才设置全局代理。

禁止：

```bash
git config --global http.sslVerify false
```

---

## 12. AI 工具回复规范

AI 执行本 Skill 时必须：

1. 明确显示当前阶段：`check`、`auth`、`repos` 或 `push`；
2. API 令牌输入必须使用隐藏输入，不得让用户发到聊天中；
3. 不显示 Token、密码、Cookie、Authorization 头或 GCM 凭据输出；
4. 浏览器打开后说明：用户需要在系统浏览器自行登录、创建令牌或授权；
5. 不声称能看见或操作用户默认浏览器，除非当前工具确实具有浏览器控制能力；
6. OAuth2 页面必须核对应用名称 `Git Credential Manager`；
7. 区分：

   ```text
   平台已授权 OAuth2 应用
   ≠
   当前本机 GCM 中仍有有效凭据
   ```

8. PAT 认证成功时只显示账号名和 API/Git 验证结果；
9. 403 时先解释 scope 或仓库访问范围，不得直接要求管理员权限；
10. 不再优先排查 SSH；
11. 不提示用户输入 `git` 系统账号密码；
12. 任何推送前都必须展示风险和二次确认。

---

## 13. 常用命令

```bash
# 只读检查
python scripts/synnovator_submit.py check

# 打开 API 文档
python scripts/synnovator_submit.py api-docs

# API 令牌优先，OAuth2 备用
python scripts/synnovator_submit.py auth --auth auto \
  --remote https://www.synnovator.com/<owner>/<repo>.git

# 只使用 API 访问令牌
python scripts/synnovator_submit.py auth --auth pat

# 只使用 OAuth2
python scripts/synnovator_submit.py auth --auth oauth \
  --remote https://www.synnovator.com/<owner>/<repo>.git

# API 读取/创建仓库
python scripts/synnovator_submit.py repos

# 一键上传
python scripts/synnovator_submit.py run

# 普通更新
python scripts/synnovator_submit.py push --mode incremental

# 保存旧 main 后发布新快照
python scripts/synnovator_submit.py push --mode snapshot
```
