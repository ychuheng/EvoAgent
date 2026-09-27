# Fieldnotes（开发样本）

这是 M0a/M1 的小型只读开发仓库，可以复制到临时目录后用于编辑实验。它不属于 M6 或 M7 的留出集。

入口是 `src/fieldnotes/cli.py` 的 `main`。`add` 命令把一条笔记写入 JSON 文件；`list` 命令读取同一文件，再由 `src/fieldnotes/render.py` 排版。持久化逻辑在 `src/fieldnotes/store.py`，基础行为由 `tests/test_fieldnotes.py` 检查。

在仓库根目录运行测试：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
