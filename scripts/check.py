#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check.py — xqshare 代码自检脚本

写完/改完代码后运行，快速发现问题：
  1. 语法错误（SyntaxError）—— 全项目 .py 扫描（排除 .venv 等）【阻断】
  2. 导入错误（ImportError）—— import 主包冒烟【阻断】

用法：
    .venv/bin/python scripts/check.py

退出码：0 = 通过；1 = 存在语法/导入错误
"""

from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {'.git', '.venv', 'venv', 'build', 'dist', '__pycache__', '.pytest_cache'}

# 导入冒烟目标模块（不依赖 Windows 端 xqshare server 即可 import）
SMOKE_MODULES = [
    'xqshare',
    'xqshare.client',
    'xqshare.server',
    'xqshare.auth',
]


def collect_py_files() -> list:
    files = []
    for p in PROJECT_ROOT.rglob('*.py'):
        parts = p.relative_to(PROJECT_ROOT).parts
        if any(part in EXCLUDE_DIRS for part in parts):
            continue
        files.append(p)
    return sorted(files)


def check_syntax(files: list) -> tuple:
    errors = []
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            inner = getattr(e, 'exc_value', None) or e
            errors.append(f"{f.relative_to(PROJECT_ROOT)}: {inner}")
        except Exception as e:
            errors.append(f"{f.relative_to(PROJECT_ROOT)}: {e}")
    return len(errors), errors


def check_imports() -> tuple:
    errors = []
    for mod in SMOKE_MODULES:
        try:
            result = subprocess.run(
                [sys.executable, '-c', f'import {mod}'],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            errors.append(f"import {mod} → 超时（30s）")
            continue
        if result.returncode != 0:
            err_lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            detail = err_lines[-1] if err_lines else f'退出码 {result.returncode}'
            errors.append(f"import {mod} → {detail}")
    return len(errors), errors


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    print('=' * 60)
    print('xqshare 代码自检')
    print('=' * 60)

    files = collect_py_files()

    print(f'\n[1/2] 语法检查（{len(files)} 个文件）...')
    n_syn, syn_errs = check_syntax(files)
    if n_syn == 0:
        print('  ✓ 语法检查通过')
    else:
        print(f'  ✗ {n_syn} 个文件存在语法错误：')
        for e in syn_errs:
            print(f'    - {e}')

    print(f'\n[2/2] 导入冒烟（{len(SMOKE_MODULES)} 个模块）...')
    n_imp, imp_errs = check_imports()
    if n_imp == 0:
        print('  ✓ 导入冒烟通过')
    else:
        print(f'  ✗ {n_imp} 个模块导入失败：')
        for e in imp_errs:
            print(f'    - {e}')

    print('\n' + '=' * 60)
    total = n_syn + n_imp
    if total == 0:
        print('✅ 自检通过')
        print('=' * 60)
        return 0
    print(f'❌ 自检失败：{n_syn} 语法错误 + {n_imp} 导入错误')
    print('=' * 60)
    return 1


if __name__ == '__main__':
    sys.exit(main())
