# Organize Lab（M5 整理 fixture）

供"授权目录整理"验收使用的小型仓库：`inbox/` 里混着 Markdown、PDF、坏文件与非 UTF-8 文本，
`notes/` 与 `reports/` 是整理目标，`reports/taken.pdf` 用来验证"目标已存在时不覆盖"。

整理规则示例：`inbox/*.md → notes/`、`inbox/*.pdf → reports/`。

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests
```
