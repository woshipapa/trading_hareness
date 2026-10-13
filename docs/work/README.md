# 工作笔记（n8n 行情研究平台：数据源、研究策略、控制台）

协议是 `dev-notes` 技能；本文件只写本项目特有的东西，与技能不一致时以本文件为准。内容用中文写，键名、状态、编号按
技能的英文拼写。目的：新来的 agent 不靠对话记录，读这里就能接着做。

## 布局

- `STATUS.md`、`STREAMS.md`：由笔记检查生成的视图。先读 `STATUS.md`。两个文件都不要手改。
- `streams/<编号>-<slug>.md`：每条工作线一份记录，编号 `W001` 起。
- `BACKLOG.md`：知道要做、现在没在做的事，编号 `B001` 起。
- `FACTS.md`：线上系统和数据的事实，每条带测量日期和探针，编号 `F001` 起。
- `plans/`：已批准的方案。草稿阶段在 `.claude/plan/`（被 git 忽略）；已移过来的五份在那里留了同名链接，其余没对账的旧方案仍是普通文件（见 BACKLOG 的 reconcile 条目）。
- `probes/`：探针脚本和它的输出，成对存放；输出来自仓库里已有脚本的，只存输出，命令写在 FACTS 的 `how` 里。

## Check settings

- stale_days: 3
- fresh_days: 14
- kinds: data-backfill, defect, follow-up, idea, reconcile
- language: zh

## 日期

笔记里的日期和时间一律是 **Asia/Shanghai**（交易日）。这台机器的时钟和 `git log` 显示的是美国太平洋时间，比上海
晚 15 小时：`git log` 里的 “10-12 20:17” 是上海 10 月 13 日 11:17。会话开头系统给的“今天日期”也是太平洋时间。
要当前上海时间：`TZ=Asia/Shanghai date`。

## 命令

- 笔记检查：`TZ=Asia/Shanghai python3 ~/.claude/skills/dev-notes/scripts/check_work.py docs/work`
  （加 `--write` 重新生成视图，`--ids` 看下一个可用编号；Codex 用 `~/.agents/skills/dev-notes/` 下的同一脚本）。
- 会话开始时对照现实：
  - `git log --oneline <记录里的 head>..HEAD` 和 `git status --short`（主检出里常有别的会话没提交的文件）；
  - `bash ~/codebase/agent-skills/own/codex-flow/scripts/lsworktrees.sh .`（Codex 工作区和分支的状态）；
  - `scripts/release status`，或带 SHA 的 `scripts/release-sync-status.sh --owner-source-sha <sha> --edge-source-sha <sha>`
    （只读；两台主机实际在跑什么）。
- 只读查 owner 库的写法（探针都照这个跑；四个 SSH 变量只在内存里，不打印、不落盘；脚本里先 `SET LOCAL statement_timeout`，只用 SELECT；全市场快照的正文很大，要什么字段就在 SQL 里取什么）：
  ```bash
  eval "$(grep -E '^LONGHU_SSH_(HOST|PORT|USER|KEY_PATH)=' config/secrets/.env.local)" && \
  ssh -o BatchMode=yes -o ConnectTimeout=15 -p "$LONGHU_SSH_PORT" -i "$LONGHU_SSH_KEY_PATH" "$LONGHU_SSH_USER@$LONGHU_SSH_HOST" \
    "docker exec -i trading-hareness-peer-quant-research-1 sh -c 'd=/app/hotfix/current; [ -f \$d/app/main.py ] || d=/app; cd \$d && PYTHONPATH=\$d python -'" \
    < docs/work/probes/<名字>.py > docs/work/probes/<名字>.out.txt 2>&1
  ```
- 合并前的检查：根目录 `AGENTS.md` “Agent workflow” 第 4 条和 “Before commit” 一段列的命令。从工作区里跑发布路径
  测试要写成 `COMPOSE_ENV_FILES=/Users/papa/codebase/n8n/.env make -C quant-service test-release-path`。
- 发布前后：`docs/RELEASE_SYNC_47.md`，先 `scripts/release plan`；发布后把结果记进该文件末尾的发布表。

## 只有用户能做的

agent 把命令备好，用户执行，记录里记下结果：

- 生产（owner）发布的 `--apply`、`git push`、往 owner 库写数据的脚本、同步凭据。自动模式会拦截这些，不要绕过。
- 命令要给成一条短的包装脚本（`! bash <路径> --apply`）。长命令粘贴时曾在换行处被拆开：推送执行了，发布没执行。
- 对授权数据网关的大批量读取（上万次，例如个股历史分钟线）要先得到明确同意。几千次以内的研究取数要事先告诉用户。两种都不在交易时段（09:15–15:00）跑。
- 发布窗口见 `config/release-windows.json`：08:30–11:30、13:00–15:10、18:45–22:05 不能发布 owner（边界时刻也算不能）。能发的时段是 11:30–13:00、15:10–18:45 和 22:05 之后。owner 发布要求提交已在 origin/main，推送也由用户做。

## 持有者

记录的 `holder` 写的是会话。新会话无法知道旧会话是否还在：用户说换了新会话、或旧会话的日期已超过 3 天，新会话可以直接接手并把 `holder` 改成自己；否则先问用户。一个会话可以同时持有几条记录。主会话派出的子 agent 接手某条记录时，`holder` 写“会话 X 派出的 agent”，交回时改回会话 X，并在 Log 里记一条交回。

## 待办的 `ready`

`ready` 指现在就能开工：做法写清了、不缺前置、不需要用户先做决定（写库那一步仍由用户执行）。还要先定方向或选数据源的是 `open`。

## 其他登记处

- 决策：`docs/decisions/`。发布记录：`docs/RELEASE_SYNC_47.md` 末尾的表。报告和调研：`docs/archive/`。
- 架构与归属：`docs/ARCHITECTURE.md`、`config/components.json`、各组件目录的 `AGENTS.md`。
- 研究的工作文件在 `state/research/`（被 git 忽略，只在这台 Mac 上）；记录引用它时要写明“本机”。
- 多 agent 流程（方案、审查、工作区、评审、回收）：`codex-flow` 技能。

## 每个会话都成立的注意事项

根目录 `AGENTS.md` 已有的不重复。这里只列它没写的：

- 发布类型由改动路径决定（`scripts/shared-peer/classify-owner-paths.sh`）：`quant-service/app/*` 走纯代码发布；`docs/*`、`*.md`、测试、`frontend/*`、`feishu-relay/*` 不算 owner 运行时；`scripts/` 下任何非测试文件的新增或改动（例如改 `scripts/tdx-promote.py`、往 `scripts/data/` 加文件）、迁移、依赖和其他没列出的路径会判成全量发布。笔记因此全在 `docs/work/`。前端和中继各有自己的发布脚本（根目录 `AGENTS.md` 的组件表）。
- Codex 的工作区里没有被 git 忽略的文件：给它的路径要用绝对路径，方案用 `docs/work/plans/` 下的。
- 发布路径测试是 `python -m unittest discover`：测试必须是 `unittest.TestCase`，放在 `quant-service/tests/test_*.py`。
- 一切研究结论都是证据，`live_effect: none`；不要把话说成买卖建议。
- 几个会话同时用这套笔记（W009 就是另一个会话的）。提交时只 `git add` 自己的记录、自己加的探针和两个视图，不要 add 整个目录：2026-10-13 曾把另一个会话的记录带进了提交。
- 新编号在动笔前用 `--ids` 取；提交前再跑一次检查，和别的会话撞号就改自己的（2026-10-13 的 B030 撞过）。
- 跑检查时不要把输出接到 `tail` 之类的管道后面再用 `&&` 连提交：管道会吞掉失败的退出码，2026-10-13 因此带着一个错误提交过一次。先单独跑检查，看到 `0 errors` 再提交。
- 视图由哪个会话生成都一样，会包含别的会话还没提交的记录状态，这是正常的。
