# -*- coding: utf-8 -*-

import os
import re
import subprocess
import glob
import json
import uuid
import time
import shutil


MODULE_NAME = "导入视频"
MODULE_ICON = "📥"
MODULE_DESC = "将项目根目录「视频」文件夹里的 MP4/MOV 视频批量导入剪映，自动生成同名草稿"


def get_root_dir():
    """
    获取项目根目录。

    规则：
    1. 如果本文件在 pages / modules / tools / scripts / plugins 等子目录里，
       根目录取上一级。
    2. 如果上一级目录里已经有「视频」文件夹，也认为上一级是根目录。
    3. 否则默认认为当前文件所在目录就是根目录。
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)

    sub_dir_names = {
        "pages",
        "modules",
        "scripts",
        "tools",
        "plugins",
        "functions"
    }

    if os.path.basename(current_dir).lower() in sub_dir_names:
        return parent_dir or current_dir

    if parent_dir and os.path.isdir(os.path.join(parent_dir, "视频")):
        return parent_dir

    return current_dir


def get_default_video_dir():
    """
    默认视频文件夹：项目根目录/视频
    """
    return os.path.join(get_root_dir(), "视频")


def render_config():
    import streamlit as st

    default_video_dir = get_default_video_dir()

    # 如果根目录下还没有“视频”文件夹，先自动创建，方便用户直接放视频
    try:
        os.makedirs(default_video_dir, exist_ok=True)
    except Exception:
        pass

    st.write("**📁 路径设置：**")

    projects = st.text_input(
        "剪映草稿目录",
        value=r"D:\jy\JianyingPro Drafts"
    )

    ffmpeg = st.text_input(
        "FFmpeg 路径",
        value=os.path.normpath(os.path.join(get_root_dir(), "ffmpeg.exe"))
    )

    st.info(f"默认读取根目录视频文件夹：{default_video_dir}")

    video_dirs_str = st.text_area(
        "视频文件夹（每行一个）",
        value=default_video_dir
    )

    video_dirs = [v.strip() for v in video_dirs_str.split('\n') if v.strip()]

    skip = st.checkbox("跳过已存在的草稿", value=True)

    return {
        "projects": projects.strip(),
        "ffmpeg": ffmpeg.strip(),
        "video_dirs": video_dirs,
        "skip": skip,
    }


def run(log, config):
    PROJECTS = config.get("projects", "").strip()
    FFMPEG = config.get("ffmpeg", "").strip()
    VIDEO_DIRS = config.get("video_dirs", [])
    SKIP_EXISTING = config.get("skip", True)

    # 如果没有填写视频文件夹，默认使用根目录/视频
    if not VIDEO_DIRS:
        VIDEO_DIRS = [get_default_video_dir()]

    try:
        import pyJianYingDraft as jy
    except ImportError:
        log("❌ 未安装 pyJianYingDraft，请运行: pip install pyJianYingDraft")
        return

    if not PROJECTS:
        log("❌ 未设置剪映草稿目录")
        return

    try:
        os.makedirs(PROJECTS, exist_ok=True)
    except Exception as e:
        log(f"❌ 创建剪映草稿目录失败：{PROJECTS}，{e}")
        return

    FFMPEG_OK = bool(FFMPEG and os.path.exists(FFMPEG))

    if not FFMPEG_OK:
        log(f"⚠️ 未找到 ffmpeg.exe：{FFMPEG}")
        log("⚠️ MP4 仍可尝试导入，但 MOV 无法自动转码，视频时长可能使用默认值。")

    def clean_name(s):
        """
        清理文件名，避免剪映草稿名出现非法字符。
        """
        s = s.strip()

        while s and s[-1] in ' .':
            s = s[:-1]

        for ch in '\\/:*?"<>|':
            s = s.replace(ch, "_")

        return s or "未命名"

    def get_dur_us(path):
        """
        用 ffmpeg 获取视频时长，单位微秒。
        如果失败或没有 ffmpeg，返回默认 10 秒。
        """
        if not FFMPEG_OK:
            return 10_000_000

        result = subprocess.run(
            [FFMPEG, '-i', path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        err = result.stderr.decode('utf-8', errors='ignore')

        m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', err)

        if m:
            return int(
                (
                    int(m.group(1)) * 3600
                    + int(m.group(2)) * 60
                    + float(m.group(3))
                ) * 1_000_000
            )

        return 10_000_000

    def fix_meta(draft_dir, name):
        """
        修正剪映草稿 meta 信息。
        """
        meta_path = os.path.join(draft_dir, 'draft_meta_info.json')

        if not os.path.exists(meta_path):
            return

        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)

        now = int(time.time() * 1_000_000)

        meta['draft_name'] = name
        meta['draft_id'] = str(uuid.uuid4())
        meta['draft_root_dir'] = draft_dir
        meta['tm_draft_create'] = now
        meta['tm_draft_modified'] = now

        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False)

    def mov_to_mp4(mov_path):
        """
        将 MOV 转成同名 MP4。
        优先尝试 -c copy，失败后再重编码。
        """
        if not FFMPEG_OK:
            return None

        dir_name = os.path.dirname(mov_path)
        base_name = clean_name(os.path.splitext(os.path.basename(mov_path))[0])
        mp4_path = os.path.join(dir_name, base_name + '.mp4')

        if os.path.exists(mp4_path):
            return mp4_path

        log('  [转换] ' + os.path.basename(mov_path))

        r = subprocess.run(
            [
                FFMPEG,
                '-hide_banner',
                '-loglevel', 'error',
                '-y',
                '-i', mov_path,
                '-c', 'copy',
                mp4_path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        if r.returncode != 0:
            r = subprocess.run(
                [
                    FFMPEG,
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-y',
                    '-i', mov_path,
                    '-c:v', 'libx264',
                    '-crf', '20',
                    '-preset', 'veryfast',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    mp4_path
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            if r.returncode != 0:
                return None

        return mp4_path

    try:
        folder = jy.DraftFolder(PROJECTS)
    except Exception as e:
        log(f"❌ 打开剪映草稿目录失败：{PROJECTS}，{e}")
        return

    total_new = 0
    total_skip = 0

    for vd in VIDEO_DIRS:
        vd = vd.strip()

        if not vd:
            continue

        # 如果视频文件夹不存在，自动创建
        if not os.path.isdir(vd):
            try:
                os.makedirs(vd, exist_ok=True)
                log(f"ℹ️ 视频文件夹不存在，已自动创建：{vd}")
            except Exception as e:
                log(f"⚠️ 视频文件夹不存在且无法创建：{vd}，{e}")
                continue

        movs = glob.glob(os.path.join(vd, '*.mov'))
        mp4s_before = glob.glob(os.path.join(vd, '*.mp4'))

        log(f"📂 {vd} -> 发现 {len(movs)} 个 mov，{len(mp4s_before)} 个 mp4")

        # 先把 MOV 转成 MP4
        if movs:
            if FFMPEG_OK:
                for mv in sorted(movs):
                    mov_to_mp4(mv)
            else:
                log("⚠️ 发现 MOV 文件，但没有可用 ffmpeg，跳过 MOV 转换。")

        log("🚀 生成剪映草稿...")

        for video in sorted(glob.glob(os.path.join(vd, '*.mp4'))):
            base = clean_name(os.path.splitext(os.path.basename(video))[0])

            if not base:
                continue

            draft_dir = os.path.join(PROJECTS, base)

            if SKIP_EXISTING and os.path.exists(os.path.join(draft_dir, 'draft_content.json')):
                total_skip += 1
                continue

            if os.path.isdir(draft_dir):
                try:
                    shutil.rmtree(draft_dir)
                except Exception:
                    pass

            dur = get_dur_us(video)
            safe_dur = max(100_000, dur - 100_000)

            log(f"  📹 {base}，时长: {dur / 1_000_000:.1f} 秒")

            try:
                script = folder.create_draft(base, 1920, 1080, 30, allow_replace=True)

                track_ref = script.append_track(
                    jy.TrackSpec(jy.TrackType.video)
                )

                script.add_segment(
                    jy.VideoSegment(video, jy.trange(0, safe_dur)),
                    track_ref
                )

                script.save()

                fix_meta(draft_dir, base)

                total_new += 1

                log(f"  [✅] {base}")

            except Exception as e:
                log(f"  [❌] {base} 生成草稿失败：{e}")

    log(f"📊 合计: 新建 {total_new} 个，跳过已存在 {total_skip} 个")

    if total_new == 0:
        log("⚠️ 没有新建草稿：请检查根目录「视频」文件夹里是否有 MP4，或剪映草稿目录是否正确。")