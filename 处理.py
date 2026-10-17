# -*- coding: utf-8 -*-
import os
import json
import glob
import uuid
import shutil
import traceback
from copy import deepcopy

MODULE_NAME = "深度处理"
MODULE_ICON = "⚙️"
MODULE_DESC = "全能合一：取样优先样式(取不到用默认模板) + 静音 + 合轨 + 切视频 + 拉伸 + 黑底"

def render_config():
    import streamlit as st
    st.write("**📁 路径设置：**")
    projects = st.text_input("剪映草稿目录", value=r"D:\jy\JianyingPro Drafts", help="请输入剪映 Pro 的草稿根目录路径")

    st.write("**🎨 样式来源：**")
    mode = st.radio("样式来源", ["取样优先（取不到用默认模板）", "仅用默认模板"], horizontal=True)
    marker = st.text_input("取样标记文字（包含在手动调好背景的那条字幕中的文字）", value="including Type A")
    template = st.text_input("默认模板草稿名称", value='1-01 铁道车辆的定义及特点 (V1)')
    style_index = st.number_input("使用模板第几个样式（0起）", 0, 10, 1)

    st.write("**⬛ 黑底设置（取样成功时自动跳过，保留手动值）：**")
    col1, col2, col3 = st.columns(3)
    with col1:
        bg_alpha = st.slider("黑底不透明度", 0.0, 1.0, 1.0, 0.1)
    with col2:
        bg_size = st.slider("黑底大小", 0.05, 0.30, 0.14, 0.01)
    with col3:
        bg_radius = st.slider("黑底圆角", 0.0, 0.10, 0.0, 0.01)

    mode_key = "auto" if mode.startswith("取样") else "template"
    return {
        "projects": projects, 
        "mode": mode_key, 
        "marker": marker,
        "template": template, 
        "style_index": int(style_index),
        "bg_alpha": bg_alpha, 
        "bg_size": bg_size, 
        "bg_radius": bg_radius
    }

def run(log, config):
    PROJECTS = config["projects"]
    MODE = config["mode"]
    MARKER = config["marker"].strip()
    TEMPLATE = config["template"]
    STYLE_INDEX = config["style_index"]
    BG_COLOR = '#000000'
    BG_ALPHA = config["bg_alpha"]
    BG_SIZE = config["bg_size"]
    BG_RADIUS = config["bg_radius"]

    # 🛡️ 安全读取 JSON（兼容带 BOM 的 UTF-8 及各种中文编码）
    def read_json_safe(path):
        for enc in ['utf-8-sig', 'utf-8', 'gbk', 'gb18030']:
            try:
                with open(path, 'r', encoding=enc) as f:
                    return json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return json.load(f)

    # ---------- 1 静音原视频 ----------
    def mute_video(data):
        vids = {m.get('id'): m for m in data.get('materials', {}).get('videos', [])}
        n = 0
        for t in data.get('tracks', []):
            if t.get('type') != 'video': 
                continue
            for seg in t.get('segments', []):
                seg['volume'] = 0.0
                seg.setdefault('last_nonzero_volume', 1.0)
                m = vids.get(seg.get('material_id'))
                if isinstance(m, dict): 
                    m['volume'] = 0.0
                n += 1
        return n

    # ---------- 2 音频合一排 ----------
    def merge_audio_tracks(data):
        tracks = data.get('tracks', [])
        ats = [t for t in tracks if t.get('type') == 'audio']
        if len(ats) <= 1: 
            return 0
        segs = []
        for t in ats: 
            segs += t.get('segments', [])
        segs.sort(key=lambda s: s['target_timerange']['start'])
        for i in range(len(segs) - 1):
            tr = segs[i]['target_timerange']
            nxt = segs[i+1]['target_timerange']['start']
            if tr['start'] + tr['duration'] > nxt: 
                tr['duration'] = max(0, nxt - tr['start'])
        ats[0]['segments'] = [s for s in segs if s['target_timerange']['duration'] > 0]
        for t in ats[1:]: 
            tracks.remove(t)
        return len(ats) - 1

    # ---------- 3 按字幕切视频 ----------
    def split_video(data):
        tracks = data.get('tracks', [])
        cuts = set()
        for t in tracks:
            if t.get('type') == 'text':
                for seg in t.get('segments', []):
                    tr = seg.get('target_timerange', {})
                    s, d = tr.get('start', 0), tr.get('duration', 0)
                    cuts.add(s)
                    cuts.add(s + d)
        if not cuts: 
            return 0
        n = 0
        for t in tracks:
            if t.get('type') != 'video': 
                continue
            ns_list = []
            for seg in t.get('segments', []):
                src = seg.get('source_timerange', {})
                tgt = seg.get('target_timerange', {})
                S0, T0, D = src.get('start', 0), tgt.get('start', 0), tgt.get('duration', 0)
                pts = sorted(set([T0, T0 + D] + [c for c in cuts if T0 < c < T0 + D]))
                for a, b in zip(pts[:-1], pts[1:]):
                    ns = deepcopy(seg)
                    ns['id'] = str(uuid.uuid4())
                    off, dur = a - T0, b - a
                    ns['source_timerange'] = {'start': S0 + off, 'duration': dur}
                    ns['target_timerange'] = {'start': a, 'duration': dur}
                    ns_list.append(ns)
                if len(pts) > 2: 
                    n += 1
            t['segments'] = ns_list
        return n

    # ---------- 4 按音频拉伸视频 ----------
    def stretch_video(data):
        tracks = data.get('tracks', [])
        vt = next((t for t in tracks if t.get('type') == 'video'), None)
        au = [s for t in tracks if t.get('type') == 'audio' for s in t.get('segments', [])]
        if not vt or not au: 
            return 0
        vl = []
        for seg in sorted(vt['segments'], key=lambda s: s['target_timerange']['start']):
            tr = seg['target_timerange']
            vs, ve = tr['start'], tr['start'] + tr['duration']
            am = 0
            for a in au:
                at = a['target_timerange']
                as_, ae = at['start'], at['start'] + at['duration']
                if as_ >= vs and as_ < ve: 
                    am = max(am, ae)
            vl.append([seg, vs, ve, max(0, am - ve)])
        
        def sh(s): 
            return sum(ex for (_, _, ve, ex) in vl if ve <= s)
        
        st = 0
        for seg, vs, ve, ex in vl:
            tr = seg['target_timerange']
            tr['start'] = vs + sh(vs)
            tr['duration'] = (ve - vs) + ex
            if ex: 
                st += 1
        for t in tracks:
            if t.get('type') in ('audio', 'text'):
                for seg in t.get('segments', []):
                    tr = seg['target_timerange']
                    tr['start'] += sh(tr['start'])
        return st

    # ---------- 5 字幕拉伸到音频 ----------
    def stretch_text(data):
        au = [(s['target_timerange']['start'], s['target_timerange']['duration'])
              for t in data.get('tracks', []) if t.get('type') == 'audio' for s in t.get('segments', [])]
        if not au: 
            return 0
        c = 0
        for t in data.get('tracks', []):
            if t.get('type') == 'text':
                for seg in t.get('segments', []):
                    tr = seg['target_timerange']
                    best = min(au, key=lambda a: abs(a[0] - tr['start']))
                    if abs(best[0] - tr['start']) < 500000: 
                        tr['duration'] = best[1]
                        c += 1
        return c

    # ---------- 6 套样式（取样 或 默认模板，描边跟随样式走） ----------
    def apply_template(data, style_part, tmat):
        texts = data.get('materials', {}).get('texts', [])
        if not texts: 
            return 0
        for i, M in enumerate(texts):
            mc = json.loads(M['content']) if isinstance(M['content'], str) else M['content']
            text = mc.get('text', '')
            newc = deepcopy(style_part)
            newc['text'] = text
            st = newc.get('styles')
            if isinstance(st, dict) and 'range' in st: 
                st['range'] = [0, len(text)]
            if isinstance(st, list):
                for x in st:
                    if isinstance(x, dict) and 'range' in x: 
                        x['range'] = [0, len(text)]
            newM = deepcopy(tmat)
            newM['id'] = M['id']
            newM['content'] = json.dumps(newc, ensure_ascii=False)
            texts[i] = newM
        return len(texts)

    # ---------- 7 黑底（仅未取样时使用） ----------
    def add_background(data):
        texts = data.get('materials', {}).get('texts', [])
        for M in texts:
            M['background_alpha'] = BG_ALPHA
            M['background_color'] = BG_COLOR
            M['background_height'] = BG_SIZE
            M['background_horizontal_offset'] = 0.0
            M['background_round_radius'] = BG_RADIUS
            M['background_style'] = 1
            M['background_vertical_offset'] = 0.0
            M['background_width'] = BG_SIZE
            M['check_flag'] = (M.get('check_flag', 0) or 0) | 16
        return len(texts)

    # ================= 主流程 =================
    try:
        if not os.path.exists(PROJECTS):
            log(f"❌ 错误：剪映草稿目录不存在 -> {PROJECTS}")
            return

        style_part = None
        tmat = None
        sampled_from = None

        # ===== ① 取样：找手动调好的那条字幕 =====
        if MODE == "auto" and MARKER:
            log(f'🔍 开始取样：查找包含「{MARKER}」的字幕...')
            for p in sorted(glob.glob(os.path.join(PROJECTS, '*', 'draft_content.json'))):
                try:
                    td = read_json_safe(p)
                except Exception: 
                    continue
                for M in td.get('materials', {}).get('texts', []):
                    try:
                        tc = json.loads(M['content']) if isinstance(M.get('content'), str) else M.get('content', {})
                    except Exception: 
                        continue
                    if MARKER in tc.get('text', ''):
                        tmat = deepcopy(M)
                        style_part = {k: v for k, v in tc.items() if k != 'text'}
                        sampled_from = os.path.basename(os.path.dirname(p))
                        break
                if tmat: 
                    break
            if tmat:
                log(f'🎨 取样成功！使用草稿「{sampled_from}」里手动调好的字幕样式')
            else:
                log('⚠️ 取样未找到匹配字幕，回退默认模板')

        # ===== ② 默认模板 =====
        if style_part is None:
            tp = os.path.join(PROJECTS, TEMPLATE, 'draft_content.json')
            if os.path.exists(tp):
                td = read_json_safe(tp)
                tlist = td['materials']['texts']
                idx = STYLE_INDEX if len(tlist) > STYLE_INDEX else 0
                tmat = deepcopy(tlist[idx])
                tc = json.loads(tmat['content']) if isinstance(tmat['content'], str) else tmat['content']
                style_part = {k: v for k, v in tc.items() if k != 'text'}
                log(f'🎨 使用默认模板草稿「{TEMPLATE}」第 {idx+1} 个字幕样式')
            else:
                log(f'⚠️ 未找到模板草稿「{TEMPLATE}」，将跳过套样式步骤')

        sampled = sampled_from is not None

        # ===== ③ 批量处理所有草稿 =====
        draft_folders = [d for d in sorted(glob.glob(os.path.join(PROJECTS, '*'))) if os.path.isdir(d)]
        total_dirs = len(draft_folders)
        if total_dirs == 0:
            log("❌ 没有找到任何草稿文件夹。")
            return

        log(f'🚀 共发现 {total_dirs} 个草稿文件夹，开始批量处理...')
        
        success_count = 0
        for i, d in enumerate(draft_folders, 1):
            p = os.path.join(d, 'draft_content.json')
            if not os.path.exists(p): 
                continue
            try:
                data = read_json_safe(p)
                r0 = mute_video(data)
                r1 = merge_audio_tracks(data)
                r2 = split_video(data)
                r3 = stretch_video(data)
                r4 = stretch_text(data)
                r5 = apply_template(data, style_part, tmat) if style_part else 0
                r7 = 0 if sampled else add_background(data)
                
                # 备份原文件
                bak = p + '.bak_all'
                if not os.path.exists(bak): 
                    shutil.copy(p, bak)
                
                # 紧凑存储 JSON 加快写入速度
                with open(p, 'w', encoding='utf-8') as f: 
                    json.dump(data, f, ensure_ascii=False, indent=None)
                
                success_count += 1
                log(f'[{success_count}/{total_dirs}] {os.path.basename(d)} | 静音{r0} 合轨{r1} 切{r2} 拉伸{r3} 字幕{r4} 样式{r5} 黑底{r7}')
            except Exception as e:
                log(f'[跳过] {os.path.basename(d)} : {str(e)}')

        if sampled:
            log('ℹ️ 取样模式：背景/描边参数全部来自取样值，已自动跳过黑底滑块')
        log(f'🎉 处理完成！成功处理 {success_count}/{total_dirs} 个草稿。重新打开剪映查看效果。')
    except Exception:
        log('❌ 出错了：')
        log(traceback.format_exc())
