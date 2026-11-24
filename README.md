# WAT G‑01 程序生成工作台

在工作台创建产品，并为每个产品独立保存多份 WAT 测试计划。可以配置测项、测量条件、单位和上下限，复制或归档计划，并下载已保存的计划用于评审。公开器件 BUK7M6R0-40H 作为独立示例，包含参考算法生成与典型值回放。

## 直接部署

首次部署需要设置一个 G0 管理员账号。在本目录新建仅本人可读的 `.env`，设置 `WAT_G0_ADMIN_USERNAME` 和 `WAT_G0_ADMIN_HASH`。选择一个独立强密码，用以下命令生成密码校验值。命令会隐藏输入，并只输出校验值。

```bash
python3 -c 'from getpass import getpass; from hashlib import pbkdf2_hmac; from secrets import token_bytes; password=getpass("G0 管理员密码: "); salt=token_bytes(16); print(salt.hex() + ":" + pbkdf2_hmac("sha256", password.encode(), salt, 600000).hex())'
```

在支持 `linux/amd64` 容器的 Docker 和 Docker Compose 环境执行：

```bash
docker compose up --build -d
```

浏览器打开 <http://localhost:8080>，用 G0 账号登录。公开部署时，将 `WAT_G0_ORIGIN` 设为浏览器访问的 HTTPS 源，例如 `https://wat.example.org`。应用只读取账号和校验值，部署环境无需保存明文密码。

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

产品和计划保存在 SQLite 中，Compose 使用 `wat-g01-workbench-data` 持久卷。重启或替换容器时保留此卷。同一台服务器部署多个独立实例时，为每个实例设置自己的 `WAT_DATA_VOLUME`。本地直接运行后端时，可用 `WAT_WORKBENCH_DB` 指定数据库文件，默认写入当前目录的 `.wat-data/workbench.sqlite3`。

## 使用流程

1. 登录后新建或选择一个产品，点击“新建计划”。
2. 为计划添加测项，填写测量条件和限值，然后保存。计划支持复制、归档和恢复。
3. 点击“下载已保存计划”获取 JSON。可在“打开公开示例”中继续体验 4080/TIS 参考程序包的生成和下载。

公开示例的参考程序包在输入相同时可重现。样例回放只覆盖其数据手册规定的条件。

## 当前使用边界

产品计划导出是供评审的测量规格文件。机台执行需要与目标版本 SPECS 兼容的计划、测量算法和限值格式，以及上机验证。公开示例的封装器件限值仅适用于其演示用途。

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

开发模式可将 `.env` 中的账号和校验值导入后端环境，再以 `WAT_G0_ORIGIN=http://localhost:5173 uvicorn wat_pilot.api:app --reload` 启动后端，并运行 `npm run dev`。Vite 将 `/api` 转发到后端的 8000 端口。

登录会话最长 8 小时，退出登录或服务重启后失效。当前单实例使用一个由环境配置的管理员账号。

## 开源范围

仓库只包含可运行的 G‑01 演示程序及其测试。内部设计文档、项目输入、机台厂商资料、编程手册原文件和本地生成物不在开源范围内，并由 `.gitignore` 阻止误提交。

项目采用 [MIT License](LICENSE)。
