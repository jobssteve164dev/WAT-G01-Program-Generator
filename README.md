# WAT G‑01 程序生成工作台

在工作台创建产品，并为每个产品独立保存多份 WAT 测试计划。可以配置测项、测量条件、单位和上下限，复制或归档计划，并下载已保存的计划用于评审。公开器件 BUK7M6R0-40H 作为独立示例，包含参考算法生成与典型值回放。

## Windows 离线安装（x64）

在 GitHub Actions 打开 **Windows offline installer**，选择 **Run workflow**。运行成功后下载 **WAT-G01-Windows-x64**，解压后只需把 `WAT-G01-Setup-x64.exe` 复制到内网电脑。安装和使用工作台均可断网，无需管理员权限、Python、Node.js 或 Docker。

1. 双击安装包，在开始菜单打开 **WAT G-01 工作台**。
2. 首次启动时创建管理员账号和密码。浏览器会打开工作台，用刚创建的账号登录。使用期间保持启动窗口打开。
3. 数据保存在 `%LOCALAPPDATA%\WAT G01\data`。升级前先关闭工作台并备份该目录，再用同一 Windows 用户运行新安装包。卸载后保留账号和数据。

默认访问地址为 `http://localhost:8080`。如端口已占用，可设置用户环境变量 `WAT_PORT` 后重新启动。供内网其他电脑访问时，通过内网 HTTPS 反向代理转发至此本机地址，并将 `WAT_G0_ORIGIN` 设为用户访问的 HTTPS 源。原厂机台配方仍按下文的现场要求接入。

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

### 按研发规格制作配方

选产品，点「制作配方」，添加关断漏电流。填写结构、漏源电压、栅源电压、温度和漏电流上限。复制条件可以增加温度点。保存后选适用设备即可生成并下载。在产品尚未接入设备时，要求可保存、重开和修改。旧计划和原有工程接线从「新建计划」或已有计划进入。

### 连接 Red Hat 机台并下发配方

在「机台与探针卡」登记机台档案，然后点「发现内网机台」。选择找到的设备及对应的机台档案，也可点「按地址连接」并填写机台内网 IPv4 和 SSH 端口。Red Hat 机台使用原有 OpenSSH SFTP 服务，无需安装工作台。

1. 在机台准备一个专用接收账号及其可读写的收件目录，例如 SFTP 登录后可见的 `/incoming`。接收目录与正在执行的配方目录分开。账号可限制为仅 SFTP。
2. 在工作台查看这台机台的连接公钥，让设备管理员将公钥加入接收账号的 `~/.ssh/authorized_keys`。在机台本地用 `ssh-keygen -E sha256 -lf /etc/ssh/ssh_host_ed25519_key.pub` 核对 SSH 主机指纹；填写账号、指纹和接收目录后点「验证并保存」。推荐为每台机台保留独立的接收目录。如机台使用其他主机密钥，请核对该机台协商使用的主机公钥指纹。
3. 保存测试要求，在「适用设备」选定机台和探针卡，点「下发到所选机台」。工作台会验证计划版本、适用方法和机台身份，通过 SFTP 传送 ZIP 并回读校验。成功后显示机台上的完整文件路径。在「内网机台」可查看最近一次送达记录、修改连接或解除关联。撤销机台侧授权时，一并从接收账号移除对应公钥。
4. 设备负责人从收件目录取包，并按该机台的 SPECS 版本完成导入和检查。送达回执只确认文件已接收。

自动发现使用同一 IPv4 广播域内的 DNS-SD（UDP 5353），识别 `_sftp-ssh._tcp` 和 `_ssh._tcp`。已运行 Avahi 的机台可将从工作台下载的 [服务声明](frontend/public/wat-sftp.service) 放到 `/etc/avahi/services/`，按实际 SSH 端口调整。跨 VLAN、容器桥接网络或禁用组播时，使用可路由的内网地址连接。请允许工作台访问机台的 SSH 端口。旧版 Red Hat 若仅支持已淘汰的 SSH 算法，可在同一机台配套的受管工程机上设置接收目录。

### 接入已验证的原厂配方

设备负责人在工程机上确认原厂配方、完整的运行依赖、算法、结构、测项顺序和有效参数区间。先用工作台工程计划保存同一机台、卡和逐测项的接线。此版本支持通过已验证的、以制表符分隔的 ASCII 数值单元设置参数的原厂依赖。二进制文件按原字节保留。需要重新编译二进制、生成新 Recipe Key 或增加原厂测项的场景仍需要同版本 SPECS 工具。

将每个已核对的方法安装在 SQLite 同目录的 `recipe-methods/<产品 UUID>/<方法名>/`，例如容器内的 `/app/data/recipe-methods/...`。方法名支持英文字母、数字、横线、下划线（最长 80 字符）。原厂文件及资料留在该私有目录。先在卷外准备完整目录，再整体就位。

`method.json` 包含以下字段。

| 字段 | 设备负责人填写的内容 |
| --- | --- |
| plan、binding | 已保存的工程参考计划 UUID 和接线记录 UUID；一旦计划、机台、卡或接线换版，须重新核对 |
| qualification | 工程机加载、参数读回、测量判定以及下载包导入操作的验证记录编号 |
| entry、files | 原厂配方入口的相对路径，以及包内每个文件相对路径到原件 SHA-256 的完整映射。保留原有目录布局和原厂依赖引用 |
| rows | 按原厂执行顺序逐项填写 structure、parameters；每行对应一项关断漏电流 |
| parameters | 每行均填写 vds_v、vgs_v、temperature_c、upper_ua。固定条件填写 `{"fixed":25}`；可调条件填写已验证的 min、max 和 cells 数组 |
| cells | 每个位置填写 file、line、column（从 1 开始）、original（原文件中的数值字符串）、scale（页面单位乘以此系数得到原厂单位）。同一物理参数有多处实际引用时全部列入 |

原厂方法的确认须逐一改变物理参数，在目标工程环境检查实际偏置、温度和判定限值。只对已证实可按文本改写且原厂入口**实际读取**的依赖登记可调参数。尤其要核对 Recipe 引用的 Limit Spec；旁置的 Limit File 不代表运行时采用了该限值。方法中固定的温度必须对应已确认的测试环境。导出包沿用该方法的原厂入口名、Recipe Key 和依赖名，按现场验证过的隔离加载流程使用，保留旧版本。导出本身不执行机台加载。仓库不包含已取得现场验证的原厂方法。

## 使用流程

1. 登录后新建或选择一个产品，点击“新建计划”。
2. 在“针脚与映射”登记探针卡、机台和每个端子的接线。为测项填写条件和限值，并勾选实际使用的针脚。保存、复制、归档和恢复时都会保留映射与测项引用。
3. 点击“下载已保存计划”获取 JSON。可在“打开公开示例”中继续体验 4080/TIS 参考程序包的生成和下载。

公开示例的参考程序包在输入相同时可重现。样例回放只覆盖其数据手册规定的条件。

## 当前使用边界

产品计划导出是供评审的测量规格文件。机台执行需要与目标版本 SPECS 兼容的计划、测量算法和限值格式，以及上机验证。公开示例的封装器件限值仅适用于其演示用途。

### 从接线到 SPECS 构建

1. 在「机台与探针卡」登记机台的实装模块、通道、切换路径和可达针位。登记探针卡的安装关系、卡针、测试头针位和 SPECS Pad Number。
2. 在已保存计划中打开「配置机台接线」，选择机台和探针卡，设置测试结构的焊盘及卡针，为各测项和阶段选择测量资源。核对后保存，可下载工程接线。
3. 对于 SPECS D.03.1x，在原厂 Test Organizer 中用工程接线核对 Probe Spec、测项算法、参数和限值。保存原厂计划，或用原厂 `transtpl` 导出制表文本，然后下载 SPECS 配方准备包。
4. 在同版本的 SPECS 工程环境按包内说明执行 `sh build-recipe.sh`。脚本调用原厂 `create_recipe`，需要转换 TSF 时先调用 `transtpl`。用 Recipe Editor 的 `Data → Check Recipe` 核对生成结果，再让目标工程机通过配方执行参考测项。

构建包依赖已配置好的原厂计划及 SPECS 工具。试测前须核对机台实际加载及运行结果。

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
