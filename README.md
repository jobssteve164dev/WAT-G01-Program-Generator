# WAT G‑01 程序生成工作台

这是一个能直接部署和参与使用的 G‑01 垂直切片：以 Nexperia BUK7M6R0-40H 车规级功率 MOSFET 的公开规格为输入，完成测试条件编辑、能力与安全校验、4080/TIS 风格参考算法生成、公开典型值确定性回放、程序预览和 ZIP 程序包下载。

## 直接部署

机器只需安装 Docker 和 Docker Compose。在本目录执行：

```bash
docker compose up --build -d
```

浏览器打开：<http://localhost:8080>

如 8080 已被占用，可指定端口：

```bash
WAT_PORT=9080 docker compose up --build -d
```

然后打开 <http://localhost:9080>。

检查服务状态：

```bash
docker compose ps
curl http://localhost:8080/api/health
```

## 使用流程

1. 查看当前公开器件及原厂数据手册。
2. 勾选测试项目，直接修改激励、量程、限流值和判定限值。
3. 点击“校验并生成程序包”。不安全或超能力的条件会被阻止，并给出具体原因。
4. 查看公开典型值回放结果和 4080/TIS 风格程序预览。回放只覆盖数据手册已给出的条件，不外推器件模型。
5. 下载 ZIP，其中包含测试计划、限值、参考算法、回放结果、校验报告和制品清单。

输入相同，生成文件内容和制品哈希保持一致，便于复核和版本追踪。

## 当前使用边界

当前版本用于把“公开规格 → 可评审程序包”的流程跑通。它明确不把封装器件数据手册限值当作 12 寸晶圆 WAT 量产限值，也不会把生成结果标成可直接上机。

真正解锁 4080/4082 机台部署仍需目标机台对应版本的 SPECS 测量算法开发手册、编译器/加载器、实际资源与探针卡映射、离线编译结果、硬件相关性验证和正式审批。缺少这些证据时，界面和程序包都会保持“机台部署锁定”。

## 本地开发与验证

后端：

```bash
cd backend
python -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest -q
```

前端：

```bash
cd frontend
npm ci
npm test -- --run
npm run build
```

开发模式可分别启动 `uvicorn wat_pilot.api:app --reload` 和 `npm run dev`；Vite 会把 `/api` 转发到后端的 8000 端口。

## 开源范围

仓库只包含可运行的 G‑01 演示程序及其测试。内部设计文档、项目输入、机台厂商资料、编程手册原文件和本地生成物不在开源范围内，并由 `.gitignore` 阻止误提交。

项目采用 [MIT License](LICENSE)。
