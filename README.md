# Synnovator Code Submit Skill

用于本地代码编辑器或 AI 编程工具的安全代码上传 Skill。

## 文件

- `SKILL.md`：完整工作流、对话规则和安全要求。
- `scripts/synnovator_submit.py`：本地交互式一键提交脚本。
- 中国大陆环境下，Git 安装引导优先提供清华大学 TUNA 镜像，并要求按系统版本匹配和备份原软件源。

## 快速使用

方式一：
```bash
请AI访问https://github.com/Onesyn-ai/synnovator-code-submit-skill.git并安装synnovator-code-submit-skill并使用
```

方式二：
在项目根目录执行：

```bash
python /path/to/synnovator-code-submit-skill/scripts/synnovator_submit.py
```

首次运行也可以直接提供仓库 SSH 地址：

```bash
python /path/to/synnovator-code-submit-skill/scripts/synnovator_submit.py \
  --remote git@synnovator.com:<owner>/<repo>.git
```

脚本不会调用未定义的平台 API。仓库创建或仓库列表在没有平台插件/API时，需要由用户在网页完成并提供 SSH 地址。
