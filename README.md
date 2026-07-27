# Synnovator Code Submit Skill

用于本地代码编辑器或 AI 编程工具的安全代码上传 Skill。它仍然是一个工具，但把访问检查、SSH 绑定和提交推送拆成独立阶段，避免在推送过程中静默生成密钥或修改认证配置。

## 文件

- `SKILL.md`：完整工作流、阶段边界、对话规则和安全要求。
- `scripts/synnovator_submit.py`：本地交互式工具。
- 中国大陆环境下，Git 安装引导优先提供清华大学 TUNA 镜像，并要求按系统版本匹配和备份原软件源。

## 四个入口

### 1. 只读检查账号和仓库

```bash
python scripts/synnovator_submit.py check \
  --remote git@synnovator.com:<owner>/<repo>.git
```

只执行 SSH 账号检查和 `git ls-remote` 仓库读取验证，不生成密钥、不修改项目、不提交、不推送。

### 2. 单独绑定 SSH 公钥

```bash
python scripts/synnovator_submit.py bind
```

只处理密钥检查、生成、公钥读取、复制和绑定验证。账号已经可访问时自动跳过。

绑定时工具会读取真实的 `.pub` 文件，在终端中完整显示可复制的一行公钥，并尝试复制到系统剪贴板。`SHA256:...` 只作为核验指纹显示，不能粘贴到平台的“密钥内容”输入框。

### 3. 单独提交推送

```bash
python scripts/synnovator_submit.py push \
  --remote git@synnovator.com:<owner>/<repo>.git \
  --mode incremental
```

`push` 会重新执行只读访问门禁。认证失败时停止并要求单独运行 `bind`，不会自动绑定。

若当前文件夹已经是带 `origin` 的 Git 仓库，工具会显示仓库根目录、远端、当前分支、跟踪分支和最近提交，并要求输入 `继续同步` 后才会使用该仓库。

快照替换并备份远端原 `main`：

```bash
python scripts/synnovator_submit.py push --mode snapshot
```

### 4. 同一工具一键编排

```bash
python scripts/synnovator_submit.py run
```

不写子命令时也默认进入 `run`。执行顺序为：只读检查 → 认证失败时经用户确认进入独立绑定阶段 → 再次检查 → 独立推送阶段。

## 平台能力限制

脚本不调用未定义的平台 API。没有平台插件、已登录浏览器自动化或明确 API 时，它不能枚举账号下的全部仓库，也不能直接创建远端仓库。此时需要用户在网页选择或创建仓库，并提供平台实际显示的 SSH 克隆地址。
