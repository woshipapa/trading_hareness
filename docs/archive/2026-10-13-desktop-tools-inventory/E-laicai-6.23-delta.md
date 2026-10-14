# 短线来财 6.18.0 → 6.23.0 差异

2026-10-13 静态对比两版 app.asar 解出的 `electron/` 和 `src/`（只读）。6.23.0 安装包 SHA-256 `14b3e954c1ff4e47b9b82bf784721ce097fe8670ee62b487e1fc56934d2c0ae1`，源码副本在本机 `state/research/duanxian_laicai_6.23.0/`（git 忽略）。打包方式变了：6.18.0 是裸 7z 内嵌，6.23.0 是标准 NSIS + `$PLUGINSDIR/app-64.7z`（electron-builder nsis-web），7zz 能直接拆。

## 一句话

主题是“北京炒家（北炒）打首板”：把原来的单因子战法二重写成一套“七闸评分 + 一票否决”，并据此加了盘中弹窗推送、大盘级风控铁律。竞价战法（11 条）和八阵图没动。另有商业化动作：激活/付款 UI、授权和加固模块被混淆。

## 策略改动

### 战法二：程咬金v2 → 北炒首板（重写）
`config.js:33` id 仍是 `cjv2`，code `QMT1.7`→`BJ-FB`，名 `程咬金v2`→`北炒首板`。实现从 `strategy.js` 一行 `best(sealStrength)` 改为调用新模块 `beichao.rankFirstBoards`（`strategy.js:90-168`，`beichao.js`）。
口径：只看首板（`boardTimes<=1`），每只过“七道闸门”累计打分（满分约 100）+ 三条一票否决；否决或不过线就显示空仓并说明原因。注释把每条规则分成 `[原话]`（北京炒家淘股吧原帖）和 `[整理]`（二手量化口径，取保守值）。
- **一票否决**（`beichao.js` scoreFirstBoard）：V1 一字板（首封 `<=092500`）；V2 尾盘板（首封 `>143000`，约 14:30 后）；V3 孤板（同行业当日涨停 `peers<3`）。
- **七闸**（`ok` 表示过闸，`pts` 入总分）：
  1. G1 板块效应：peers≥5→25 分 / ≥3→20 分；<3 不过。
  2. G2 时间：首封 ≤094000→20 / ≤100000→16 / ≤103000→12（踩 10:30 线）；更晚不过（午前 6、午后 3）。
  3. G3 流通市值：30–80 亿→15 / 80–100 亿→10 / 20–30 亿→10；区间外不过。
  4. G4 低价：股价 <20 元（具体档未全读，`beichao.js:50-56`）。
  5. G5 封单厚度 = 封单额/当日成交额：≥35%→15 / ≥25%→13 / ≥20%→11；<20% 不过。
  6. G6 换手：8–15%→10 / 5–8%→7 / 15–18%→7；>18% 不过。
  7. G7 回封优先：炸 1–2 次后回封（`lastSealTime>firstSealTime`）→5 分。
- **过线判定**：`pass = 无否决 && score>=55 && peers>=3 && 092500<首封<=103000`；评级 A≥70 / B≥55 / C≥40 / D。排序：有否决沉底，pass 优先，再按 score、fund。

### guard.js：加入“炒家铁律”市场级风控（+27 行）
`guard.js` 现在 require `beichao`，叠加两段（均标“养家原话”）：
- `discipline()`：跌停 ≥20 家 → `rest=true` 强制空仓；≥10 家 → 降级“只打最优一只”；封板率 <60% 提示风险、<45% 强制空仓。
- `winRateOf(score)`：把情绪分映射成赢面和仓位（≥80→满仓、≥62→中仓、≥45→小仓、≥30→观望、否则空仓），并常驻提示“纯 K 线打板统计胜率约 `klineWinRate`%，必须分仓讲盈亏比”。

### 新增推送/弹窗（picker.js 657 行 + push.js 402 + popup.js 168 + popup.html + src/popup.js）
- `picker.js`（纯本地纯函数，不触网不落盘）：四组推送标的——①一字板可排单 2 只、②一进二 2 只、③高位龙头→同题材低位切换 2 只、④午后板块内第一只直线拉涨停。判据带 `[原话]/[整理]` 分级（龙飞虎、92科比、养家等）。
- `push.js`：三个时段弹窗排程——09:25:05 竞价确认（①②③各 2 只）、13:00–13:03 午后第一波、13:20–13:23 第二波，连弹 2–3 次。
- `popup.js`/`popup.html`：无边框置顶弹窗窗口，右下角，25 秒自动消失，悬停暂停。

### playbook.js：小改
趋势手法描述等微调（如“多头排列回踩 MA5 → 波段买点，乖离 >5% 不追”）；未见新战法。

## 非策略改动

- **商业化**：新增 `src/js/activation.js`、`src/css/activation.css`、`src/assets/pay-qr.png`、`dashboard.css`、`motion.css`。但 `config.js` 的 `PLANS` 仍是 `[]`、`CONTACT`（蔡老师微信）未变。
- **代码加固（混淆）**：新增 `harden.js`、原 `license.js`（6.18 为 268 行明文）在 6.23 变成 1 行 `/*sycc-obfuscated*/` 混淆代码。授权/激活的真实逻辑这版读不出来，需要反混淆才能看；本次没做。
- `main.js`、`preload.js` 有改动（接入 push/popup 的 IPC，未逐行读）；`src/js/{app,auction-panel,panels,lwc}.js`、`index.html`、部分 css 有改动（多为承接弹窗和激活 UI）。

## 没变

- 其余 8 路打板战法（q10d…v66）、八阵图 8 槽、情绪温度公式、11 条竞价战法（auction-strategy.js）、因子（factors.js）、方法论模块（buydian/longtou/jiedian/cycle/dixi/fanpao/trend）文件无差异。

## 没读/不确定

- `harden.js`、`license.js` 混淆内容（激活和付费是否真正启用）。
- G4 低价闸、picker/push 的完整阈值只读了头部和前半段，用某条阈值前回 `beichao.js`/`picker.js` 对应行核对。
- `main.js` 2800+ 行未逐行读，弹窗触发链只看了 push.js 的排程注释。
