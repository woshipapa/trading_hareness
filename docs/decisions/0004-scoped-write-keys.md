# 0004 写密钥按调用方分开并限定路径

- 状态：生效（调用方迁移中）
- 日期：2026-10-09

## 背景

十二个调用方共用一个 `X-Quant-Write-Key`。给其中一个轮换密钥，就得同时改另外
十一个；任何一个持有者都能写任何路由；出了问题也看不出是谁写的。

## 决定

- quant-research 接受 `QUANT_WRITE_API_KEYS`：`调用方|路径前缀,...|密钥;...`，`*` 表示
  全部路径。未知密钥返回 401；已知密钥写到作用域之外返回 403，并指明调用方。
- 共享的 `QUANT_WRITE_API_KEY` 继续有效，身份是全作用域的 `legacy`，直到最后一个
  调用方迁走。
- `/health` 的 `write_boundary` 报告调用方名和作用域（从不包含密钥）、配置问题、
  启动以来各调用方的写入次数，以及按状态码统计的拒绝次数。
- 每个调用方的密钥放在 `.env.local` 的 `QUANT_WRITE_KEY_<CALLER>`；作用域写在
  `config/secrets/env-split.py` 的表里（不是机密）。迁移顺序：先 owner，再调用方。

## 后果

edge relay 的作用域暂时是 `*`：面板上的研究操作都经它的 `proxyResearchAction`
转发。要收窄，得等 relay 的路由表从 OpenAPI 生成之后。

## 怎样保证

`quant-service/tests/test_scoped_write_keys.py`；`/health` 里 `legacy` 的写入计数
归零，说明迁移完成。
