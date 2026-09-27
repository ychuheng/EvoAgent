# Beacon（M7 发布 holdout fixture）

发布集用的**留出**仓库，与 dev fixture 和 M6 holdout 都不同：一个素材采集与汇总管线。

- 入口：`src/beacon/cli.py`（`def main(`）
- 配置：`src/beacon/config.py`（`DEFAULT_ENCODING`、`RETRY_LIMIT`、`resolve_data_root`）
- 来源读取：`src/beacon/sources.py`（`load_source`、`discover`）
- 管线编排：`src/beacon/pipeline.py`（`run`、`count_words`、`average_words`）
- 输出渲染：`src/beacon/report.py`（`render`、`write_report`）
- 文本工具：`src/beacon/util.py`（`normalize_name`、`is_blank`、`slug`）
- 设计说明：`docs/pipeline.md`

**已知缺陷（发布集故意保留，供"找出失败原因"一类任务使用）**：

1. `tests/test_report_edges.py::test_skips_blank_rows`：`render()` 会为空行输出一个空字符串；
2. `tests/test_pipeline_stats.py::test_average_of_empty_is_zero`：`average_words([])` 会除零；
3. `tests/test_discover_nested.py::test_finds_nested_text_files`：`discover()` 不递归子目录；
4. `tests/test_util_names.py::test_strips_surrounding_spaces`：`normalize_name()` 不裁空格。

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
