# CLAUDE.md

本文件为 Claude Code / 其他 AI 助手在本仓库工作时的指引。

## 项目概述

GNPy —— 开源的 DWDM 光网络路由规划与传输性能仿真库。

- 上游项目：[Telecominfraproject/oopt-gnpy](https://github.com/Telecominfraproject/oopt-gnpy)
- 本仓库：`origin git@github.com:ganlin668/oopt-gnpy.git`（默认分支 `master`），是 BPLab 在上游基础上的**扩展分支**
- 语言/版本：纯 Python，`python_requires = >=3.11,<3.13`；本地开发环境为 `.venv`（Python 3.12）
- 打包：`pbr` + `setuptools`（`pyproject.toml` / `setup.cfg`），依赖见 `setup.cfg` 的 `[options] install_requires`

## 最高优先级约束：只新增，不删改

与上游保持低冲突是本分支的第一原则，详细规则见 [.claude/rules/bplab-add-on.md](.claude/rules/bplab-add-on.md)：

1. **禁止**修改、删除或重命名 `gnpy/` 下上游已有的任何文件（`gnpy/core/`、`gnpy/tools/`、`gnpy/topology/`、`gnpy/yang/`、`gnpy/example-data/`）。
2. 新功能一律写到自建的 `gnpy/bplab/` 包中，通过 `from gnpy.bplab... import ...` 复用上游能力；上游有 bug 时在 `gnpy/bplab/` 内包装/改写，不改上游源文件。
3. 新测试一律放在 `tests/bplab/`，不修改 `tests/` 下已有的测试文件；**不要**新增 `tests/bplab/__init__.py`。
4. 新增 `.py` 文件必须带仓库标准文件头（见下），否则 CI 的 `test_file_headers` 会失败。

## 目录结构

```
gnpy/
  core/            # 核心模型：elements.py 网络元素、info.py 光谱信息、equipment.py 设备库、parameters.py 参数
  tools/           # CLI 与 IO：cli_examples.py、json_io.py、convert.py（xls/yang 格式转换）、plots.py
  topology/        # 拓扑与请求：request.py 路径计算、spectrum_assignment.py、topology_parameters.py
  yang/            # YANG 模型与 xls/yang 转换工具
  bplab/           # 本地扩展包（新增代码写这里）：utils.py 频点/栅格工具
  example-data/    # 示例网络、设备库(eqpt_config*.json)、EDFA 模型
tests/
  bplab/           # 本地新增测试
  data/            # 测试输入与期望输出
  *.py             # 上游测试
scripts/ganlin/test_gnpy/   # 手动实验脚本（test.py 走拓扑 JSON，test2.py 用 API 手工构造链路）
.trae/documents/   # 需求/改造说明文档
.claude/rules/     # AI 助手的仓库级规则
docs/              # Sphinx 文档
```

## 常用命令

所有命令在仓库根目录、使用 `.venv` 执行（PowerShell）：

```powershell
# 本地新增测试 + doctest
.\.venv\Scripts\python.exe -m pytest tests/bplab gnpy/bplab -q

# 新增 .py 后必跑：文件头合规
.\.venv\Scripts\python.exe -m pytest tests/test_opensource_compliancy.py::test_file_headers -q

# 静态检查
.\.venv\Scripts\python.exe -m flake8 gnpy/bplab tests/bplab

# 全量测试（较慢，CI 等价）
.\.venv\Scripts\python.exe -m pytest -vv

# 内置 CLI 示例（已安装在 .venv\Scripts\）
.\.venv\Scripts\gnpy-example-data.exe          # 打印示例数据目录
.\.venv\Scripts\gnpy-transmission-example.exe  # 传输性能仿真
.\.venv\Scripts\gnpy-path-request.exe          # 路径计算（PCE）
.\.venv\Scripts\gnpy-convert-xls.exe           # xls -> json
.\.venv\Scripts\gnpy-convert-yang.exe          # 传统 json <-> yang json
```

`tox`（CI 使用）：`tox -e py311`、`tox -e py312-cover`、`tox -e linters`、`tox -e docs`。

## 代码风格

- flake8 配置见 [tox.ini](tox.ini)：`max-line-length = 120`，`max-complexity = 15`，`ignore = N806 W503 C901`。
- 字符串以单引号为主；numpy 沿用 `from numpy import <符号>` 的扁平导入写法。
- 每个新增 `.py` 文件开头必须包含（四项缺一不可，`test_file_headers` 会校验）：
  ```python
  # -*- coding: utf-8 -*-

  # SPDX-License-Identifier: BSD-3-Clause
  # <本文件的简短说明>
  # Copyright (C) 2025 Telecom Infra Project and GNPy contributors
  # see AUTHORS.rst for a list of contributors
  ```
- `pytest.ini` 全局启用了 `addopts = --doctest-modules`，**模块中的 `>>>` doctest 会被真实执行**；给 `gnpy/bplab/` 的纯函数补 doctest 时要保证结果正确。
- 本地扩展代码的注释/文档字符串沿用仓库现状，使用中文。
- 不要对既有文件做无关的整文件重排/格式化。

## CI 与提交

- CI 配置：[.github/workflows/main.yml](.github/workflows/main.yml)（tox py311 / py312-cover / docs）+ [.github/workflows/flake8.yml](.github/workflows/flake8.yml)。
- 两个容易失败的合规测试：
  - `tests/test_opensource_compliancy.py::test_file_headers` —— 检查文件头。
  - `tests/test_opensource_compliancy.py::test_commit_authors_in_author_rst` —— 近 365 天提交者邮箱必须出现在 [AUTHORS.rst](AUTHORS.rst) 中。
- 提交信息沿用 Conventional Commits 风格（`feat:` / `fix:` / `docs:` / `refactor:` / `chore:`），描述可用中文。
- 上游补丁走 Gerrit（见 `.gitreview`）；本仓库推送到 `origin`。

## 注意事项

- 修改上游文件会直接造成与上游的合并冲突 —— 这是本分支最忌讳的操作，宁可复制到 `gnpy/bplab/` 实现。
- 运行仿真/测试前确认 `gnpy` 是从本仓库（可编辑安装）导入，而非 site-packages 中的 PyPI 版本。
- `tests/invocation/` 下是大量 CLI 集成测试的输入/基准目录，改动 CLI 行为时需一并关注。
