# 影视剧组拍摄制作管理平台

面向剧本分场、选角档期、拍摄通告、场地器材、后期特效与杀青结算的一体化剧组管理后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   ├── app/store.py          内存数据仓库与示例数据
│   └── data/                 剧组成员数据源（sample 入库，local/prepared 为本地文件）
├── scripts/                  本地启动流水线（依赖校验、数据准备、启动前检查、编排、冒烟）
├── .gitignore
└── docker-compose.yml
```

## 启动

推荐使用一键流水线，把**依赖校验 → 剧组成员数据准备 → 启动前检查 → 启动 → 冒烟检查**
串成一条可重复的流程；任一环节失败都会说明原因并停在该阶段，修好后重跑同一命令即可：

```bash
make dev-up          # 五阶段全跑；依赖缺失时会停下并提示，先 make check-deps-install 再重跑
./scripts/dev-up.sh --install   # 等价于 dev-up，但依赖缺失时自动安装修复
make dev-down        # 停止并清理本流水线拉起的进程（按进程组回收，不留 vite 孤儿）
```

各阶段也可以单独执行，方便定位问题：

| 命令 | 阶段 | 失败时的行为 |
| --- | --- | --- |
| `make check-deps` | 校验 python3 ≥ 3.10、node ≥ 18、后端 venv、前端 node_modules | 列出缺失项与修复命令；加 `--install`（`make check-deps-install`）自动安装 |
| `make prepare-data` | 校验剧组成员数据源并原子产出 `backend/data/crew.prepared.json` | 报出具体行/字段原因；源文件与已有产物都不改动 |
| `make preflight` | 数据产物与数据源一致、后端可导入、8000/5173 端口空闲 | 逐项列出原因和修复方式，不启动任何服务 |
| `make dev-up` | 启动前后端、等待健康、冒烟检查列表与四种状态 | 健康/冒烟失败时自动回收本次已启动的服务 |

冒烟检查会确认 `/api/health` 模块数完整、`/api/crew` 列表中
**待进场 / 在组 / 已请假 / 已离场** 四种状态各至少一条且筛选接口非空、
前端 dev server 与 `/api` 代理可用。

### 剧组成员示例数据的两份输入

```text
backend/data/crew.sample.json      随仓库分发的 canonical 样例（入库，固定进场日期与真实岗位）
backend/data/crew.local.json       本地覆盖（gitignore；复制 sample 改名后自行修改，脚本永不写它）
backend/data/crew.prepared.json    准备阶段产物（gitignore，临时文件 + 原子替换生成）
```

- `prepare-data` 优先读 `crew.local.json`，没有才读 sample；已请假/日期矛盾/岗位不在
  白名单/状态缺项等问题都会在这一阶段拦下，而不是等服务起来后才发现列表缺项。
- 产物与数据源用 sha256 绑定：数据源改了但没重新准备，`preflight` 会判为过期并要求重跑；
  产物已最新时重跑是幂等的，不会产生无意义变更。
- 后端直接启动（如 `./run.sh`、docker）且没有产物时，回落到 `app/seed.py` 的内置示例，
  不破坏原有启动方式。

### 手动分步启动（原方式，仍然可用）

后端：

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

前端：

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 剧本管理 | `script` | 剧本 | 剧本编号、剧本名称、题材类型 |
| 分场大纲 | `scene` | 分场表 | 场次编号、所属剧本、场景地点 |
| 角色选角 | `casting` | 角色 | 角色编号、角色名称、角色类型 |
| 剧组人员 | `crew` | 剧组成员 | 成员编号、姓名、岗位职务 |
| 拍摄通告 | `notice` | 拍摄通告单 | 通告编号、拍摄日期、集合时间 |
| 场地租用 | `location` | 拍摄场地 | 场地编号、场地名称、场地类型 |
| 道具管理 | `prop` | 道具 | 道具编号、道具名称、道具类别 |
| 服装造型 | `costume` | 戏服 | 服装编号、服装名称、角色归属 |
| 化妆造型 | `makeup` | 妆造方案 | 方案编号、角色名称、造型风格 |
| 器材管理 | `equipment` | 拍摄器材 | 器材编号、器材名称、器材类别 |
| 拍摄进度 | `shooting` | 拍摄日 | 拍摄日编号、拍摄日期、拍摄地点 |
| 素材管理 | `footage` | 拍摄素材 | 素材编号、素材类型、拍摄日期 |
| 后期剪辑 | `edit` | 剪辑任务 | 任务编号、所属集数、剪辑师 |
| 特效制作 | `vfx` | 特效镜头 | 镜头编号、所属集数、特效类型 |
| 审片意见 | `review` | 审片记录 | 审片编号、审片轮次、审片人 |
| 预算科目 | `budget` | 预算科目 | 科目编号、科目名称、费用类别 |
| 费用报销 | `expense` | 报销单 | 报销单号、报销人、费用类别 |
| 档期协调 | `schedule` | 演员档期 | 档期编号、演员姓名、经纪公司 |
| 外景许可 | `permit` | 拍摄许可 | 许可编号、许可类型、申请地点 |
| 杀青结算 | `wrap` | 结算单 | 结算单号、结算对象、结算周期 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
