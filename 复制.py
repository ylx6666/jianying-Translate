# -*- coding: utf-8 -*-
import base64
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

MODULE_NAME = "一键复制"
MODULE_ICON = "📄"
MODULE_DESC = "网页输入任意文字 → 一键复制到剪贴板；或下载内嵌该文字的「一键复制.bat」。"

PROMPT_CN = """你是专业字幕校对专家。下面的SRT字幕由电脑语音识别生成，含有错别字与识别错误，请校对修正，严格规则：
所有序号行、时间轴行（含 -- 的行）原样保留，一个字符都不许改；
只修正文字行中的错别字、同音字、近音字等识别错误，结合铁道车辆铁路专业语境判断（如 转向架、车钩、制动机、轮对 等术语必须准确）；
保持原句结构和说话风格，只做纠错，不重写、不翻译、不增删内容；
数字、型号、代号一律保持原样；
条目数量不变，不合并、不拆分、不增删；
不要代码块标记、不要解释、不要引号；
纯文本输出"""

PROMPT_EN = """你是专业字幕翻译。下面是一个SRT字幕文件（中文由语音识别生成，可能含错别字），请先结合铁道车辆铁路专业语境正确理解，再翻译成英文，严格规则：
所有序号行、时间轴行（含 -- 的行）原样保留，一个字符都不许改；
只翻译文字行为英文；
条目数量不变，不合并、不拆分、不增删；
不要代码块标记、不要解释、不要引号；
纯文本输出；
所有数字、型号、代号必须翻译成英文读法，不保留阿拉伯数字（如 1998 - nineteen ninety eight，C70 - C seventy，3号 - No. Three）。"""

PRESETS = {
    "中文纠错提示词": PROMPT_CN,
    "英文翻译提示词": PROMPT_EN,
}


def _set_state_text(value: str) -> None:
    """按钮回调：更新文本框内容。"""
    st.session_state["copy_text"] = value


def build_bat(text: str) -> str:
    """
    生成内嵌文字的 .bat。

    优化：
    - 文字先 Base64 编码，避免中文、引号、换行、特殊符号破坏脚本；
    - Base64 分块写入临时文件，避免超长命令行；
    - 由 PowerShell 解码并写入剪贴板；
    - bat 内提示使用英文，避免 CMD 中文编码问题。
    """
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")

    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "title Copy Text",
        "setlocal EnableExtensions",
        'set "B64_FILE=%TEMP%\\copy_text_%RANDOM%%RANDOM%.b64"',
    ]

    if not b64:
        lines.append('type nul > "%B64_FILE%"')
    else:
        chunk_size = 4000
        chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]

        for idx, chunk in enumerate(chunks):
            redirect = ">" if idx == 0 else ">>"
            lines.append(f'{redirect} "%B64_FILE%" echo {chunk}')

    ps_cmd = (
        "$b64 = Get-Content -Raw -LiteralPath $env:B64_FILE; "
        "$b64 = $b64 -replace '\\s', ''; "
        "Set-Clipboard -Value ([System.Text.Encoding]::UTF8.GetString("
        "[System.Convert]::FromBase64String($b64))); "
        "Remove-Item -LiteralPath $env:B64_FILE -Force -ErrorAction SilentlyContinue"
    )

    lines.extend([
        f'powershell -NoProfile -Command "{ps_cmd}"',
        "if %errorlevel% neq 0 (",
        "    echo Copy failed.",
        "    timeout /t 3 /nobreak >nul",
        "    exit /b 1",
        ")",
        "echo Copied to clipboard. Press Ctrl+V.",
        "timeout /t 1 /nobreak >nul",
        "endlocal",
    ])

    return "\r\n".join(lines) + "\r\n"


def _copy_windows(text: str) -> None:
    """Windows：通过 PowerShell 设置剪贴板，使用临时文件避免超长命令。"""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    tmp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".b64",
            delete=False,
            encoding="ascii",
        ) as f:
            f.write(b64)
            tmp_path = f.name

        ps_path = tmp_path.replace("'", "''")
        ps_cmd = (
            f"$b64 = Get-Content -Raw -LiteralPath '{ps_path}';"
            "$b64 = $b64 -replace '\\s', '';"
            "Set-Clipboard -Value ([System.Text.Encoding]::UTF8.GetString("
            "[System.Convert]::FromBase64String($b64)))"
        )

        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="ignore")
            raise RuntimeError(err or "PowerShell 执行失败")
    finally:
        if tmp_path:
            try:
                p = Path(tmp_path)
                if p.exists():
                    p.unlink()
            except OSError:
                pass


def _copy_mac(text: str) -> None:
    """macOS：使用 pbcopy。"""
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def _copy_linux(text: str) -> None:
    """Linux：优先尝试 xclip / xsel / wl-copy。"""
    cmds = [
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["wl-copy"],
    ]
    last_error = None

    for cmd in cmds:
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            last_error = e

    raise RuntimeError("未找到可用剪贴板工具，请安装 xclip、xsel 或 wl-copy") from last_error


def copy_text_to_clipboard(text: str) -> None:
    """跨平台复制到剪贴板。"""
    if sys.platform.startswith("win"):
        _copy_windows(text)
    elif sys.platform == "darwin":
        _copy_mac(text)
    else:
        _copy_linux(text)


def render_config():
    if "copy_text" not in st.session_state:
        st.session_state["copy_text"] = ""

    st.write("✏️ 要复制的文字（支持多行、中文、代码、特殊符号）：")

    c1, c2, c3 = st.columns(3)

    c1.button(
        "⚡ 中文纠错提示词",
        key="preset_cn",
        on_click=_set_state_text,
        args=(PRESETS["中文纠错提示词"],),
    )

    c2.button(
        "⚡ 英文翻译提示词",
        key="preset_en",
        on_click=_set_state_text,
        args=(PRESETS["英文翻译提示词"],),
    )

    c3.button(
        "🗑️ 清空",
        key="preset_clear",
        on_click=_set_state_text,
        args=("",),
    )

    text = st.text_area(
        "文字内容",
        height=220,
        key="copy_text",
    )

    total_chars = len(text)
    total_lines = len(text.splitlines())
    non_empty_lines = len([line for line in text.splitlines() if line.strip()])

    st.caption(
        f"📊 共 {total_chars} 个字符｜{total_lines} 行（非空 {non_empty_lines} 行）"
    )

    has_text = bool(text.strip())

    st.download_button(
        "⬇️ 下载「一键复制.bat」（内嵌上面文字，双击即复制）",
        data=build_bat(text).encode("utf-8"),
        file_name="一键复制.bat",
        mime="application/octet-stream",
        use_container_width=True,
        disabled=not has_text,
    )

    return {"text": text}


def run(log, config):
    text = (config or {}).get("text", "")

    if not text.strip():
        log("⚠️ 文字为空，没有可复制的内容")
        return

    try:
        copy_text_to_clipboard(text)
        st.toast("✅ 已复制到剪贴板！", icon="📄")
        log("✅ 已复制到剪贴板！去任意地方 Ctrl+V 粘贴即可。")
    except Exception as e:
        log(f"❌ 复制失败：{e}")