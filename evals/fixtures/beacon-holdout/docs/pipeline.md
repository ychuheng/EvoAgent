# 管线设计说明

1. `discover(data_root)` 找出数据根下的文本来源；
2. `load_source(path)` 一次读入整个文件（大文件应改为逐块读取）；
3. `count_words(text)` 统计词数；
4. `write_report(path, rows)` 把 `名称: 词数` 写进 `report.txt`。

数据目录：`data/` 下现有 `notes.txt`、`second.txt`、`empty.txt`、`archive/legacy.txt`、
`legacy-gb18030.txt`、`broken.pdf`、`inbox.txt`。其中 `archive/` 是嵌套子目录，
`legacy-gb18030.txt` 不是 UTF-8，`broken.pdf` 是一个损坏的 PDF。
