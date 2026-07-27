---
name: synnovator-code-submit
version: 1.0.0
description: 在本地代码编辑器或 AI 编程工具中，安全地检查 Git、配置 SSH、选择或创建仓库、扫描敏感文件，并将当前项目发布到 Synnovator 类代码托管平台的 main 分支。
---

# Synnovator 代码提交 Skill

## 1. 目标

将当前本地项目以尽量少的操作发布到代码托管平台，同时确保：

- 本机存在可用的 Git；
- 中国区网络环境下给出可执行的安装、代理和超时处理方案；
- 优先使用 SSH，不要求用户反复输入账号密码；
- 已绑定 SSH 公钥时自动跳过密钥生成与绑定步骤；
- 能识别当前仓库、选择已有仓库，或引导创建新仓库；
- 正式发布分支固定为 `main`；
- 推送前必须扫描敏感文件、构建产物和大文件；
- 推送前必须进行二次确认；
- 覆盖或重新发布 `main` 前，先把远端原 `main` 保存到历史分支；
- 不把 `.env`、私钥、访问令牌、云凭证等文件上传；
- 不静默执行破坏性操作，不静默覆盖远端历史。

平台设置页：

```text
https://www.synnovator.com/user/settings/keys
```

默认 Git SSH 主机：

```text
synnovator.com
```

> 不要假设平台提供 GitHub、GitLab 或 Gitea 的同名 API。没有明确 API 文档时，不得编造接口。仓库列表和仓库创建应优先使用当前 AI 工具已有的浏览器能力、平台插件或已配置 API；否则回退为让用户粘贴仓库 SSH 地址。

---

## 2. 适用场景

当用户表达以下意图时启用本 Skill：

- “把这个项目上传到代码平台”；
- “提交比赛代码”；
- “推到 Synnovator”；
- “给这个文件夹建仓库并上传”；
- “更新之前上传的代码”；
- “把当前版本发布到 main”。

默认项目目录为当前工作区根目录。若检测到多个项目根目录、嵌套 Git 仓库或 monorepo，必须先让用户选择具体目录。

---

## 3. 不可违反的安全规则

1. **禁止上传私钥。** 包括但不限于：
   - `id_rsa`、`id_ed25519`、`*.pem`、`*.key`、`*.p12`、`*.pfx`；
   - 含有 `BEGIN ... PRIVATE KEY` 的文件。
2. **默认禁止上传环境和凭证文件。** 包括：
   - `.env`、`.env.*`，但允许 `.env.example`、`.env.sample`；
   - `.npmrc`、`.pypirc`、`.netrc`；
   - `credentials.json`、`service-account*.json`、`firebase-adminsdk*.json`；
   - `.aws/`、`.ssh/`、`kubeconfig`；
   - 任何包含可疑 Token、密码、访问密钥的文件。
3. 不得使用裸 `git push --force`。需要改写 `main` 时，只允许使用带远端预期提交值的：

   ```bash
   git push --force-with-lease=refs/heads/main:<EXPECTED_OID> origin HEAD:main
   ```

4. 远端 `main` 非空时，在任何覆盖式发布前必须先创建历史分支并验证创建成功。
5. 未得到用户明确确认前，不得：
   - 安装系统软件；
   - 修改全局 Git 配置；
   - 创建或替换 SSH 密钥；
   - 创建远端仓库；
   - 执行提交；
   - 执行推送；
   - 改写远端 `main`。
6. 不删除用户本地文件。对敏感文件只做忽略、停止提交或从 Git 索引移除，不删除磁盘上的原文件。
7. 不在日志中打印 Token、密码、私钥内容或完整认证响应。
8. 新建仓库时默认建议 `private`。只有用户明确选择后才创建 `public` 仓库。

---

## 4. 总体流程

严格按以下顺序执行：

1. 确定项目根目录；
2. 检查 Git；
3. 检查 Git 用户身份；
4. 检查 SSH 密钥和平台绑定状态；
5. 获取仓库列表或目标仓库地址；
6. 必要时创建新仓库；
7. 初始化或检查本地 Git 仓库；
8. 更新 `.gitignore`；
9. 扫描敏感文件、风险目录和大文件；
10. 获取远端 `main` 状态；
11. 生成推送计划；
12. 展示风险并二次确认；
13. 必要时保存远端 `main` 到历史分支；
14. 提交本地变更；
15. 推送到正式 `main`；
16. 验证远端提交；
17. 输出结果和下一次“一键更新”命令。

任何一步失败都应停止后续写操作，并给出明确修复命令。

---

## 5. 第一步：识别项目目录

执行：

```bash
pwd
```

Windows PowerShell：

```powershell
Get-Location
```

检查：

- 当前目录是否存在源码或项目配置文件；
- 是否存在 `.git`；
- 是否存在嵌套 `.git`；
- 是否是工作区上层目录而非项目根目录；
- 是否包含多个独立项目。

可用于判断项目根目录的常见文件：

```text
package.json
pyproject.toml
requirements.txt
Cargo.toml
go.mod
pom.xml
build.gradle
CMakeLists.txt
*.sln
*.csproj
README.md
```

若发现多个候选项目，显示相对路径、主要语言和是否已有 Git，然后询问用户选择。

---

## 6. 第二步：检查并安装 Git

### 6.1 检查

```bash
git --version
```

建议 Git 版本不低于 2.30。若版本过低，提示升级，但不要强制升级。

### 6.2 Git 不存在时

先识别操作系统和可用包管理器，再展示将要执行的命令，得到确认后安装。

#### Windows

优先：

```powershell
winget install --id Git.Git -e --source winget
```

可选：

```powershell
choco install git -y
```

安装后重新打开终端，验证：

```powershell
git --version
ssh -V
```

#### macOS

```bash
xcode-select --install
```

或：

```bash
brew install git
```

#### Debian / Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y git openssh-client
```

#### Fedora / RHEL / Rocky / AlmaLinux

```bash
sudo dnf install -y git openssh-clients
```

#### Arch Linux

```bash
sudo pacman -S --needed git openssh
```

### 6.3 中国区环境：优先使用清华大学 TUNA 镜像

在中国大陆网络环境中，若检测到 Git 未安装，或系统软件源下载明显超时、失败，优先向用户提供清华大学开源软件镜像站地址。AI 必须先识别操作系统、Linux 发行版、版本代号和 CPU 架构，再选择匹配的镜像配置；不得把其他发行版或错误版本代号的源写入系统。

清华大学开源软件镜像站统一入口：

```text
https://mirrors.tuna.tsinghua.edu.cn/
```

仅 IPv4 网络可使用：

```text
https://mirrors4.tuna.tsinghua.edu.cn/
```

#### Ubuntu

镜像仓库与配置帮助：

```text
https://mirrors.tuna.tsinghua.edu.cn/ubuntu/
https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu/
```

先检查版本和架构：

```bash
cat /etc/os-release
dpkg --print-architecture
```

Ubuntu 24.04 及以上通常使用 `/etc/apt/sources.list.d/ubuntu.sources`；更早版本通常使用 `/etc/apt/sources.list`。替换前必须备份原配置，并从上面的帮助页选取与当前版本代号一致的内容。完成后执行：

```bash
sudo apt-get update
sudo apt-get install -y git openssh-client
```

不要把 `security.ubuntu.com` 安全更新源强制替换为镜像源，避免因同步延迟错过最新安全更新。

#### Debian

镜像仓库与配置帮助：

```text
https://mirrors.tuna.tsinghua.edu.cn/debian/
https://mirrors.tuna.tsinghua.edu.cn/help/debian/
```

先检查版本和架构：

```bash
cat /etc/os-release
dpkg --print-architecture
```

替换前必须备份 `/etc/apt/sources.list` 或 `/etc/apt/sources.list.d/debian.sources`，并使用与当前 Debian 版本代号一致的配置。完成后执行：

```bash
sudo apt-get update
sudo apt-get install -y git openssh-client
```

安全更新源优先保留 `security.debian.org`，不要默认改为镜像源。

#### Fedora

镜像仓库与配置帮助：

```text
https://mirrors.tuna.tsinghua.edu.cn/fedora/
https://mirrors.tuna.tsinghua.edu.cn/help/fedora/
```

只有默认 Metalink 在当前网络不可用时，才按帮助页备份并修改 `/etc/yum.repos.d/fedora.repo` 与 `/etc/yum.repos.d/fedora-updates.repo`。然后执行：

```bash
sudo dnf makecache
sudo dnf install -y git openssh-clients
```

#### Arch Linux

镜像仓库与配置帮助：

```text
https://mirrors.tuna.tsinghua.edu.cn/archlinux/
https://mirrors.tuna.tsinghua.edu.cn/help/archlinux/
```

在 `/etc/pacman.d/mirrorlist` 顶部加入：

```text
Server = https://mirrors.tuna.tsinghua.edu.cn/archlinux/$repo/os/$arch
```

备份原文件后执行：

```bash
sudo pacman -Syy
sudo pacman -S --needed git openssh
```

#### macOS / Homebrew

Homebrew 镜像帮助：

```text
https://mirrors.tuna.tsinghua.edu.cn/help/homebrew/
https://mirrors.tuna.tsinghua.edu.cn/help/homebrew-bottles/
```

只对当前终端临时使用清华镜像时：

```bash
export HOMEBREW_BREW_GIT_REMOTE="https://mirrors.tuna.tsinghua.edu.cn/git/homebrew/brew.git"
export HOMEBREW_API_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles/api"
export HOMEBREW_BOTTLE_DOMAIN="https://mirrors.tuna.tsinghua.edu.cn/homebrew-bottles"
brew install git
```

不要未经确认把这些变量永久写入 `~/.zprofile`、`~/.bash_profile` 或其他 shell 配置文件。

#### Windows

Windows 上不应拼接或猜测带版本号的清华镜像安装包地址。优先从清华镜像统一入口的“获取下载链接”查找可用安装包；若没有可验证的 Git for Windows 镜像，则使用系统包管理器安装：

```powershell
winget install --id Git.Git -e --source winget
```

#### 失败回退与安全要求

若清华镜像不可用，先恢复备份的源配置，再回退到用户原有软件源或系统默认源。不得为了“加速”关闭 TLS/SSL 校验，也不得执行：

```bash
git config --global http.sslVerify false
```

Git 安装完成后统一验证：

```bash
git --version
ssh -V
```

---

## 7. 第三步：检查 Git 用户身份

读取：

```bash
git config --get user.name
git config --get user.email
```

若当前仓库已有局部配置，优先使用局部配置：

```bash
git config --local --get user.name
git config --local --get user.email
```

缺失时询问：

- 提交显示名称；
- 提交邮箱；
- 只配置当前项目，还是配置全局。

当前项目配置：

```bash
git config user.name "<NAME>"
git config user.email "<EMAIL>"
```

全局配置只有在用户明确选择后执行：

```bash
git config --global user.name "<NAME>"
git config --global user.email "<EMAIL>"
```

---

## 8. 第四步：检查 SSH 密钥和绑定状态

### 8.1 检查现有密钥

检查：

```bash
ls -la ~/.ssh
```

重点寻找：

```text
id_ed25519_synnovator
id_ed25519_synnovator.pub
id_ed25519
id_ed25519.pub
id_rsa
id_rsa.pub
```

不得覆盖已有私钥。若目标文件已存在，必须复用、改用新文件名，或让用户选择。

### 8.2 测试平台认证

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@synnovator.com
```

判断规则：

- 若输出明确表示认证成功，即使命令退出码非 0，也可视为已绑定；
- 若出现 `Permission denied (publickey)`，视为未绑定或未选中正确密钥；
- 若是 DNS、超时或主机不可达，不得误判为未绑定，应先处理网络；
- 首次连接出现主机指纹确认时，向用户展示主机名和指纹，确认后再加入 `known_hosts`。

已认证成功时：

- 跳过密钥生成；
- 跳过设置页粘贴步骤；
- 直接进入仓库选择。

### 8.3 生成专用 SSH 密钥

优先 Ed25519：

```bash
ssh-keygen -t ed25519 -C "<EMAIL>" -f ~/.ssh/id_ed25519_synnovator
```

仅在系统不支持 Ed25519 时使用：

```bash
ssh-keygen -t rsa -b 4096 -C "<EMAIL>" -f ~/.ssh/id_rsa_synnovator
```

建议用户为私钥设置口令。不得把私钥口令写入脚本、配置或日志。

### 8.4 配置 SSH 使用专用密钥

在 `~/.ssh/config` 中追加，不覆盖原内容：

```sshconfig
Host synnovator.com
  HostName synnovator.com
  User git
  IdentityFile ~/.ssh/id_ed25519_synnovator
  IdentitiesOnly yes
  ServerAliveInterval 15
```

Windows 对应路径通常为：

```text
C:\Users\<用户名>\.ssh\config
```

修正权限：

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/config
chmod 600 ~/.ssh/id_ed25519_synnovator
chmod 644 ~/.ssh/id_ed25519_synnovator.pub
```

### 8.5 启动 ssh-agent 并加载密钥

Linux / macOS：

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519_synnovator
```

Windows PowerShell：

```powershell
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent
ssh-add $HOME\.ssh\id_ed25519_synnovator
```

若修改系统服务需要管理员权限，先说明并确认。

### 8.6 读取并复制公钥

```bash
cat ~/.ssh/id_ed25519_synnovator.pub
```

Windows PowerShell：

```powershell
Get-Content $HOME\.ssh\id_ed25519_synnovator.pub
```

公钥应以以下之一开头：

```text
ssh-ed25519
ssh-rsa
ecdsa-sha2-nistp256
ecdsa-sha2-nistp384
ecdsa-sha2-nistp521
sk-ecdsa-sha2-nistp256@openssh.com
sk-ssh-ed25519@openssh.com
```

复制到剪贴板：

macOS：

```bash
pbcopy < ~/.ssh/id_ed25519_synnovator.pub
```

Windows：

```powershell
Get-Content $HOME\.ssh\id_ed25519_synnovator.pub | Set-Clipboard
```

Linux，按可用命令选择：

```bash
wl-copy < ~/.ssh/id_ed25519_synnovator.pub
```

或：

```bash
xclip -selection clipboard < ~/.ssh/id_ed25519_synnovator.pub
```

### 8.7 指导用户绑定

告诉用户：

1. 打开 `https://www.synnovator.com/user/settings/keys`；
2. 在“密钥名称”中填写可识别名称，例如 `比赛电脑-2026`；
3. 在“密钥内容”中粘贴刚才读取出的整行公钥；
4. 点击“增加密钥”；
5. 回到终端后继续。

然后再次执行：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@synnovator.com
```

只有验证通过后才能进入推送阶段。

---

## 9. 第五步：选择已有仓库或创建新仓库

### 9.1 优先检测当前仓库远端

```bash
git remote -v
```

若当前目录已经存在指向 `synnovator.com` 的 `origin`，显示：

- 远端名称；
- fetch URL；
- push URL；
- 当前分支；
- 是否有跟踪分支。

询问是否继续使用。用户确认后可跳过仓库列表。

### 9.2 读取已绑定用户的仓库

按以下优先级：

1. 当前 AI 工具已连接的平台插件；
2. 当前 AI 工具的浏览器自动化，且用户已登录；
3. 平台明确提供并已配置的 API；
4. 本地保存的最近仓库列表；
5. 让用户粘贴仓库 SSH 克隆地址。

展示仓库时至少包含：

- 仓库名；
- 命名空间或所有者；
- `private` / `public`；
- 默认分支；
- SSH 地址；
- 最近更新时间（若可得）。

不要通过猜测路径来伪造“仓库列表”。

### 9.3 用户不满意时创建仓库

依次询问：

1. 仓库名称；
2. 可选描述；
3. 可见性：`private` 或 `public`；
4. 是否确认创建。

仓库名建议规则：

```text
[a-zA-Z0-9._-]
```

建议使用小写英文和短横线。若名称包含空格或平台不支持字符，先转换并让用户确认。

新仓库建议：

- 不初始化 README；
- 不初始化 `.gitignore`；
- 不初始化 License；
- 默认分支为 `main`。

这样可避免首次推送出现不相关历史。

若无可用 API 或浏览器自动化，指导用户在网页中新建仓库，然后粘贴仓库的 SSH 地址，例如：

```text
git@synnovator.com:<owner>/<repo>.git
```

上面的格式只是示例，必须以平台页面实际显示的 SSH 克隆地址为准。

---

## 10. 第六步：初始化或检查本地 Git

### 10.1 非 Git 项目

```bash
git init
git branch -M main
```

### 10.2 已有 Git 项目

检查：

```bash
git status --short --branch
git branch --show-current
git log -1 --oneline
```

若当前分支不是 `main`，不要直接重命名用户正在开发的分支。询问：

- 将当前分支内容发布到远端 `main`；或
- 本地分支也改名为 `main`。

默认只执行：

```bash
git push origin HEAD:main
```

### 10.3 设置远端

无 `origin`：

```bash
git remote add origin <SSH_URL>
```

已有 `origin` 但地址不同：

- 展示旧地址和新地址；
- 询问是否替换；
- 确认后执行：

```bash
git remote set-url origin <SSH_URL>
```

验证：

```bash
git remote get-url origin
git ls-remote origin
```

---

## 11. 第七步：生成和维护 `.gitignore`

若已有 `.gitignore`，只能追加缺失规则，保留用户原内容。

推荐基础规则：

```gitignore
# Environment and secrets
.env
.env.*
!.env.example
!.env.sample
*.pem
*.key
*.p12
*.pfx
id_rsa
id_rsa.*
id_ed25519
id_ed25519.*
credentials.json
service-account*.json
firebase-adminsdk*.json
.aws/
.ssh/
kubeconfig
.netrc
.npmrc
.pypirc

# Dependencies
node_modules/
.venv/
venv/
env/
vendor/

# Build outputs
build/
dist/
out/
target/
coverage/
.next/
.nuxt/
.cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/
__pycache__/
*.py[cod]

# Logs and temporary files
*.log
logs/
*.tmp
*.temp
*.swp
.DS_Store
Thumbs.db

# IDE local state
.idea/
.vscode/
*.user
*.suo
```

注意：

- `.vscode/` 中可能包含团队需要的配置。若项目已明确提交该目录，不应自动移除；
- `vendor/` 是否忽略取决于语言和比赛规则；
- `dist/` 是否提交取决于平台是否要求部署产物；
- 规则冲突时，必须询问用户。

检查忽略效果：

```bash
git status --short --ignored
```

若敏感文件已经被 Git 跟踪，仅添加 `.gitignore` 不会停止跟踪。必须提示并确认后执行：

```bash
git rm --cached <FILE>
```

目录：

```bash
git rm -r --cached <DIR>
```

这只从 Git 索引移除，不删除本地文件。

---

## 12. 第八步：推送前风险扫描

### 12.1 必查文件名

阻止或警告以下文件：

```text
.env
.env.local
.env.production
*.pem
*.key
*.p12
*.pfx
id_rsa
id_ed25519
credentials.json
service-account*.json
firebase-adminsdk*.json
kubeconfig
.npmrc
.pypirc
.netrc
```

### 12.2 必查内容模式

对小于 2 MiB 的文本文件扫描：

```text
-----BEGIN PRIVATE KEY-----
-----BEGIN RSA PRIVATE KEY-----
-----BEGIN OPENSSH PRIVATE KEY-----
AKIA[0-9A-Z]{16}
ASIA[0-9A-Z]{16}
sk-[A-Za-z0-9_-]{20,}
ghp_[A-Za-z0-9]{20,}
github_pat_[A-Za-z0-9_]{20,}
Bearer <token>
password=<非空值>
secret=<非空值>
api_key=<非空值>
access_token=<非空值>
```

内容扫描只能用于提示和阻止，不要把完整匹配值打印出来。输出文件路径、行号和凭证类型，匹配值必须打码。

### 12.3 风险目录

默认忽略或警告：

```text
node_modules/
.venv/
venv/
dist/
build/
target/
coverage/
.next/
.cache/
__pycache__/
.git/
```

### 12.4 大文件

- 大于 20 MiB：提示；
- 大于 50 MiB：高风险提示；
- 大于 100 MiB：默认阻止普通 Git 推送，建议 Git LFS 或移除文件。

检查：

```bash
find . -type f -size +50M -not -path './.git/*'
```

跨平台脚本应使用 Python 遍历，不依赖 `find`。

### 12.5 确认待提交文件

```bash
git ls-files --cached --others --modified --exclude-standard
```

对将上传的内容汇总：

- 文件总数；
- 新增、修改、删除数量；
- 总大小；
- 最大的 10 个文件；
- 被忽略文件数量；
- 敏感文件命中；
- 可疑内容命中；
- 构建产物；
- 二进制文件。

有高危命中时停止推送，不允许通过普通“确认”绕过。必须先移除或显式加入安全例外，并说明理由。

---

## 13. 第九步：检查远端 `main`

```bash
git fetch origin main --prune
```

远端不存在 `main`：

```bash
git ls-remote --exit-code --heads origin main
```

退出码表示不存在时，视为首次推送。

远端存在时记录：

```bash
git rev-parse refs/remotes/origin/main
```

将该 OID 保存为 `<EXPECTED_OID>`，后续 `force-with-lease` 必须使用这个精确值。

同时检查本地与远端关系：

```bash
git merge-base --is-ancestor origin/main HEAD
```

分类：

- **首次推送**：远端无 `main`；
- **快进更新**：远端 `main` 是本地 `HEAD` 的祖先；
- **远端领先**：本地缺少远端提交；
- **分叉历史**：双方都有不同提交；
- **无关历史**：本地项目与远端不是同一 Git 历史。

不得在没有解释分类的情况下直接推送。

---

## 14. 第十步：推送计划与二次确认

在任何提交和推送前，必须输出类似以下计划：

```text
发布目录：/path/to/project
目标仓库：owner/repo
远端地址：git@synnovator.com:owner/repo.git
目标分支：main
发布模式：首次发布 / 普通更新 / 快照替换
远端 main：<OID 或不存在>
历史备份分支：archive/main-20260727-153012
待提交：128 个文件，新增 110，修改 17，删除 1
预计上传大小：8.4 MiB
被忽略：node_modules、.env、dist
高风险项：0
警告项：2
```

风险提醒必须明确列出可能误上传的内容，例如：

```text
- 配置文件中可能包含数据库地址或内部服务地址；
- .vscode/settings.json 可能包含本机绝对路径；
- dist/ 或 build/ 可能包含体积较大的构建产物；
- 测试数据可能包含真实用户信息；
- 图片、模型、压缩包可能显著增大仓库体积；
- 许可证不兼容的第三方代码可能被一并上传。
```

普通推送确认：

```text
确认将以上文件推送到 owner/repo 的 main 分支吗？请输入“确认推送”继续。
```

覆盖式或无关历史替换确认：

```text
远端 main 将被新快照替换。原 main 会先保存到 archive/main-<时间戳>。
请输入目标仓库名“repo”继续，其他输入均取消。
```

不能把“继续吗”这样的模糊回答当作高风险确认。

---

## 15. 第十一步：保存远端原 `main`

只要远端 `main` 已存在，且本次属于“快照替换”或可能改写历史，就先创建历史分支。

分支命名：

```text
archive/main-YYYYMMDD-HHMMSS
```

示例：

```bash
git push origin refs/remotes/origin/main:refs/heads/archive/main-20260727-153012
```

验证：

```bash
git ls-remote --exit-code --heads origin archive/main-20260727-153012
```

只有验证成功后才能继续改写 `main`。

如果同名分支已经存在，增加短提交号：

```text
archive/main-20260727-153012-a1b2c3d
```

不得删除旧历史分支。

---

## 16. 第十二步：提交本地内容

先查看状态：

```bash
git status --short
```

添加：

```bash
git add --all
```

再次扫描已暂存文件：

```bash
git diff --cached --name-status
git diff --cached --check
```

若暂存后出现敏感文件，执行：

```bash
git restore --staged <FILE>
```

生成提交信息。首次发布默认：

```text
chore: initial submission
```

后续快照发布默认：

```text
chore: publish snapshot YYYY-MM-DD HH:MM
```

普通更新应根据变更内容生成简短、准确的提交信息。

提交：

```bash
git commit -m "<MESSAGE>"
```

若没有变更，不创建空提交，直接说明当前工作区无新内容。

---

## 17. 第十三步：推送到正式 `main`

### 17.1 首次推送

```bash
git push -u origin HEAD:main
```

### 17.2 快进更新

```bash
git push origin HEAD:main
```

### 17.3 远端领先

默认停止，提供两种选择：

1. 拉取并整合远端历史；
2. 快照替换，先备份远端 `main`，再使用 `force-with-lease`。

不得自动选择第二种。

### 17.4 分叉或无关历史，用户确认快照替换

前置条件：

- 已记录 `<EXPECTED_OID>`；
- 历史分支已创建并验证；
- 用户已输入仓库名完成高风险确认。

执行：

```bash
git push --force-with-lease=refs/heads/main:<EXPECTED_OID> origin HEAD:main
```

如果 lease 失败，说明远端在确认后发生变化。必须重新 fetch、重新展示差异、重新确认，不能直接重试强推。

---

## 18. 后续再次上传同一文件夹

每次运行先读取本地 `.git` 和 `origin`。默认提供两种模式：

### 模式 A：普通更新

适用于继续开发同一仓库：

- 保留 Git 历史；
- 只提交本次差异；
- 远端可快进时直接推送；
- 不改写 `main`。

### 模式 B：快照替换

适用于“把当前文件夹视为新的正式版本”：

1. fetch 远端 `main`；
2. 将原 `main` 保存到 `archive/main-<时间戳>`；
3. 验证历史分支；
4. 扫描当前文件夹；
5. 二次确认；
6. 将当前快照推送到正式 `main`；
7. 如需改写历史，只使用精确 OID 的 `force-with-lease`。

默认推荐模式 A。只有用户明确表达“替换正式版本”“以当前文件夹为准”“覆盖 main”时选择模式 B。

---

## 19. 推送后验证

读取本地提交：

```bash
git rev-parse HEAD
```

读取远端 `main`：

```bash
git ls-remote origin refs/heads/main
```

两者 OID 必须一致。

还应检查：

```bash
git status --short --branch
```

成功输出应包括：

- 仓库名称；
- 远端地址；
- `main` 提交短哈希；
- 提交信息；
- 历史备份分支（若创建）；
- 被忽略的敏感文件摘要；
- 下一次运行命令。

示例：

```text
上传完成。
仓库：owner/repo
分支：main
提交：a1b2c3d chore: publish snapshot 2026-07-27 15:30
历史备份：archive/main-20260727-153012
未上传：.env、node_modules/、dist/
```

---

## 20. 一键入口

本 Skill 附带脚本：

```bash
python scripts/synnovator_submit.py
```

首次使用可指定 SSH 地址：

```bash
python scripts/synnovator_submit.py --remote git@synnovator.com:<owner>/<repo>.git
```

普通更新：

```bash
python scripts/synnovator_submit.py --mode incremental
```

以当前文件夹替换正式版本：

```bash
python scripts/synnovator_submit.py --mode snapshot
```

脚本只负责本地 Git、SSH 检查、安全扫描、备份分支和推送。没有平台 API 配置时，仓库列表和仓库创建仍由 AI 工具通过网页或让用户提供 SSH 地址完成。

---

## 21. AI 对话规范

### 21.1 信息缺失时一次只问必要问题

推荐顺序：

1. “当前要上传哪个目录？”
2. “使用已有仓库，还是创建新仓库？”
3. “新仓库名称是什么？”
4. “设为 private 还是 public？”
5. “这是普通更新，还是用当前文件夹替换正式 main？”
6. “确认推送吗？”

不要一次抛出十几个问题。

### 21.2 已检测到的信息不要重复询问

例如：

- SSH 已认证，跳过绑定；
- 当前已有正确 `origin`，不再要求粘贴地址；
- Git 身份已配置，不再询问；
- 远端仓库为空，不再提示覆盖风险；
- `.env` 已忽略，不再要求用户手工删除。

### 21.3 操作前说明，操作后验证

每个写操作都遵守：

```text
说明将做什么 → 得到确认 → 执行 → 验证结果
```

### 21.4 错误信息必须可执行

不要只说“推送失败”。应说明类别并提供修复步骤，例如：

```text
SSH 认证失败：当前密钥未被平台接受。
请确认公钥已完整粘贴到 SSH 公钥页面，然后执行：
ssh -vT git@synnovator.com
```

---

## 22. 常见错误处理

### `git: command not found`

按操作系统安装 Git，完成后重新打开终端。

### `Permission denied (publickey)`

检查：

```bash
ssh-add -l
ssh -vT git@synnovator.com
```

确认 SSH config 的 `IdentityFile` 指向正确私钥，并重新绑定公钥。

### `Host key verification failed`

不要关闭验证。检查主机名和指纹后，重新建立可信记录。

### `Repository not found`

可能原因：

- SSH URL 错误；
- 仓库不存在；
- 当前账号无权限；
- 命名空间不正确。

重新从仓库页面复制 SSH 地址。

### `rejected non-fast-forward`

远端存在本地没有的提交。先 fetch 并比较，不要直接强推。

### `force-with-lease` 失败

远端在确认后被其他人更新。重新读取远端，重新生成备份分支和推送计划。

### 文件过大

移除文件、加入 `.gitignore`，或在平台支持时使用 Git LFS。

### 敏感文件已出现在历史提交

仅从当前提交删除不足以消除泄漏。立即停止推送，轮换相关凭证，并使用专门的历史清理工具处理。任何历史改写都必须重新确认。

---

## 23. 完成标准

只有同时满足以下条件才报告成功：

- `git --version` 可用；
- SSH 认证通过；
- `origin` 指向用户确认的仓库；
- 高危敏感文件为 0；
- 用户完成二次确认；
- 远端原 `main` 在必要时已保存并验证；
- 本地 `HEAD` 与远端 `main` OID 一致；
- 工作区状态已向用户说明；
- 输出了下一次一键更新命令。
