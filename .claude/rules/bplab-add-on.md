# BPLab 扩展开发规则（本地 oopt-gnpy 分支）

本项目基于上游 [oopt-gnpy](https://github.com/Telecominfraproject/oopt-gnpy)。为与上游远程仓库保持低冲突，本地所有改动遵循**只新增、不删改**。

## 1. 不修改 gnpy 原始代码

- **禁止**修改、删除或重命名 `gnpy/` 下上游已有的任何文件，包括 `gnpy/core/`、`gnpy/tools/`、`gnpy/topology/`、`gnpy/yang/`、`gnpy/example-data/`。
- 需要新功能或需要调整既有行为时，一律写到自建的 `gnpy/bplab/` 包中（当前为 `gnpy/bplab/__init__.py`、`gnpy/bplab/utils.py`），通过 `from gnpy.bplab... import ...` 复用上游能力。
- 上游代码有 bug 或能力不足时：在 `gnpy/bplab/` 里包装/改写，**不改上游源文件**。

## 2. 新增测试放在 tests/bplab/

- 新增的 pytest 用例一律放在 `tests/bplab/`（例如 `tests/bplab/test_utils.py`），**不修改** `tests/` 下已有的测试文件。
- 不新增 `tests/bplab/__init__.py`（与既有 `tests/data/` 的约定保持一致）。

## 3. 新增 .py 文件必须带仓库标准文件头

`tests/test_opensource_compliancy.py::test_file_headers` 会扫描 `gnpy/**/*.py` 与 `tests/**/*.py`，排除名单只有 `gnpy/__init__.py`、`gnpy/core/__init__.py`、`gnpy/tools/__init__.py`、`gnpy/topology/__init__.py`、`tests/__init__.py`。因此**每个新增的 .py 文件**（含 `gnpy/bplab/__init__.py`）开头必须是：

```python
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: BSD-3-Clause
# <本文件的简短说明>
# Copyright (C) 2025 Telecom Infra Project and GNPy contributors
# see AUTHORS.rst for a list of contributors
```

缺少上述四项（coding、SPDX、Copyright、AUTHORS 引用）中任意一项，CI 的 `test_file_headers` 就会失败。

## 4. 遵守仓库既有风格

- flake8 行宽上限 120（`tox.ini` 的 `[flake8] max-line-length = 120`），字符串以单引号为主。
- `gnpy/bplab/` 中的纯函数请补 `>>>` doctest：`pytest.ini` 已全局启用 `addopts = --doctest-modules`，doctest 会被真实执行。
- numpy 沿用 `from numpy import <符号>` 的扁平导入写法。

## 5. 验证命令

```powershell
# 新增的单元测试 + doctest
.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q

# 文件头合规（新增 .py 后必跑）
.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q

# 静态检查
.\.venv\Scripts\python.exe -m flake8 gnpy/bplab tests/bplab
```

> 注：`.venv` 需按 `setup.cfg` 的 `[options.extras_require] tests` 安装 `pytest`、`flake8`。
