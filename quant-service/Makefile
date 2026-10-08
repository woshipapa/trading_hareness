.PHONY: test test-release-path standalone-up standalone-down dashboard

# 容器内直接跑（镜像里的那份代码）。
test:
	python -m unittest discover -s tests -q

# 把工作区挂进现有镜像再跑：和 deploy-code-only.sh 发布后跑的是同一份源码，
# 只借镜像的依赖。挂的是**仓库根**而不是 quant-service —— 有测试会按
# ``Path(__file__).resolve().parents[2] / "scripts"`` 找仓库根下的脚本，
# 只挂子目录会让它找不到。
#
# 为什么需要它：``docker compose exec -T quant-research`` 跑的是**镜像里烤进去**
# 的代码。日常变更走源码 overlay、不重建镜像，所以那条命令会放过所有新代码 ——
# 2026-10-08 实测同一个模块 exec 跑出 32 个、这里跑出 35 个；全量 2290 对 2336，
# 被静默跳过 46 个。
#
# 数据库每次新建、从工作树迁到 head、跑完删除（和 CI 一样），结果不再取决于开发库里
# 以前留下的数据；指定模块：make test-release-path QUANT_TESTS="tests.test_x"。
test-release-path:
	QUANT_TESTS="$(QUANT_TESTS)" bash scripts/run-release-path-tests.sh

standalone-up:
	docker compose -f compose.standalone.yaml up --build

standalone-down:
	docker compose -f compose.standalone.yaml down --volumes --remove-orphans

dashboard:
	cd ../frontend && npm ci && npm run typecheck && npm run build
