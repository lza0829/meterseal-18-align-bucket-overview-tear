# 抄表簿

骨架：抄表读数装载 → 估抄/实抄对齐 → 差额处置 → 账期封印。

- 前端 http://127.0.0.1:6010（勿用 6000，浏览器会拦）
- 接口 http://127.0.0.1:11000

## 容差架构（全局唯一）

同一组估抄/实抄读数在整个系统里只允许一把尺子，全局贯彻 **相对误差率**：

```
ratio = |actual - estimate| / max(|estimate|, rel_eps)
ratio <= rel_tol   -> 自动合拢 matched，并写 match_audit 审计行
ratio >  rel_tol   -> 生成差额 variances，fate = pending，必须人工处置
```

绝对差值口径（`classify_abs`）只存在于 `app/services/tolerance.py`，**仅作测试对照物**，
用于证明两把尺子在相同读数上会给出不同的 matched 集合；生产路径不得调用，
`tol_mode` 也不允许被切换为 `abs`。

选择相对口径的原因：同一账期里水表（≈12 m³）与电表（≈300 kWh）量级不同，
单一绝对容差对水表约为 4%、对电表不到 0.2%，而按表类型分设容差被明令禁止；
相对误差率无量纲，对两者一视同仁，既不会放大基数漏掉大偏差，也不会把大基数微差误判成差额。

### 联动点（改一处必须同时成立）

| 环节 | 落地 | 约束 |
| --- | --- | --- |
| 容差维护 | `PUT /api/settings/rel_tol`、`rel_eps` | 校验 `0<rel_tol<1`、`rel_eps>0`；改后所有 open 批置 `stale`，不可封印 |
| 差额生成 | `run_align` | 只读一次尺子；超容差一律 `pending`，绝不自动 `accept_over/short` |
| 自动合拢审计 | `match_audit` | 只写不改，逐行记录 `outcome/reason/ratio/tol_mode/rel_tol/rel_eps` |
| 批次快照 | `reconcile_batches.snapshot` + `tol_mode/rel_tol/rel_eps` | 封印时校验快照与当前尺子一致，且批内每行尺子相同 |
| 命运落库 | `set_fate` | 已封印账期或 non-open 批拒绝；认短/认溢必须与 `delta` 方向一致；单据重复不可直接认账 |
| 封印闸 | `seal_period` / `writings` | `BEGIN IMMEDIATE` 内检查并写入，封印与写读数不会交错 |
| 重对齐 | `run_align` | 旧 open/stale 批转 `expired`，其差额不得再处置 |
| 总览 | `GET /api/overview` | 未决数 = 其分桶之和，且只计 open 批；封印残留单列 `sealed_unresolved` |
| 列表 | `GET /api/batches`、`/variances`、`/matches` | 全部回带所属尺子字段 |

### 边界口径

- `estimate = 0` 且 `actual != 0`：分母只有 `rel_eps`，比例巨大 → 必然 `pending`，不会因除零误合拢。
- 同一表同一账期出现多张单：`reason = multi_doc`，强制 `pending`，避免拿错单据对齐。
- 缺估抄或缺实抄：直接 `pending`，`reason` 标明缺哪边。

## 计数一致性架构（全局唯一）

差额计数全局只贯彻一种口径：**以差额分桶正式表（`variances` / `match_audit`）为唯一真源**，
批快照字段与总览计数都只是分桶的投影，库内不允许存在第二套计数（缓存、涂改、镜像列）。

```
分桶正式表（variances.fate / match_audit）  ── 真源，唯一可写
   ├─ 批快照字段 reconcile_batches.matched_count / variance_count / snapshot
   │     = 分桶投影；漂移即按分桶重算并【落库回写】，不是只刷总览
   ├─ 总览未决 / 已决 / 自动合拢 = open 批分桶与审计行的实时聚合，无缓存
   └─ 差额列表 / 批列表 / 合拢审计 = 直接读正式表，过滤只换 WHERE 不换口径
```

### 一致性联动点（改一处必须同时成立）

| 环节 | 落地 | 约束 |
| --- | --- | --- |
| 对齐入口 | `run_align` | 旧批 expired、新批 open、分桶行、审计行、快照字段在同一 `BEGIN IMMEDIATE` 落库；同事务按分桶重算回写快照字段，回执数字即落库数字 |
| 分桶空而快照非零 | `consistency.recompute_batch` | 判定为半截生成（ghost），该批 `status='void'` 作废现行，不得重算成 0 蒙混；void 批不可处置、不可封印 |
| 快照字段漂移 | `consistency.heal_if_drifted` | 总览 / 批列表读路径先只读检测，发现漂移开写事务按分桶重算落库再出数；封印预检同规则 |
| 处置入口 | `set_fate` | 同一事务改分桶行，总览派生即同事务跟随；禁止只改列表或只刷缓存的第二路径 |
| 并发对齐 | `run_align` | 写事务串行化，同账期任意时刻至多一个 open 批，不得双快照现行；半截写入整体回滚 |
| 涂改层遗留 | `consistency.purge_paint_cache` | 旧库升级（启动）与每次对齐事务内清除 `align_pending_summary` / `batch_paint:*` 缓存键 |
| 封印预检 | `seal_period` | `pending_left` 直接数该批分桶行，与总览未决同口径 |

## 启动

```powershell
cd backend
C:\Users\18435\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 11000 --app-dir .
```

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1 --port 6010
```

## 测试

```bash
cd backend
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests -q
```

覆盖：两把尺子在同一组读数上分叉、超容差不得自动合拢、零基数/缺边/重复单据、
已封批不被改容差撬动、改容差使开放批作废、并发改尺子与对账不混用新旧尺子、
封印闸与写入竞争、总览未决计数与分桶一致、旧库就地升级。
计数一致性（`tests/test_bucket_truth.py`）：分桶空而快照非零的 ghost 批作废、
快照字段漂移按分桶重算落库、涂改缓存被忽略并清除、处置后分桶/快照/总览/列表
四分叉同动、并发对齐不双快照现行、处置与重对齐抢跑无半截残留、启动自愈旧库。
