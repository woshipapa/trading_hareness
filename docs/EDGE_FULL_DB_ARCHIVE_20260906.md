# Edge quant 数据库完整可恢复归档

归档时间：2026-09-06（Asia/Shanghai）  
数据库：`quant_intraday_edge`  
归档 ID：`20260906-010015`  
百度网盘路径：`/apps/股票paper存储/db-backups/edge-quant-intraday/20260906-010015/`

## 格式

```text
pg_dump --format=custom --compress=0 --no-owner --no-privileges
  -> zstd
  -> gpg AES-256 对称加密
  -> 256 MiB 分片
```

归档由 `manifest.json` 描述，包含分片顺序、字节数和 SHA-256。恢复时按文件名顺序拼接，使用本地保管的备份口令解密，再由 `pg_restore` 导入；口令不在百度网盘中。

## 结果

- 分片数：2
- 加密归档总大小：270,117,752 bytes
- `part.0000`：268,435,456 bytes，SHA-256 `bf252b99f46f28b1f19786bd4cddb2efe5874630f4f3382917929c15165ff967`
- `part.0001`：1,682,296 bytes，SHA-256 `3ee5e85f893ba04de87e4650e44a2a16112392d37e884a9f230021eaa3aba9b8`
- 百度网盘回读：两个分片的远端大小和 SHA-256 均一致
- 本地恢复校验：解密、zstd 解压成功；`pg_restore --list` 成功读取 835 个对象
- 数据库原地校验：归档前后未删除数据；edge 数据库仍约 3.94GB

## 恢复顺序

1. 从上述目录下载两个 `edge-quant-intraday.dump.enc.part.NNNN`，按文件名排序拼接。
2. 使用本地备份口令执行 `gpg --decrypt`。
3. 使用 `zstd -d` 解压得到 PostgreSQL custom dump。
4. 在隔离数据库中先执行 `pg_restore --list`，再使用 `pg_restore --no-owner --no-privileges` 导入。
5. 对照 `database.inventory.tsv`、manifest 中的 SHA-256 和恢复后的表/时间范围，完成恢复验收后才允许清理 edge 原库。

本次只删除了 edge 上已完成回读校验的临时分片和临时解密文件，没有删除 PostgreSQL 数据库、schema 或实时证据。
