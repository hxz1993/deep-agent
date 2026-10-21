# 结构化输出资源专项验收

本机隔离进程实测，SDK FunctionModel、真实文件 SQLite；不作为容量 SLA 或真实 Provider 性能结论。SQL 计数只记录对应操作窗口，用户批量测试保留真实 resolver，仅替换无网络 fetcher。并发测试测量父 Python 的 tracemalloc 峰值，RSS 是进程累计高水位；子进程 RSS 单列。

```json
{
  "agent_list_scaling": [
    {
      "agents": 10,
      "sql": 1,
      "output_config_selects": 0,
      "username_fetch_batches": [
        10
      ],
      "elapsed_ms": 98.27
    },
    {
      "agents": 100,
      "sql": 1,
      "output_config_selects": 0,
      "username_fetch_batches": [
        100
      ],
      "elapsed_ms": 48.42
    },
    {
      "agents": 1000,
      "sql": 1,
      "output_config_selects": 0,
      "username_fetch_batches": [
        100,
        100,
        100,
        100,
        100,
        100,
        100,
        100,
        100,
        100
      ],
      "elapsed_ms": 23.79
    }
  ],
  "runtime_configuration_reads": [
    {
      "enabled": false,
      "output_config_selects": 0,
      "total_sql": 85,
      "elapsed_ms": 213.98
    },
    {
      "enabled": true,
      "output_config_selects": 1,
      "total_sql": 104,
      "elapsed_ms": 622.98
    }
  ],
  "near_limit_concurrency": {
    "schema_bytes": 65408,
    "output_bytes": 1048448,
    "concurrency": 4,
    "tasks": [
      {
        "validation_ms": 248.74,
        "event_write_ms": 590.32
      },
      {
        "validation_ms": 223.44,
        "event_write_ms": 565.32
      },
      {
        "validation_ms": 351.41,
        "event_write_ms": 525.61
      },
      {
        "validation_ms": 369.53,
        "event_write_ms": 547.04
      }
    ],
    "total_ms": 929.99,
    "heartbeat_samples": 87,
    "max_heartbeat_gap_ms": 95.3,
    "python_tracemalloc_peak_bytes": 9458929,
    "parent_rss_high_water_bytes": 437026816,
    "worker_rss_high_water_bytes": 30031872
  },
  "schema_budget": {
    "output_tool_count": 1,
    "without_output_tokens": 0,
    "with_output_tokens": 1661,
    "changed_schema_fallback": "request_parameters_changed"
  }
}
```

缓存上限另由 tests.unit.test_structured_output_schema 的128项边界回归验证；本专项不重复将该单元结果计为新的性能测量。

实测命令：`DEEP_AGENT_RESOURCE_REPORT=... PYTHONPATH=src .venv/bin/python -m unittest tests.integration.test_structured_output_resources -v`。4 项全部通过；随后不覆盖测量报告的独立复跑同样 4/4（1.988 秒）。本机并发测量不是生产容量承诺。
