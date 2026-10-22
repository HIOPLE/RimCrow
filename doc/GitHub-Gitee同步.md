# GitHub → Gitee 自动同步

工作流将 `Inky-Feather/RimCrow` 单向同步到 `InkyXFeather/RimCrow`，支持手动试运行和按条件自动执行。不构建安装包，不修改 GitHub 仓库或 Release。

## 1. 首次配置

将以下文件通过正常开发流程合入 GitHub 默认分支（当前为 `main`），并确保自动同步所针对的 `main`、`dev` 分支也包含该工作流：

- `.github/workflows/sync-gitee.yml`
- `scripts/sync_gitee.py`
- `.gitignore` 中允许跟踪 `.github/workflows/` 的规则
- 对应测试和本说明

工作流必须存在于默认分支，GitHub 才会显示手动运行入口。自动触发只监听 Release 发布事件，并始终从默认分支读取脚本。

在 Gitee 的个人访问令牌设置中创建令牌，授予目标仓库代码推送和发行版管理所需的权限（通常包含 `projects`，以实际设置页面为准）。账号需要有目标仓库写入权限；同名分支、标签的强制更新还受 Gitee 保护规则限制。

在 GitHub 仓库中进入 **Settings → Secrets and variables → Actions → Secrets**，添加：

| Secret | 内容 |
| --- | --- |
| `GITEE_TOKEN` | Gitee 个人访问令牌 |

GitHub 读取权限由工作流自己的 `GITHUB_TOKEN` 提供，无需额外创建 GitHub Token。令牌不写进仓库文件、Git URL 或聊天记录。

**强制同步会覆盖 Gitee 同名分支和 Tag 的独立提交。Gitee 应作为只读镜像使用；首次正式执行前，先备份其独立开发内容。** 本工作流不删除 Gitee 独有的分支、Tag 或历史发行版，不同步 Issue、PR、Wiki、Git LFS 对象，也不复制子模块仓库本身。

## 2. 手动测试

进入 **Actions → Sync GitHub to Gitee → Run workflow**，选择：

| 输入 | 行为 |
| --- | --- |
| `sync_code = true` | 在 Release 同步之外，强制同步全部 GitHub 分支和 Tag |
| `sync_code = false` | 只同步 Release、标题、说明和符合规则的附件 |
| `release_tag` | 例如 `v0.24.12`；留空选择 GitHub 最新正式版，不批量补齐历史版本 |
| `dry_run = true` | 默认值。读取两端信息、检查包、计算 SHA256，并执行 Git 推送预览；不写入 Gitee |
| `dry_run = false` | 真正更新 Gitee |

建议先执行 `sync_code = false + dry_run`，确认发行版附件筛选正确；需要镜像代码时再打开 `sync_code` 试运行。确认后将 `dry_run` 取消勾选，执行正式同步。

试运行仍会下载选中的附件及 Gitee 已有同名附件，因此会使用流量和运行时间。它能验证读取、包筛选和 Git 推送预览，但不能证明正式上传一定成功。以非试运行任务结束成功和 Gitee 实际页面为准。

## 3. 自动触发开关

进入同一设置页的 **Variables**，按需添加仓库变量。未配置或值不是小写 `true` 时，自动执行关闭；手动运行不受开关影响。

| Variable | 开启值 | 条件和行为 |
| --- | --- | --- |
| `GITEE_AUTO_RELEASE_SYNC` | `true` | 发布正式版 Release 时自动同步该发行版；跳过预发布版 |
| `GITEE_SYNC_CODE` | `true` | 在手动或自动触发时额外强制同步全部分支和 Tag；未配置或不是小写 `true` 时关闭 |

手动和自动只负责触发；代码是否同步由 `sync_code`（手动输入）或 `GITEE_SYNC_CODE`（仓库变量）控制，任一开启即可。自动 Release 运行时没有手动输入，因此只看 `GITEE_SYNC_CODE`。正式发行版 Tag 必须是 `v数字.数字.数字`，如 `v0.24.12`；不符合格式会失败并显示原因。

请先创建 Release 草稿、上传全部附件，再发布。发布后才补传附件或修改说明，不会触发本工作流，需要手动填写该 Tag 补同步。手动重跑会更新对应发布说明。

如果未来由另一条 Actions 使用默认 `GITHUB_TOKEN` 自动创建 Release，通常不会连锁触发本工作流；应在构建发布流程中显式调用同步脚本。不要假设发布事件必然再次触发。

手动和自动触发共用一个并发锁，不会同时修改镜像。GitHub 默认并发机制最多保留一个等待任务，连续发布多版后应检查各版本的运行结果，必要时手动按 Tag 补同步。

## 4. 附件规则

默认只接受当前 Tag 对应的文件，至少需要一个 ZIP 安装包：

```text
RimCrow-{tag}-windows.zip
RimCrow-{tag}-macos.zip
RimCrow-{tag}-linux.zip
SHA256SUMS.txt
```

例如 Tag 为 `v0.24.12` 时，Windows 包名为 `RimCrow-v0.24.12-windows.zip`。只发布一个平台也可以；测试包、GitHub 自动生成的源码下载和其他附件不会同步。

可选仓库变量：

| Variable | 默认值与含义 |
| --- | --- |
| `GITEE_ASSET_PATTERNS` | 留空使用上述规则；自定义时每行一个文件名或 glob 表达式，支持 `{tag}` 占位符，并整体替代默认规则 |
| `GITEE_MAX_ASSET_BYTES` | `100000000`；单文件最大字节数，必须是正整数 |

不要直接配置 `*.zip`，否则测试包也会匹配。提高大小阈值不会提高 Gitee 账号的真实配额。Gitee [官方帮助](https://help.gitee.com/repository/release/create)列出的普通项目限制为单附件 100M、仓库附件总计 1G；特殊项目额度和账号实际限制应另行确认。超出限制需要更换存储方案，本工作流不会自动删除历史包腾空间。

## 5. 重试和失败处理

- 使用同一 Tag 创建或更新发行版；附件缺失则补传，同名且 SHA256 一致则跳过，同名内容不同则失败，不覆盖或删除旧附件。
- 所有选中附件先下载校验，再开始写入。上传后再从 Gitee 下载并验证 SHA256，公开下载失败也会视为失败。
- API 只有明确的 404 被视为发行版不存在；认证错误、限流等不会被误当作需要创建新版本。
- 写入超时不自动重复提交；在 Actions 中重跑任务或手动指定同一 Tag，重新检查目标状态后续传。
- 跨 Git 和两个平台的发布不是原子事务。中途失败时可能已经同步代码、创建发行版或上传部分附件；不会自动回滚其他成功步骤，重跑时会补齐缺失项。
- Git 命令超时为 5 分钟，API 连接/读超时分别为 15/120 秒，整条任务最多 30 分钟。保护分支拒绝强推、空间不足、网络故障都会让任务失败。

本工作流不做双向同步、不自动删除旧版本、不替你修改分支保护规则。
