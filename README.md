# 线路公告增量收件 API

线路资料员每天查看运营方是否新发了**停运 / 更正 / 撤销**公告，并把新公告交接给
调度室。本服务按公告首页 URL 抓取同站分页与详情，用 SQLite 维护每个站点的
**已收录编号集**和**待交接列表**：

1. **首次完整扫描**只建立编号集（已有公告视为历史，不打扰调度室）；
2. 之后**只有完整扫描**才把新编号及原文链接加入待交接列表，并同步更新编号集；
3. 确认指定编号后，它从待交接列表移除且以后不再提示；
4. 列表/详情抓取或解析失败、分页循环、达到页数上限时返回 **无法核定**，
   编号集与待交接列表都保持原状；
5. 同一站点的并发扫描与确认由 SQLite 事务串行化，并在提交时重新核对确认记录，
   已确认公告不会被重新入列。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/scan` | 按 `index_url` 做一次同站全量扫描 |
| `GET` | `/pending` | 待交接列表，可选 `?index_url=` 过滤站点 |
| `POST` | `/confirm` | 确认编号，`index_url` 可选 |
| `GET` | `/known` | 已收录编号集（核对/诊断用） |
| `GET` | `/health` | 健康检查 |

交互式文档：`http://127.0.0.1:8000/docs`。

### 扫描

```bash
curl -s -X POST http://127.0.0.1:8000/scan \
  -H 'Content-Type: application/json' \
  -d '{"index_url":"http://notice-site:8080/index.html"}'
```

首次成功扫描返回：

```json
{
  "status": "baseline_established",
  "result": "ok",
  "complete": true,
  "baseline": true,
  "scanned_count": 10,
  "known_count": 10,
  "new_notices": [],
  "pending_notices": [],
  "errors": [],
  "message": "首次完整扫描完成，已建立编号集（10 条），已有公告不进入待交接列表"
}
```

运营方上新后再次扫描，`status` 为 `updated`，`new_notices` / `pending_notices`
中包含新编号及详情原文链接。

无法核定时仍返回 HTTP 200（请求本身成功，但业务上无法核定），响应中：

```json
{
  "status": "undetermined",
  "result": "undetermined",
  "complete": false,
  "known_count": 10,
  "new_notices": [],
  "pending_notices": [],
  "errors": ["详情页抓取失败 http://site/notices/N-2026-015.html: ..."],
  "message": "无法核定：..."
}
```

`known_count` 与 `pending_notices` 是扫描前的原状快照，本次没有写入任何数据。

### 确认

```bash
curl -s -X POST http://127.0.0.1:8000/confirm \
  -H 'Content-Type: application/json' \
  -d '{"notice_id":"N-2026-011","index_url":"http://notice-site:8080/index.html"}'
```

- `index_url` 提供时只确认该站点；
- 省略时按编号在该编号所属的所有站点中确认；
- 若编号尚未被任何站点收录（例如扫描进行中用户先确认），记录为全局确认，
  之后扫描也不会把它入列；
- 重复确认幂等，返回 `already_confirmed`。

### 站点身份

站点键 = **协议 + 主机 + 端口**，与爬虫的同站判定一致。`index_url` 只是列表入口，
同一站点不同列表页共享编号集/待交接状态。通过 Compose 演示时，API 容器内使用
`http://notice-site:8080/index.html`；若改成 `http://127.0.0.1:8080/index.html`
会被视为另一个站点（主机不同），请保持一致。

## 完整扫描与“无法核定”

一次扫描必须同时满足以下条件才算完整，才会写入 SQLite：

- 从首页开始沿 `rel=next` 抓完所有同站列表页；
- 每个列表页都能解析出公告列表容器与详情链接；
- 所有同站详情页都抓取成功并解析出公告编号；
- 未出现分页循环；
- 未达到列表页数上限 `/` 详情页数上限。

任一条件不满足即返回 `undetermined`。跨站链接按同站约定跳过并记录，不算失败。
页数上限可用环境变量调整：

```
MAX_LIST_PAGES=50     # 默认 50
MAX_DETAIL_PAGES=500  # 默认 500
```

## 并发正确性

同一站点的写操作使用 SQLite `BEGIN IMMEDIATE` 事务：

- 事务内重新读取编号集与确认记录后再计算“新编号”，并发扫描不会基于旧快照重复入列；
- `(site_key, notice_id)` 主键保证待交接列表与编号集天然去重；
- 确认记录与“扫描进行中先确认”的全局确认记录在扫描提交时再次核对，
  已确认公告不会被重新入列；
- 完全不完整的扫描根本不会调用写入路径，因此状态保持原状。

## 目录结构

```
app/
  app/main.py        FastAPI 入口（scan/pending/confirm/known/health）
  app/crawler.py     同站分页 + 详情抓取；完整性判定
  app/parser.py      列表页/详情页解析
  app/store.py       SQLite 编号集、待交接列表、确认记录（原子提交）
  app/models.py      Pydantic 模型与 API 契约
  Dockerfile
site/
  server.py          可切换批次/故障的本地示例站（纯标准库）
  batches.py         v1/v2/v3 三批示例公告与 HTML 渲染
tests/               pytest：解析、爬虫、存储并发、API 端到端
demo/
  flow.py            离线演示流程（上新 → 失败重试 → 确认）
  run_compose_demo.sh
  run_local_demo.sh
docker-compose.yml   api(8000) + notice-site(8080) + SQLite 数据卷
```

## 运行

### Docker Compose（推荐，完整离线演示）

```bash
./demo/run_compose_demo.sh
```

脚本会清理旧数据卷、构建并启动 API 与示例站，然后自动执行：

1. 批次 `v1` 首次完整扫描 → 建立编号集（10 条，无待交接）；
2. 切换到 `v2` → 扫描发现 4 条新公告并加入待交接；
3. 切换到 `v3` 并让 `N-2026-015` 详情返回 500 → 扫描返回“无法核定”，
   编号集和待交接列表与失败前完全一致；
4. 清除详情故障后重试 → 一次性收齐 `v3` 的 3 条新公告；
5. 确认 `N-2026-011`、`N-2026-015` 后再次扫描 → 不再提示，且不会重新入列。

停止服务：`docker compose down -v`。

### 本地 venv（无 Docker）

```bash
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
./demo/run_local_demo.sh
```

### 测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

测试会在随机端口启动真实的示例站子进程，每个用例使用独立的 SQLite 数据库，
覆盖完整扫描、详情故障、解析失败、分页循环、页数上限、并发扫描、
扫描中确认以及确认后不再入列。

## 示例站控制接口

示例站（宿主机 `http://127.0.0.1:8080`）支持在线切换，无需重启：

```bash
# 查看当前状态
curl -s http://127.0.0.1:8080/control/state

# 切换公告批次 v1 / v2 / v3
curl -s -X POST http://127.0.0.1:8080/control/batch \
  -H 'Content-Type: application/json' -d '{"batch":"v3"}'

# 指定公告详情故障：http500（抓取失败）或 malformed（解析失败）
curl -s -X POST http://127.0.0.1:8080/control/failure \
  -H 'Content-Type: application/json' \
  -d '{"mode":"http500","notice_ids":["N-2026-015"]}'

# 清除故障
curl -s -X POST http://127.0.0.1:8080/control/failure \
  -H 'Content-Type: application/json' -d '{"mode":"none"}'

# 模拟列表页故障 / 分页循环
curl -s -X POST http://127.0.0.1:8080/control/list-failure \
  -H 'Content-Type: application/json' -d '{"enabled":true}'
curl -s -X POST http://127.0.0.1:8080/control/pagination-loop \
  -H 'Content-Type: application/json' -d '{"enabled":true}'

# 人为放慢详情响应（演示“扫描进行中同时确认”的并发不变量）
curl -s -X POST http://127.0.0.1:8080/control/delay \
  -H 'Content-Type: application/json' -d '{"ms":200}'
```

批次内容：

| 批次 | 公告 | 用途 |
| --- | --- | --- |
| `v1` | `N-2026-001` ~ `010`（10 条，3 个列表页） | 首次扫描基线 |
| `v2` | 追加 `011`（停运）、`012`（更正）、`013`（撤销）、`014`（停运） | 演示上新 |
| `v3` | 再追加 `015`（更正）、`016`（撤销）、`017`（停运） | 演示失败重试 |

接入真实站点时，只需替换 `app/parser.py` 中的选择器与字段键映射；
爬虫、SQLite 存储与 API 契约无需改动。
