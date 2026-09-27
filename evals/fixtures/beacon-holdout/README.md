# Beacon（M7 发布 holdout fixture）

发布集用的**留出**仓库，与 dev fixture 和 M6 holdout 都不同：一个采集管线，
管线编排在 `beacon/pipeline.py`，来源在 `beacon/sources.py`，输出在 `beacon/report.py`，
入口是 `beacon/cli.py`。测试在 `tests/test_beacon.py`。

`beacon/sources.py` 里的 `load_source` 会读取整个文件到内存；大文件应由流水线逐块处理。
