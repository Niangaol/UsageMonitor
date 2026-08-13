# -*- coding: utf-8 -*-
"""paths.py — 统一路径解析（消除全项目硬编码绝对路径）。

- script_dir()：代码所在目录。frozen（PyInstaller onefile）时返回 exe 所在目录，
  避免把数据写到 _MEIPASS 临时解压目录（重启即丢失）。
- default_data_root()：数据根目录默认值 = script_dir()（config.json 的
  data_root 为空/相对路径时使用，语义：数据与程序同目录）。
"""

from __future__ import annotations

import os
import sys


def script_dir() -> str:
    """程序目录：脚本运行取脚本目录；打包 exe 取 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_data_root() -> str:
    """默认数据根目录（可移植：随程序位置变化，拷到任何目录/机器都能用）。"""
    return script_dir()
