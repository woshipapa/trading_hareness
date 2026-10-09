# frontend-shared：前端共享层（canonical 源，不参与任何构建）

这里是多个控制台前端共用代码的**唯一可编辑源**。它不打包、不发布、
不进任何 package.json / lockfile / 镜像——消费方式是**每个应用持有自己的
vendored 副本**，由同步工具显式拷贝：

```bash
python3 scripts/sync_frontend_shared.py            # 查看各应用副本状态（只读）
python3 scripts/sync_frontend_shared.py --apply    # 把 canonical 同步到所有副本
python3 scripts/sync_frontend_shared.py --apply frontend   # 只同步一个应用
```

## 规则（由 scripts/test_frontend_shared_sync.py 强制）

1. **只在这里编辑**。应用内的副本（如 `frontend/src/shared/`、
   `feishu-relay/dashboard/src/shared/`）直接改动会使守卫测试失败——
   副本内容必须等于 canonical 的某个**已提交**版本。
2. **允许滞后，不允许分叉**。canonical 更新后，各应用**自主决定**何时
   同步：跑同步脚本 → 跑自己的 typecheck/测试/构建 → 走自己的发布节奏。
   改 canonical 本身不触碰任何组件源码，因此不触发任何构建或部署。
3. **保持框架内自洽**。这里的模块不得 import 任何应用内部代码，也不得
   引入新的 npm 依赖（副本要在每个应用现有依赖下原样编译）。
4. 新增共享文件时，在 `scripts/sync_frontend_shared.py` 的 `FILES`
   映射里登记它的副本位置。

## 为什么不用 file:/workspace 依赖

实时链接会让共享层改动隐式进入两个应用的下一次构建，使两个独立发布单元
的镜像/部署互相影响。vendored 副本保证：两个项目独立维护、独立构建、
独立回滚，共享只发生在显式同步的那一刻。
