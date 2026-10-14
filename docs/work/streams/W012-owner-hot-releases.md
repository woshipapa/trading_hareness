# W012 owner 只用一套镜像，其余改动全部热部署

- state: building
- holder: Claude Code 会话 4a420907（新会话，接手 93ba6e1d），2026-10-14
- goal: owner 的量化服务只保留一个镜像，只有镜像自己的输入变了（依赖、Dockerfile）才重建；其余改动，包括现在要走“全量发布”的 compose、在主机上执行的脚本、迁移文件和分类规则不认识的路径，都在现有镜像上不构建地发布
- plan: docs/work/plans/owner-hot-releases.md
- branch: main
- head: 556a9a2c
- next: A 段已合进本地 main（86831925），随 W006 的发布一起到 owner（见 W006 的 Waiting on the user）。接着：(1) owner 主机恢复后（BACKLOG B058），发布之前只读核对主机上 `scripts/backfill-full-market-daily.sh` 和 `scripts/fill-longhu-sector-membership.py` 与目标提交的是否相同（`sha256sum ~/trading_hareness/scripts/<名字>` 对 `git show <sha>:scripts/<名字> | shasum -a 256`）：这两个脚本的目录登记在本线改成了 `47owner`，分类会把它们报成 `full` 而拒绝纯代码发布。相同就照 `state/rel07.sh` 里已写好的 `RELEASE_RELABELLED_CURRENT` 放行；不同就告诉用户（要么手工把这两个文件拷上主机，要么做一次全量发布，后者要在 894 MB 的主机上构建镜像），并把 `rel07.sh` 里那一行去掉。(2) 第一次用新规则的发布之后，把检查模式和发布的输出存进 `docs/work/probes/`，状态才改 `released`。(3) 回收工作区 `/Users/papa/codebase/.codex-wt/n8n-owner-hot-a`。(4) 之后才是 B 段（Steps 里的 1–4），先为它建分支。
- updated: 2026-10-14

## Requirements

- 2026-10-13 「对于后续owner侧的这个内存问题，你看看能否统一都用一套镜像，以后都是代码上的hotfix啊」
- 2026-10-13 「这里的发布规则更改，保证以后除了非常必要的需要重新构建其他全是热部署。可以搞一个按你的设计去压缩的」（主会话的理解：照此前说的三步做一个精简的方案；“非常必要”指镜像自己的输入变了）

## Plan

`docs/work/plans/owner-hot-releases.md`。要点：分类从三种（运行时、跳过、全量）改成五种——运行时（源码覆盖，照旧）、迁移（照旧随覆盖带上，但只有 owner 库已经在目标版本才放行）、目录（compose、主机上执行的脚本、不认识的路径：给主机建一棵硬链接的新发布目录、原子切换、用现有镜像重建容器，不构建）、镜像（`requirements.txt`、两个 Dockerfile、隧道入口脚本：热路径拒绝）、跳过。不认识的路径默认从“全量”改成“目录”，并用一条测试从 Dockerfile 的 COPY 行推出镜像的输入，防止新加的 COPY 漏进“目录”。全量发布脚本保留为唯一会构建的路径，构建前加内存检查。

## Steps

- [x] 方案审计（Codex，只读，两轮；第 2 轮的 11 条留给 B 段，见 Log）— 第 1 轮 11 条异议全部接受，方案改成第 2 版（2026-10-13 23:17）；第 2 轮在跑
- [x] A 段（方案第 3 版）：分类的六个种类、`schema-gate.sh`、三处使用方、测试、文档 — a3783f22、3288e1ed（分支 `codex/owner-hot-a`，工作区 `/Users/papa/codebase/.codex-wt/n8n-owner-hot-a`，基点 39bcc7ee）— `python -m pytest scripts -q` → `1 failed, 371 passed, 3 skipped`（失败的是 B035 那一个）；`node scripts/shared-peer/deploy-code-only.test.mjs` 退出 0；`node scripts/release-sync-status.test.mjs` 退出 0
- [x] A 段独立评审并修正 — 评审 11 条（`docs/work/probes/2026-10-14-classifier-review.md`），修正 3598250f、09743272 — `python -m pytest scripts -q` → `1 failed, 379 passed, 3 skipped`（失败的是 B035 那一个）；`node scripts/shared-peer/deploy-code-only.test.mjs` 退出 0；`node scripts/release-sync-status.test.mjs` 退出 0；`generate_scripts_catalog.py --check` → `scripts catalog is current`；`verify_component_boundaries.py --check` 通过
- [x] A 段合并 — main 合进分支 919aa056（复跑：`python -m pytest scripts -q` → `1 failed, 379 passed, 3 skipped`，B035；两个 node 测试退出 0；目录检查 current），合回 main 86831925（`--no-ff`）
- [ ] （以下是 B 段）1 `switch-owner-tree.sh` 与测试、脚本目录规则（先不被任何东西调用）
- [ ] 2 `schema-gate.sh`：迁移版本检查抽成共用脚本，并加“发布后 peer 不会自己执行建表”的开关检查
- [ ] 3 分类的新种类与所有使用方一起落地（分类规则、`deploy-code-only.sh`、发布计划、命令行、镜像路径的内存检查）及相关测试
- [ ] 4 文档与决策记录（两个 `AGENTS.md`、五份文档、决策 0003 加注）
- [ ] 双评审、合并
- [ ] 第一次带“目录”改动的真实发布：核对并演练一次回滚（用户在场）

## Log

- 2026-10-13 22:34 开线。背景：新 owner 主机内存 894 MB（FACTS F022），全量发布要在主机上构建镜像；当天改两个手工脚本的默认地址就让下一次发布变成全量，只好撤回（提交 e8cb8dbf）。子代理把现有做法读了一遍（镜像只装了 `requirements.txt` 的依赖、`quant-service` 的源码和两个工具脚本；compose、守护脚本、systemd 单元都从主机上的发布目录读；源码覆盖的目录本身也在那棵发布目录下面），事实已摘进方案的 “What exists”。方案写成。

- 2026-10-13 23:17 审计第 1 轮 11 条，主会话逐条看过，全部接受（方案文件末尾有清单）。改动最大的三处：(1) 不认识的路径不再默认发到主机上，仍然拒绝，但拒绝的理由是“请给它加一条分类规则”而不是“去做全量发布”，“目录”一类改成明确的名单——仓库里有不该上主机的被跟踪文件（`config/secrets/set-local-secret.sh`、`scripts/data/` 下的证据文件）；(2) 回滚时把发布写过的环境变量也恢复，并用 `docker inspect` 证明容器挂的是新目录；(3) 迁移检查除了“库已到目标版本”，还要确认发布后的 compose 不会让 peer 自己执行建表。派工顺序也调了：先写切换脚本和检查脚本，再让分类的新种类和所有使用方一起落地；整个分支只合并一次。隧道镜像单列一类，任何发布都拒绝（现有流程本来也不会重建它们）。

- 2026-10-13 23:30 审计第 2 轮 11 条的要点：在主机上执行的脚本名单还不全（`configure-peer-self-tunnel.sh`、`export-peer-data.sh`、`verify-complete-stock-api.py`）；systemd 单元改了文件并不会生效，回滚也不恢复已安装的单元；解析 compose 需要 `yaml`，发布工具用的 `python3` 没有；`migrations` 目录里还有别的文件；wheelhouse 和基础镜像没有摘要可比；隧道镜像与镜像两类有重叠要定先后；SSH 断开后主机会停在半途，要有远端的事务日志和可重入的恢复；锁 1800 秒不续期可能不够；“渲染后的配置”在工作站一侧没有来源；`database_bootstrap.py` 不读那两个开关，说它被开关关住是错的；`schema-gate.sh` 也要脚本目录规则。主会话的判断：这些大多成立，而且都集中在“目录发布”这一步上，所以把这一步后移（见 next）。
- 2026-10-14 00:37 A 段由主会话写完（用户 10 月 13 日夜：“codex当前推进太慢了，全部交给你来处理现在，全部推进”）。做法和数字：
  - 改之前用旧规则把全仓库每个文件分了一遍类：354 个落到“全量发布”，其中约 170 个是 `scripts/` 下的脚本、54 个是 `scripts/data/` 的证据文件、14 个是已停用的 edge 运行时、12 个是 n8n 工作流。改之后：`runtime` 579、`migration` 117、`skip` 1503、`full` 23、`image` 3、`unclassified` 0。
  - 逐个文件比了新旧分类：全量→跳过 211 个（上面那几组，加本机的 compose、LaunchAgent、网关配置、对方 Windows 工作站的 ps1、仓库元数据）；全量→迁移 116；全量→镜像 3；`alembic.ini` 由运行时改成迁移。
  - 脚本的种类不再靠规则里一条条列，而是读 `scripts/CATALOG.md` 的“Runs on”：`47owner` 开头的归主机目录树，其余跳过，目录里没有的脚本归“未分类”。与方案第 2 版的明细表不同，原因：目录本来就要求每个脚本登记在哪台机器上跑。新测试把主机 systemd 单元 `ExecStart` 指到的脚本与目录对了一遍，发现目录把 `backfill-full-market-daily.sh` 登记成工作站（主机的 `peer-daily-backfill.service` 在跑它），已改成 `47owner`。
  - `scripts/fill-longhu-sector-membership.py`（目录登记为“quant container”）归跳过：它是人工用 `docker cp` 从主机目录拷进容器执行的工具，改了它不值得为此构建镜像；要用新版本时从工作站拷过去。
  - 迁移：纯代码发布本来就把整个 `quant-service/migrations` 放进覆盖层（我们这一侧从不自己执行迁移），缺的只是“对方的库是否已到本次发布的 head”这道检查。把全量发布里的那段闸门原样搬成 `schema-gate.sh`，两条发布路径共用；全量发布的 16 个测试不改就通过。
  - 没做（留给 B 段）：compose、systemd 单元、主机脚本的不构建发布；构建前的内存检查；决策记录。
- 2026-10-14 00:37 评审：另起一个只读的 Claude 子代理（opus）独立评审提交 a3783f22，重点是“有没有主机或镜像实际在用的路径被判成了跳过”。结果返回后记在这里。
- 2026-10-14 00:41 同一分支上加了一个提交（da300c04）：B041 的遗留，把文档里仍指向旧 owner 地址的操作命令改到新服务器（发布流程、读接口说明、股票数据接口说明、老师复盘说明、运维页；搭建那一节保留原命令并加注），`scripts/sync-xiaojie-message-features.sh` 的默认地址改成新服务器（它在目录里登记为工作站脚本，新规则下是跳过；10 月 13 日撤回这处改动只是因为旧规则把它判成全量发布）。`python -m pytest scripts -q` → `1 failed, 371 passed, 3 skipped`（B035）。
- 2026-10-14 01:00 A 段独立评审返回（报告 `docs/work/probes/2026-10-14-classifier-review.md`）。评审者自己跑了新旧规则对全仓库的分类并逐类看了从全量变成跳过的 211 个路径：除 `fill-longhu-sector-membership.py` 一个外没有发现主机或镜像在用的；全量发布的行为没有变（闸门搬出去后内容相同、退出码和提示相同）；两条高、六条中、三条低，都还没修（见 next）。主会话还没逐条对代码核实。
- 2026-10-14 10:31 新会话 4a420907 接手（旧会话 93ba6e1d 已无动静，工作区干净，head da300c04）。
- 2026-10-14 10:44 A 段评审的 11 条逐条对代码核实后修正（3598250f 代码与测试，09743272 文档与决定 0024）：
  - 1（高）`fill-longhu-sector-membership.py` 的目录登记改成 `47owner (copied into the container)`，它是 `full`。规则里把跳过的登记值逐个写明（`workstation…`、`owner-windows`、`never run`、`no host runs it`），别的登记值一律 `unclassified`。评审怀疑同类的另两个脚本（`backfill-eastmoney-hot-rank-history.py`、`probe-public-sources.py`）没有改：它们的说明只写了在容器里执行 `/tmp/…`，没有说文件从主机目录拷，仍登记为工作站。
  - 2（高）目录里某个脚本的登记在两个提交之间跨过 `47owner` 这条线时，它输出 `full`，不在差异里也输出（行尾注明原因）；反方向且脚本在差异里的也是 `full`。放行方式是环境变量 `RELEASE_RELABELLED_CURRENT`（空格分隔的路径），含义是“核对过主机上那份就是目标提交的”。没有解决的那一半写进了决定 0024 的“后果”：改分类规则本身把一类路径从跳过改成全量时，规则管不到此前被跳过的改动。
  - 3 隧道镜像的两个文件单列 `tunnel-image`：纯代码发布拒绝；发布计划仍给出全量发布命令，另加一行说明全量发布只把文件带上主机、不重建隧道。全量发布脚本本身没有改（它不读分类）。
  - 4 两处 `git diff --name-only` 加 `--no-renames`。5 干跑的那句改成“the schema gate passed: see the schema: line”。6 闸门改成每次纯代码发布都跑；代价是检查模式也要 SSH 读一次对方的 `/health`，对方主机不通时检查模式会失败。
  - 7 COPY 测试：跳过的来源逐个列出（测试目录和两个工具脚本），每个来源至少匹配一个被跟踪的路径；`.dockerignore` 是 `image`、`alembic.ini` 和 `env.py` 是 `migration` 各有断言。
  - 8 新测试（`PairOfCommitsTests`，在临时仓库里对两个提交分类）：名单之外的登记值、被删除的脚本、被移动的文件、登记跨线及其放行、目录生成器不收的路径。主机上手工执行或被主机脚本调用的七个脚本必须是 `full`。
  - 9 `scripts/` 下跳过的模式与目录生成器不收的范围一致；`deploy/*.conf` 改成点名三个网关配置。
  - 10 决定 0002、0003 加了带日期的更正；`RELEASE_SYNC_47.md` 两处；发布计划在库落后时给纯代码发布命令加注“refused until…”；`deploy-code-only.sh` 的用法说明写了检查模式会读对方的 `/health`；新决定 0024（0023 被 W006 用了）。
  - 11 提交里没有目录文件时在 stderr 说明；三个重复的循环合成一个 `refuse`。主机地址默认值在四处重复，没有改。
  - 用新规则把 owner 现在跑的 0a57d7fb 到本分支 head 分了一遍：95 个跳过、2 个 `full`，就是上面说的登记跨线的两个脚本（见 next 的第 2 点）。
- 2026-10-14 10:44 correction: 10 月 14 日 00:37 那条说 `fill-longhu-sector-membership.py` “归跳过…要用新版本时从工作站拷过去”。`OPERATIONS.md:374-377` 写的做法是在 peer 上从主机目录 `docker cp`，所以它属于主机的目录树；已按上面第 1 条改正。
- 2026-10-14 10:57 A 段合并：main 合进分支（919aa056）复跑通过后 `--no-ff` 合回 main（86831925）。这一轮评审修正没有第二个评审者看过；合并的依据是用户 10 月 13 日 17:02 的话（W006 的 Requirements：两条实现线做到可发布为止）。
- 2026-10-14 10:59 correction: 本会话上面两条最初写的时刻（10:40、12:10）是估的；已按提交时间改成 10:31、10:44。
- 2026-10-14 11:20 用户 11:02 要求 owner 修好之前把现在能做的代码都做完。B 段里与主机无关、今天就有用的一项先做了：全量发布在构建镜像之前读主机的可用内存，不足 700 MB 就拒绝（退出码 7，此时什么都没改），放行要带 `RELEASE_ALLOW_BUILD_ON_HOST=1`（提交 599ee1e3，合进 main 556a9a2c；`python -m pytest scripts -q` → `382 passed, 3 skipped`，其中一条测试把这段检查拿出来对着假的 `/proc/meminfo` 实际执行）。700 这个数是主会话定的，方案没有给数；894 MB 的主机上它总是拒绝，这是有意的。B 段其余部分（主机目录树的不构建发布）没有开工：方案写明要等 A 段用过一阵、并先把第 2 轮审计的 11 条并进方案；切换脚本也只能在主机上演练。
- 2026-10-14 11:17 主机恢复后做了 next 第 (1) 点的只读核对：主机上那两个脚本与 main 上的内容相同（FACTS F032）。所以这次发布可以用 `RELEASE_RELABELLED_CURRENT` 放行这两个脚本；`state/rel07.sh` 在每次运行时自己再比一遍。主机内存已是 3,499 MB（F030），全量发布的内存检查在它上面会通过。

## Waiting on the user

## Running

- [x] 2026-10-13 23:17 Codex 方案审计第 2 轮在跑（线程 01a135b0-5382-74e2-b25b-a52af7b9ead2，只读）。结束标志：它交回 SOUND 或编号的异议。之后见 next。挂在主会话 93ba6e1d 上。 → 11 条（2026-10-13 23:30）
- [x] 2026-10-14 00:37 A 段独立评审（只读 Claude 子代理，挂在主会话 93ba6e1d 上；主会话结束它就停）。报告：会话草稿目录 `/private/tmp/claude-501/-Users-papa-codebase-n8n/93ba6e1d-d513-4b99-8ce8-0ca62ebc19e1/scratchpad/review_classifier.md`（会话结束后可能不在）。结束标志：主会话收到完成通知。新会话接手而文件不在时重新起一个评审，范围是 `git -C /Users/papa/codebase/.codex-wt/n8n-owner-hot-a show a3783f22`。

## Due

## Evidence

- 子代理的事实清单在会话临时目录（`release_internals.md`，48 条，带文件行号），要点已写进方案；临时目录不保留，需要时按方案里的文件行号重读。

## Open questions

- 在别处构建镜像再传到主机加载的做法这次不做，等第一次真的要换镜像时再写（方案的 Out of scope）。在那之前如果必须换镜像，只能用现有全量发布加内存检查的放行开关，由用户决定。
- 隧道镜像（`Dockerfile.ssh-tunnel`、入口脚本）现在的全量发布也不会重建它们；它们怎么更新没有现成流程，方案里归为“镜像”一类，先只做到拒绝并说明。

## Resume here

方案在 `docs/work/plans/owner-hot-releases.md`，还没审计、没开工。这是改发布安全规则的线：不要跳过审计和双评审；`AGENTS.md` 里“依赖、compose、迁移变更必须走全量流程”那几句要和代码一起改，并补决策记录。
