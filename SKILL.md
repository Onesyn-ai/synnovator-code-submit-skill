---
name: synnovator-code-submit
version: 1.2.0
description: 在一个本地 Skill 工具中，将账号访问检查、SSH 公钥绑定、仓库选择和安全提交推送拆分为独立阶段，并将当前项目发布到 Synnovator 类代码托管平台的 main 分支。
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

## 4. 总体流程与阶段边界

本 Skill 仍然是一个工具，但内部必须拆成三个相互隔离的阶段：

### 阶段 A：访问检查 `check`

只执行只读操作：

1. 检查 Git 和 SSH 命令；
2. 强制测试是否能访问平台账号；
3. 尝试读取已绑定账号的仓库列表；
4. 对选定或已知仓库执行只读 `git ls-remote`；
5. 输出访问结果。

本阶段禁止生成密钥、修改 `.git/config`、初始化仓库、提交或推送。

### 阶段 B：SSH 绑定 `bind`

仅当账号 SSH 认证未通过时进入：

1. 检查并复用现有密钥；
2. 必要时生成专用密钥；
3. 读取并复制公钥；
4. 指导用户在 SSH 公钥页面绑定；
5. 再次验证账号访问。

本阶段禁止选择仓库、修改 `origin`、扫描项目、提交或推送。若账号访问已经通过，直接结束并跳过重复绑定。

### 阶段 C：提交推送 `push`

只处理项目与仓库：

1. 确定项目根目录；
2. 检测当前文件夹是否为已 clone 或已配置远端的仓库；
3. 即使已存在 `origin`，也必须询问是否继续同步到该仓库；
4. 选择已有仓库，或确认名称和 `private` / `public` 后创建新仓库；
5. 重新执行账号和目标仓库的只读访问门禁；
6. 初始化或检查本地 Git 仓库；
7. 更新 `.gitignore` 并扫描敏感文件、风险目录和大文件；
8. 获取远端 `main` 状态并生成推送计划；
9. 展示风险并进行二次确认；
10. 必要时保存远端原 `main` 到历史分支；
11. 提交并推送到正式 `main`；
12. 验证远端提交。

`push` 阶段认证失败时必须立即停止，并提示用户单独执行 `bind`；不得在推送流程中静默生成或绑定密钥。

### 一键编排 `run`

`run` 仍属于同一个 Skill 工具，其作用只是按以下顺序调用独立阶段：

```text
check → 认证失败时询问并进入 bind → 再次 check → push
```

阶段之间必须输出清晰边界。任何一步失败都停止后续写操作，并给出明确修复命令。

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

## 8. 第四步：访问检查与 SSH 绑定完全分离

### 8.1 访问检查阶段必须先执行

先测试账号 SSH 访问：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@synnovator.com
```

判断规则：

- 输出明确表示认证成功时，即使命令退出码非 0，也可视为账号访问通过；
- `Permission denied (publickey)` 表示账号绑定或本机密钥选择存在问题；
- DNS、超时、拒绝连接和主机不可达属于网络问题，不得误判为“没有绑定”；
- 首次连接出现主机指纹确认时，必须让用户核对指纹，不能关闭主机校验。

账号访问通过后，必须继续检查仓库读取能力。读取仓库列表按以下优先级：

1. 代码编辑 AI 工具已连接的平台插件；
2. 已登录的平台浏览器会话；
3. 平台明确提供并已配置的 API；
4. 用户提供任意一个有权限仓库的 SSH 地址，执行只读验证。

指定仓库的只读验证：

```bash
git ls-remote --heads <SSH_URL>
```

空仓库没有分支输出也可以是成功，必须以命令退出状态判断。不得使用 `git clone` 作为账号检查，因为 clone 会创建本地文件。

命令行脚本在没有平台 API 时不能枚举整个账号的仓库列表，此时必须明确说明限制，并要求提供一个仓库 SSH 地址进行读取验证。不得伪造仓库列表。

### 8.2 访问失败后的分流

- **账号 SSH 认证失败**：结束访问检查，询问是否进入独立的绑定阶段；
- **账号认证成功但仓库读取失败**：不要重新生成密钥，优先检查仓库地址、账号权限、仓库是否存在；
- **网络失败**：先处理网络、DNS、代理或 SSH 端口；
- **仓库列表无法枚举但账号认证成功**：允许进入仓库选择或新建仓库流程，但目标仓库确定后必须再次执行 `git ls-remote`。

### 8.3 独立 SSH 绑定阶段

绑定阶段不得初始化项目仓库，也不得执行 `git add`、`git commit`、`git remote set-url` 或 `git push`。

先检查现有密钥：

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

不得覆盖已有私钥。若账号访问已经通过，立即结束绑定阶段，不重复生成或上传公钥。

### 8.4 生成专用 SSH 密钥

优先 Ed25519：

```bash
ssh-keygen -t ed25519 -C "<EMAIL>" -f ~/.ssh/id_ed25519_synnovator
```

仅在系统不支持 Ed25519 时使用：

```bash
ssh-keygen -t rsa -b 4096 -C "<EMAIL>" -f ~/.ssh/id_rsa_synnovator
```

建议用户为私钥设置口令。不得把私钥口令写入脚本、配置或日志。

### 8.5 配置 SSH 使用专用密钥

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

### 8.6 读取、验证并完整展示公钥

只允许读取带 `.pub` 后缀的文件：

```bash
cat ~/.ssh/id_ed25519_synnovator.pub
```

Windows PowerShell：

```powershell
Get-Content -Raw $HOME\.ssh\id_ed25519_synnovator.pub
```

读取后必须先验证：

- 文件路径以 `.pub` 结尾；
- 内容只有一条非空行；
- 不包含 `PRIVATE KEY`；
- 以 `ssh-ed25519`、`ssh-rsa`、`ecdsa-sha2-`、`sk-ecdsa-sha2-` 或 `sk-ssh-ed25519@openssh.com` 开头；
- 包含非空的密钥主体。

验证失败时立即停止，不得猜测、截断或改写公钥。只能展示公钥，禁止展示私钥。

工具应尝试把完整公钥复制到系统剪贴板，但剪贴板只是辅助方式。无论复制是否成功，都必须在终端中完整展示公钥，推荐格式：

```text
========================================================================
SSH 公钥（请复制完整一行）
========================================================================
只复制两条边界线之间的公钥内容，不要复制边界线：
----- SSH PUBLIC KEY BEGIN -----
ssh-ed25519 AAAA... 用户备注
----- SSH PUBLIC KEY END -------

公钥文件：<实际 .pub 文件路径>

仅供核验的公钥指纹（不要粘贴到“密钥内容”输入框）：
SHA256:<指纹>
========================================================================
```

上例中的 `AAAA...` 只用于说明格式。实际运行时必须输出 `.pub` 文件中的完整原文，不得使用省略号、打码、折叠或只输出指纹。

公钥指纹必须放在完整公钥之后，并明确标注“仅供核验、不要粘贴”。`SHA256:...` 不能代替公钥。

### 8.7 指导用户绑定并验证

告诉用户：

1. 打开 `https://www.synnovator.com/user/settings/keys`；
2. 在“密钥名称”中填写可识别名称，例如 `比赛电脑-2026`；
3. 在“密钥内容”中粘贴终端中显示的完整一行公钥；
4. 只复制公钥本身，不复制 `BEGIN/END` 边界线；
5. 确认粘贴内容以 `ssh-ed25519`、`ssh-rsa` 等密钥类型开头，而不是以 `SHA256:` 开头；
6. 点击“增加密钥”；
7. 回到终端继续验证。

再次执行：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@synnovator.com
```

验证通过后，绑定阶段结束。此时仍然不得自动提交或推送，必须显式进入 `push` 阶段。

---

## 9. 第五步：选择已有仓库或创建新仓库

### 9.1 检测当前文件夹是否已有 clone/远端

提交推送阶段开始时执行：

```bash
git rev-parse --is-inside-work-tree
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git rev-parse --abbrev-ref --symbolic-full-name @{upstream}
git log -1 --oneline
```

Git 无法可靠区分“由 clone 创建”和“本地 init 后添加 remote”，因此只要当前文件夹是 Git 仓库且存在远端，就按“已有 clone/已配置仓库”处理。

显示以下信息：

- 仓库根目录；
- `origin` 的 fetch 和 push 地址；
- 当前分支；
- 跟踪分支；
- 最近一次提交。

即使 `origin` 指向 Synnovator，也不得直接使用。必须询问：

```text
是否继续使用这个已 clone/已配置的仓库同步推送？
请输入“继续同步”确认。
```

用户确认后才能继续使用该远端。用户拒绝时，进入仓库列表选择或新建仓库流程，不得静默替换 `origin`。

### 9.2 读取已绑定用户的仓库

按以下优先级：

1. 当前 AI 工具已连接的平台插件；
2. 当前 AI 工具的浏览器自动化，且用户已登录；
3. 平台明确提供并已配置的 API；
4. 本地保存的最近仓库记录；
5. 让用户粘贴仓库 SSH 克隆地址。

展示仓库时至少包含：

- 仓库名；
- 命名空间或所有者；
- `private` / `public`；
- 默认分支；
- SSH 地址；
- 最近更新时间（若可得）。

用户选择仓库后，必须先执行只读检查：

```bash
git ls-remote --heads <SSH_URL>
```

读取失败时不得进入扫描、提交和推送。

### 9.3 用户不满意时创建仓库

依次询问：

1. 仓库名称；
2. 可选描述；
3. 可见性：`private` 或 `public`；
4. 是否确认创建。

新建仓库默认建议 `private`。仓库名建议只使用：

```text
[a-zA-Z0-9._-]
```

建议不初始化 README、`.gitignore` 和 License，默认分支为 `main`，避免首次推送出现无关历史。

若无平台 API 或浏览器自动化，指导用户在网页中新建仓库，然后粘贴平台实际显示的 SSH 地址。不能猜测仓库创建接口。

### 9.4 推送阶段的强制访问门禁

目标仓库确定后，`push` 阶段必须重新执行：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=10 -T git@synnovator.com
git ls-remote --heads <SSH_URL>
```

该门禁只读。若认证失败，`push` 必须停止并提示单独运行 `bind`，不能在推送阶段自动绑定。若仓库读取失败但账号认证成功，应检查仓库地址或权限，不要盲目重建密钥。

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

## 20. 同一个 Skill 工具的分阶段入口

工具仍然只有一个脚本，但提供四个入口。

### 20.1 只读访问检查

```bash
python scripts/synnovator_submit.py check \
  --remote git@synnovator.com:<owner>/<repo>.git
```

只检查账号访问和仓库读取，不生成密钥、不修改项目、不提交、不推送。

### 20.2 单独绑定 SSH

```bash
python scripts/synnovator_submit.py bind
```

只生成/读取公钥并指导绑定，不选择仓库、不提交、不推送。

### 20.3 单独提交推送

```bash
python scripts/synnovator_submit.py push \
  --remote git@synnovator.com:<owner>/<repo>.git \
  --mode incremental
```

`push` 会重新进行只读访问门禁，但认证失败时只停止并提示运行 `bind`，不会自动进入绑定。

快照替换：

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

### 20.4 一键编排

```bash
python scripts/synnovator_submit.py run
```

不写子命令时默认等同于 `run`。它仍然是一个 Skill 工具，只是按阶段调用 `check`、必要时 `bind`、再 `push`。

没有平台 API 配置时，仓库列表和仓库创建仍由 AI 工具通过已登录网页完成，或让用户提供平台实际显示的 SSH 地址。

---

## 21. AI 对话规范

### 21.1 信息缺失时一次只问必要问题

推荐顺序：

1. “当前要上传哪个目录？”
2. “检测到当前文件夹已有 origin，是否继续同步这个仓库？”
3. “使用账号下哪个已有仓库，还是创建新仓库？”
4. “新仓库名称是什么，设为 private 还是 public？”
5. “这是普通更新，还是用当前文件夹替换正式 main？”
6. “确认推送吗？”

不要一次抛出十几个问题。

### 21.2 已检测到的信息不要重复询问

例如：

- SSH 已认证，跳过绑定；
- 当前已有 `origin`，仍必须确认是否继续同步，但确认后不再要求重复粘贴地址；
- Git 身份已配置，不再询问；
- 远端仓库为空，不再提示覆盖风险；
- `.env` 已忽略，不再要求用户手工删除。

### 21.3 AI 必须在回复中再次展示完整公钥

当绑定阶段读取到真实 `.pub` 文件内容后，代码编辑 AI 必须把该完整公钥再次放入面向用户的回复中。即使终端提示“已复制到剪贴板”，也不得省略，因为沙箱、远程终端或子进程剪贴板可能不是用户当前系统剪贴板。

展示要求：

- 使用独立的 `text` 代码块；
- 内容必须来自本次实际读取的 `.pub` 文件；
- 保持完整单行，不得出现 `...`、打码或截断；
- 代码块前写明“请复制下面的完整一行”；
- 代码块后再提供绑定页面和操作步骤；
- 可以显示 `SHA256:` 指纹，但必须位于完整公钥之后，并标注“仅供核验，不要粘贴”；
- 绝不显示不带 `.pub` 后缀的私钥内容。

若 AI 工具无法从终端结果中取得完整公钥，不得声称已经展示或复制成功。应让用户在本机执行以下命令并把输出作为待复制内容：

```powershell
Get-Content -Raw $HOME\.ssh\id_ed25519_synnovator.pub
```

或：

```bash
cat ~/.ssh/id_ed25519_synnovator.pub
```

### 21.4 操作前说明，操作后验证

每个写操作都遵守：

```text
说明将做什么 → 得到确认 → 执行 → 验证结果
```

### 21.5 错误信息必须可执行

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
- SSH 账号认证通过；
- 目标仓库的只读 `git ls-remote` 验证通过；
- 若当前文件夹原本已有远端，用户已明确输入“继续同步”确认使用，或已明确选择其他仓库；
- `origin` 指向用户确认的仓库；
- 高危敏感文件为 0；
- 用户完成二次确认；
- 远端原 `main` 在必要时已保存并验证；
- 本地 `HEAD` 与远端 `main` OID 一致；
- 工作区状态已向用户说明；
- 输出了下一次一键更新命令。
