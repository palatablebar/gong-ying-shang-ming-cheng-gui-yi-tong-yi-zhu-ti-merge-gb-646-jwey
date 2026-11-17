# 运行手册 供应商主数据台账

## 启动

```bash
docker compose up -d --build
curl -s http://127.0.0.1:18205/api/health
```

首次打开任意页面会按内置样例自动采信一次，随后逐级计算归一名、分块、候选对与合并簇。

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

`-v` 会删除命名卷 `merge-data`，批次表与复核决定一并清空。

## 重跑与幂等

- `POST /api/suppliers/import` 带 `force: true` 时按同一批文件重算。
- 批次号由输入摘要（按文件名排序的 sha256 拼接）与规则版本共同决定，同一批文件得到同一个 `run_id`；
  命中已有批次时直接复用结果，不新增记录。
- 复核决定写入后，簇与金额会立即重算，不需要手工触发。
- 派单同步 `POST /api/packages/sync` 幂等：同批重跑不重复派单、不改变任何责任人。

## 上游名录补充

在 `files` 里追加新文件即生成新批次；派单同步会自动完成继承：

```bash
curl -s -X POST http://127.0.0.1:18205/api/suppliers/import -H 'Content-Type: application/json' -d '{
  "files": ["erp_a_primary_utf8.csv","erp_b_alias_bom.csv","erp_c_north_gb18030.csv",
            "erp_d_amounts.csv","erp_e_header_only.csv","erp_f_blank_rows.csv",
            "erp_g_supplement.csv"]
}'
curl -s -X POST http://127.0.0.1:18205/api/packages/sync
```

- 签名未变的工作包：责任人与已完成决定原样继承（决定带 `carried_from` 标记）。
- 签名变化的工作包：旧包留在原批次作审计，新批次重新派单继续裁决。
- 主数据发布只采用当前批次有效包的决定；`GET /api/packages/blockers` 看发布阻断。

## 常用请求

```bash
curl -s -X POST http://127.0.0.1:18205/api/suppliers/import -H 'Content-Type: application/json' -d '{}'
curl -s http://127.0.0.1:18205/api/reconcile
curl -s http://127.0.0.1:18205/api/export/master.csv -o master.csv
curl -s http://127.0.0.1:18205/api/export/manifest.json -o manifest.json
```

判定一张复核单：

```bash
curl -s -X POST http://127.0.0.1:18205/api/reviews/P-00105/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision":"merge","expected_version":0}'
```

## 失败码处理

| 原因码 | 处理 |
|---|---|
| `E_FILE_MISSING` | 检查 `sample_data/` 下的文件名与大小写 |
| `E_MISSING_COLUMN` | 表头缺必填列，按列契约补列后重试 |
| `E_DECODE_FAILED` | 文件既非 UTF-8 也非 GB18030，需转码 |
| `Q_ROW_SHORT` / `Q_ROW_LONG` | 该行进隔离区，修正分隔符后重跑 |
| `V_STALE_DECISION` | 页面停留过久，刷新后按新版本再提交 |
| `V_BAD_DECISION` | 税号冲突的候选对不允许合并 |
| `V_UNKNOWN_PAIR` | 候选对编号不属于当前批次，刷新页面 |
| `V_UNKNOWN_PACKAGE` | 工作包号不属于当前批次，刷新 `/packages` |
| `B_REVIEW_PENDING` | 工作包仍有待决关系，裁决完成前发布被阻断 |

## 测试

```bash
docker compose exec -T app python -m pytest -q
```
