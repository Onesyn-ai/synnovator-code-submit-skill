# Synnovator Code Submit Skill

在本地代码编辑器、Codex、Copilot、Cursor、Claude Code 等 AI 编程环境中，将当前文件夹安全上传到 Synnovator/Forgejo。

版本 `3.1.0` 的认证顺序：

```text
Forgejo 细粒度访问令牌 + API + Git Credential Manager
→ 读取账号和仓库
→ 选择或创建仓库
→ HTTPS Git 推送
→ 没有可用令牌时才使用 GCM OAuth2 浏览器授权
→ SSH 仅为显式备用
```

## 文件

- `SKILL.md`：完整流程、API、scope、浏览器授权、安全规则和 AI 行为要求。
- `scripts/synnovator_submit.py`：交互式一键工具。

## 关键地址

```text
平台：https://www.synnovator.com
Swagger：https://www.synnovator.com/api/swagger
API：https://www.synnovator.com/api/v1
令牌/OAuth2：https://www.synnovator.com/user/settings/applications
Forgejo scope：https://forgejo.org/docs/latest/user/authentication/token-scope/
```

## 为什么 API 令牌优先

Forgejo 访问令牌可以限制 API 路由和仓库范围；OAuth2 token 当前没有同等细粒度 scope。因此默认先使用最小权限令牌：

```text
只推送指定已有仓库：特定仓库 + write:repository
读取账号和仓库列表：read:user + write:repository
通过 API 新建个人仓库：read:user + write:user + write:repository
```

令牌通过隐藏输入读取，并交给 Git Credential Manager 保存。脚本不会打印令牌，也不会把令牌写入 URL、项目文件或 `.git/config`。

已知目标仓库时，脚本允许使用只有 `write:repository` 的特定仓库令牌，并通过 `GET /api/v1/repos/{owner}/{repo}` 验证。此类令牌无法访问 `/api/v1/user` 时可能返回 `403`，脚本不会因此误删令牌或要求扩大权限。只有读取账号/仓库列表或通过 API 创建仓库时，才要求 `read:user`/`write:user`。

## 环境要求

- Python 3.10 或更高版本；
- Git 2.27 或更高版本；
- Git Credential Manager 2.4.1 或更高版本；
- 可访问 `https://www.synnovator.com:443`；
- 操作系统具有安全凭据存储。

Windows 通常通过 Git for Windows 获得 GCM：

```powershell
winget install --id Git.Git -e --source winget
```

macOS：

```bash
brew install --cask git-credential-manager
```

## 一键上传

在待上传项目目录执行：

```bash
python /path/to/synnovator-code-submit-skill/scripts/synnovator_submit.py run
```

流程：

```text
检查 Git/GCM/API
→ 复用安全存储中的访问令牌
→ 没有令牌时打开访问令牌页面
→ 已知仓库时用仓库 API 验证最小权限令牌
→ 需要时再用账号 API 读取或创建仓库
→ 确认已有 clone/origin
→ 无 PAT 时才启动 OAuth2 浏览器授权
→ 安全扫描
→ 二次确认
→ 提交并推送 main
→ 验证远端提交
```

## 只读检查

```bash
python scripts/synnovator_submit.py check \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

不会打开浏览器、初始化仓库、修改远端或推送。

## API 文档

```bash
python scripts/synnovator_submit.py api-docs
```

脚本会尝试使用系统默认浏览器打开 Swagger 和 Forgejo token scope 文档。

## 独立认证

API 令牌优先，OAuth2 备用：

```bash
python scripts/synnovator_submit.py auth --auth auto \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

只使用访问令牌：

```bash
python scripts/synnovator_submit.py auth --auth pat
```

只使用 OAuth2：

```bash
python scripts/synnovator_submit.py auth --auth oauth \
  --remote https://www.synnovator.com/<owner>/<repo>.git
```

OAuth2 模式会由 Git Credential Manager 启动系统默认浏览器。真正的授权 URL 和本机回调由 GCM 管理。

## 仓库读取和创建

```bash
python scripts/synnovator_submit.py repos
```

使用 Forgejo API：

```text
GET  /api/v1/repos/{owner}/{repo}  验证已知目标仓库
GET  /api/v1/user                 识别账号
GET  /api/v1/user/repos           读取仓库列表
POST /api/v1/user/repos           创建个人仓库
```

新仓库默认建议 `private`。公开仓库必须额外确认。

## 已 clone 的文件夹

检测到 `.git` 和 `origin` 时，脚本会显示：

- 根目录；
- 远端地址；
- 当前分支；
- 跟踪分支；
- 最近提交。

必须输入：

```text
继续同步
```

若原远端是 SSH，还必须输入：

```text
转换 HTTPS
```

## 单独推送

普通增量更新：

```bash
python scripts/synnovator_submit.py push --mode incremental
```

使用当前文件夹替换正式版本，并先保存原 `main`：

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

历史分支：

```text
archive/main-YYYYMMDD-HHMMSS
```

`push` 不会静默打开浏览器或要求新令牌。没有已有凭据时会停止。

## 安全扫描

默认阻止：

- `.env`、`.env.*`；
- SSH/证书私钥；
- `.npmrc`、`.pypirc`、`.netrc`；
- 云凭据和服务账号文件；
- 项目内克隆的 `.synnovator-submit-skill/` 工具目录；
- 疑似 Token、密码、访问密钥；
- 不小于 100 MiB 的文件。

允许模板：

```text
.env.example
.env.sample
```

## 浏览器行为

脚本可以启动默认浏览器，但普通 CLI 无法自动读取或操作该浏览器页面。用户需要自行：

- 登录正确账号；
- 创建最小权限访问令牌；
- 或授权 `Git Credential Manager` OAuth2 应用。

在无桌面环境中，自动打开浏览器可能失败，脚本会打印 URL。

## SSH 备用

```bash
python scripts/synnovator_submit.py ssh-bind \
  --remote <平台仓库页面显示的完整 SSH 地址>
```

SSH 不再是默认方式，也不会假设 `synnovator.com:22` 是平台 Git SSH 服务。
