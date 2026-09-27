# Ledger（M6 holdout fixture）

供 Skill 配对实验使用的**留出**仓库，与 `project-dev-notes` 不同家族、不同结构。
配置在 `ledger/config.py`，摄取在 `ledger/ingest.py`，归属规则在 `ledger/attribute.py`，
汇总在 `ledger/summary.py`，入口是 `ledger/cli.py`。

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
