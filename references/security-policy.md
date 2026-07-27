# 提交安全策略补充

## 必须排除的典型内容

- 运行环境文件：`.env`、`.env.local`、`.env.production`。
- 云和集群凭据：`.aws/`、`.kube/`、service account JSON。
- SSH 私钥和证书容器：`id_*` 私钥、PEM、P12、PFX、JKS。
- 包管理器认证：`.npmrc`、`.pypirc`、`.netrc`。
- 本地数据库与导出：SQLite 文件、数据库 dump、用户数据导出。
- 依赖和构建产物：`node_modules/`、虚拟环境、`dist/`、`build/`、`target/`。
- 日志、崩溃转储、缓存和编辑器本地状态。

## 可以提交的模板

模板必须只包含占位符，不得包含可用凭据：

```dotenv
# .env.example
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB_NAME
API_KEY=replace_me
```

## public 仓库额外检查

- 源码中的内网域名、IP、数据库结构和测试账户。
- 许可证和第三方代码再分发权限。
- 题目要求不得公开的测试数据、答案、模型权重或赛事材料。
- 个人信息、聊天记录、用户上传文件和访问日志。

## 失败分级

- `FAILED`：未发生任何远端写入。
- `PARTIAL`：历史备份分支已创建，但 main 未更新；或 main 已更新但校验失败。
- `SUCCESS`：远端 main 的哈希已验证等于本地 HEAD。
