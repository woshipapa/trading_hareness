# 0001 每张表只有一个所有者组件，跨组件读写必须登记

- 状态：生效
- 日期：2026-10-08

## 背景

quant-research、feishu-relay 和各种脚本曾经直接读写彼此的表。quant 的启动检查要求
relay 的 `public.ingestion_jobs` 存在（独立部署时还得挂一个桩 SQL），CI 为了跑 quant
的测试要先初始化 relay 的账本。任何一边改表都可能让另一边启动失败，而且没有地方能
查到谁依赖了谁。

## 决定

- `quant` schema 归 quant-research 所有；relay 的 21 张 `public.*` 账本表归
  feishu-relay 所有（`adapter/ledger.mjs` 创建）。所有权写在
  `config/components.json` 各组件的 `owned_data` 里。
- 一个组件要读写别的组件的表，必须在 `foreign_contracts` 里登记：消费者、对象、
  调用位置和理由。没有登记的跨组件访问就是缺陷。
- 组件之间优先走 HTTP（quant 的 `/openapi.json` 是路由契约），不共享数据库连接。

## 后果

- quant 的启动检查不再要求 relay 的表存在，桩 SQL 已删除；CI 跑 quant 测试不再初始化
  relay 的账本。
- 新增跨组件访问要多写一条登记，这是刻意的摩擦。

## 怎样保证

`scripts/verify_component_boundaries.py --check`（CI 中运行）扫描源码里的表引用，
发现未登记的外部对象，或者 `owned_data` 声称拥有、却不是由该组件创建的表，就失败。
