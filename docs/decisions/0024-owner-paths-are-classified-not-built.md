# 0024 owner 的改动按路径分类；不认识的路径是缺一条规则，不是去构建镜像

- 状态：生效（2026-10-14）；实现见 `scripts/shared-peer/classify-owner-paths.sh`、`scripts/shared-peer/schema-gate.sh`、`scripts/shared-peer/deploy-code-only.sh`、`scripts/release_plan.py`
- 日期：2026-10-14

## 背景

owner 的量化主机 2026-10-13 换成了一台 894 MB 内存的服务器，全量发布要在这台主机上构建镜像。而分类规则把“没有列出的路径”一律判成全量发布：按旧规则把全仓库分一遍，354 个文件落在全量发布里，其中约 170 个是 `scripts/` 下在工作站上跑的脚本，54 个是证据文件。10 月 13 日改两个手工脚本的默认地址，就让下一次发布变成了全量，只好撤回。迁移文件也一律走全量发布，尽管我们这一侧从不自己执行迁移，纯代码发布本来就把整个 `quant-service/migrations` 放进覆盖层。

## 决定

1. 每个改动的路径归到一类：`runtime`（覆盖层）、`migration`（覆盖层，过库版本闸门）、`skip`（到不了 owner）、`full`（主机自己的目录树：compose、systemd 单元、主机上执行的脚本）、`image`（镜像的输入）、`tunnel-image`（隧道镜像的输入）、`unclassified`。
2. 没有规则提到的路径是 `unclassified`：纯代码发布拒绝，并要求给它加一条规则。它不触发任何构建。
3. `scripts/` 下的脚本由 `scripts/CATALOG.md` 的“Runs on”决定：`47owner…` 是 `full`；`workstation…`、`owner-windows`、`never run`、`no host runs it` 是 `skip`；别的登记值和没有登记的脚本是 `unclassified`。跳过的登记值在规则里逐个写明，新的或拼错的登记值不会被悄悄跳过。
4. 一个脚本的登记在两个提交之间跨过 `47owner` 这条线时，它按 `full` 输出，即使这次差异里没有它：登记成别的主机期间对它的改动都被跳过了。核对过主机上那份就是目标提交的内容，才用 `RELEASE_RELABELLED_CURRENT` 逐个放行。
5. 迁移文件随覆盖层发布。**每一次**纯代码发布都过库版本闸门：对方的库没到本次发布的迁移 head 就拒绝，不看这次差异里有没有迁移文件（起点 SHA 给错、此前用过放行开关、对方恢复过库，都会让“差异里没有迁移”和“库是齐的”不是一回事）。闸门与全量发布共用一个脚本。
6. 隧道镜像的两个输入单列一类。纯代码发布拒绝；全量发布只把文件带到主机上，不重建隧道镜像，也不重建隧道容器，发布计划会写明这一点。
7. 镜像的输入从 `Dockerfile.peer` 的 COPY 行核对：每个 COPY 来源必须是 `runtime`、`migration` 或 `image`，被跳过的在测试里逐个列出并写明理由。

## 后果

- 改工作站脚本、证据文件、本机 compose、别的主机的部署文件，不再让 owner 的发布变成全量。
- 给仓库加一种新路径时，第一次发布会被拒绝，直到有人写下它属于哪一类。这是有意的。
- 纯代码发布的检查模式也要读一次对方的 `/health`（闸门）；对方主机不通时检查模式会失败。
- 改分类规则本身、把一类路径从 `skip` 改成 `full` 时，规则管不到“此前被跳过的改动”：主机的目录树停在上一次全量发布，而路径是对着上一次纯代码发布的 SHA 分类的。改这种规则的人要自己核对主机上那几份文件。
- 全量发布不读这份分类：它照旧发布并构建整个提交，包括含有 `unclassified` 路径的提交。
- 主机目录树的不构建发布（compose、systemd 单元、主机脚本）还没有做；在那之前这类改动仍要全量发布（方案 `docs/work/plans/owner-hot-releases.md` 的 B 段）。

## 怎样保证

- `scripts/test_owner_path_classes.py`：仓库里每个被跟踪的路径都有规则；主机的 systemd 单元和主机上手工执行的脚本是 `full`；COPY 来源的种类；登记值在名单之外、被删除的脚本、被移动的文件、登记跨线及其放行。
- `scripts/shared-peer/deploy-code-only.test.mjs`：每一类路径在检查模式下的结果；闸门在库落后、读不到、放行开关三种情况下的行为；差异里没有迁移而库落后时仍然拒绝。
- `scripts/test_release_cli.py`：发布计划对各类混合差异的结论。
- `scripts/test_deploy_full_release.py`：全量发布的闸门行为不变。
