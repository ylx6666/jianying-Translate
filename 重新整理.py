# -*- coding: utf-8 -*-
import os, re, json, glob, uuid, shutil, traceback

MODULE_NAME = "一键重整"
MODULE_ICON = "🛠️"
MODULE_DESC = "三合一：①去除声音 + ②删旧字幕 + ③注入新字幕(带黑底)，勾选后按顺序一键完成"

def render_config():
    import streamlit as st
    st.write("**📁 路径设置：**")
    projects = st.text_input("剪映草稿目录", value=r"D:\jy\JianyingPro Drafts")
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    srt_dir = st.text_input("SRT 字幕目录（②③使用，同名匹配草稿）", value=parent_dir)

    st.write("**🧰 执行步骤（勾选要执行的操作，按 ①→③ 顺序）：**")
    do_noaudio = st.checkbox("① 去除所有音乐/配音", value=True)
    do_delsub = st.checkbox("② 删除旧字幕", value=True)
    do_addsub = st.checkbox("③ 注入新字幕（带黑底@底部）", value=True)
    pos_y = st.slider("字幕Y轴位置（仅③使用）", -1.0, 0.0, -0.80, 0.01)

    return {"projects": projects, "srt_dir": srt_dir,
            "do_noaudio": do_noaudio, "do_delsub": do_delsub, "do_addsub": do_addsub,
            "pos_y": pos_y}

def run(log, config):
    PROJECTS = config["projects"]
    SRT_DIR = config["srt_dir"]
    POS_X = 0.0
    POS_Y = config["pos_y"]

    def find_draft(name):
        p = os.path.join(PROJECTS, name, 'draft_content.json')
        if os.path.exists(p): return p
        if name.endswith('_en'):
            p2 = os.path.join(PROJECTS, name[:-3], 'draft_content.json')
            if os.path.exists(p2): return p2
        return None

    # ═══════════ ① 去除所有音乐/配音 ═══════════
    def step_noaudio():
        log('🔇  去除所有音乐/配音...')
        total = 0
        for d in glob.glob(os.path.join(PROJECTS, '*')):
            if not os.path.isdir(d): continue
            p = os.path.join(d, 'draft_content.json')
            if not os.path.exists(p): continue
            try:
                with open(p, encoding='utf-8') as f: data = json.load(f)
            except Exception: continue
            audio_ids = set()
            for t in data.get('tracks', []):
                if t.get('type') == 'audio':
                    for seg in t.get('segments', []): audio_ids.add(seg.get('material_id'))
            old_tracks = data.get('tracks', [])
            data['tracks'] = [t for t in old_tracks if t.get('type') != 'audio']
            removed_tracks = len(old_tracks) - len(data['tracks'])
            mats = data.setdefault('materials', {})
            old_audios = mats.get('audios', [])
            mats['audios'] = [a for a in old_audios if a.get('id') not in audio_ids]
            removed_mats = len(old_audios) - len(mats['audios'])
            if removed_tracks or removed_mats:
                bak = p + '.bak_nomusic'
                if not os.path.exists(bak): shutil.copy(p, bak)
                with open(p, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
                total += 1
                log(f'  [✅ 删 {removed_tracks} 条音频轨 / {removed_mats} 个素材] {os.path.basename(d)}')
        log(f'📊 ① 完成：共处理 {total} 个草稿，只剩静音画面+字幕')

    # ═══════════ ② 删除旧字幕 ═══════════
    def step_delsub():
        log('🗑️ ② 删除旧字幕...')
        total = 0
        for srt in sorted(glob.glob(os.path.join(SRT_DIR, '*.srt'))):
            name = os.path.splitext(os.path.basename(srt))[0]
            p = find_draft(name)
            if not p:
                log(f'  [跳过] {name}（没有同名草稿）'); continue
            with open(p, encoding='utf-8') as f: data = json.load(f)
            old_text_ids = set()
            for t in data.get('tracks', []):
                if t.get('type') == 'text':
                    for seg in t.get('segments', []):
                        if seg.get('material_id'): old_text_ids.add(seg.get('material_id'))
            original_track_count = len(data.get('tracks', []))
            data['tracks'] = [t for t in data.get('tracks', []) if t.get('type') != 'text']
            removed_tracks = original_track_count - len(data['tracks'])
            mats = data.get('materials', {})
            removed_mats = 0
            if 'texts' in mats:
                original_mat_count = len(mats['texts'])
                mats['texts'] = [m for m in mats['texts'] if m.get('id') not in old_text_ids]
                removed_mats = original_mat_count - len(mats['texts'])
            bak = p + '.bak_del_sub'
            if not os.path.exists(bak): shutil.copy(p, bak)
            with open(p, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
            total += 1
            log(f'  [✅ 删 {removed_tracks} 条字幕轨 / {removed_mats} 个素材] {name}（音频/视频保留）')
        log(f'📊 ② 完成：共清理 {total} 个草稿')

    # ═══════════ ③ 注入新字幕（带黑底） ═══════════
    def to_us(t):
        a, b = t.split(','); h, m, s = a.split(':')
        return (int(h)*3600 + int(m)*60 + int(s))*1000000 + int(b)*1000

    def parse_srt(path):
        try:
            txt = open(path, encoding='utf-8-sig').read()
        except UnicodeDecodeError:
            txt = open(path, encoding='gbk').read()
        out = []
        pattern = r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\r?\n(.*?)(?=\r?\n\s\r?\n|\Z)'
        for m in re.finditer(pattern, txt, re.S):
            t1 = to_us(m.group(1).replace('.', ',')); t2 = to_us(m.group(2).replace('.', ','))
            out.append((t1, t2, ' '.join(m.group(3).split())))
        return [x for x in out if x[1] > x[0] and x[2]]

    def make_mat(mid, text):
        content = {
            "text": text,
            "styles": [{
                "fill": {"alpha": 1.0, "content": {"solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
                "range": [0, len(text)], "size": 5.0,
                "stroke": {"alpha": 1.0, "content": {"solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}}, "width": 0.2},
                "strokes": [{"content": {"solid": {"color": [0.0, 0.0, 0.0]}}, "width": 0.08}],
                "bold": False, "italic": False, "underline": False,
                "letter_spacing": 0.0, "line_spacing": 0.02}]
        }
        return {
            "add_type": 2, "alignment": 1,
            "background_alpha": 1.0, "background_color": "#000000",
            "background_height": 0.14, "background_horizontal_offset": 0.0,
            "background_round_radius": 0.0, "background_style": 1,
            "background_vertical_offset": 0.0, "background_width": 0.14,
            "base_content": " ", "bold_width": 0.0,
            "border_alpha": 1.0, "border_color": "#000000", "border_width": 0.08,
            "caption_template_info": {"category_id": " ", "category_name": " ", "effect_id": " ",
                "is_new": False, "path": " ", "request_id": " ", "resource_id": " ",
                "resource_name": " ", "source_platform": 0},
            "check_flag": 55, "combo_info": {"text_templates": []},
            "content": json.dumps(content, ensure_ascii=False),
            "fixed_height": -1.0, "fixed_width": -1.0,
            "font_category_id": " ", "font_category_name": " ", "font_id": " ", "font_name": " ",
            "font_path": "D:/llqxz/JianyingPro5.9版本/JianyingPro/5.9.0.11632/Resources/Font/SystemFont/zh-hans.ttf",
            "font_resource_id": " ", "font_size": 15.0, "font_source_platform": 0,
            "font_team_id": " ", "font_title": "none", "font_url": " ", "fonts": [],
            "force_apply_line_max_width": False, "global_alpha": 1.0, "group_id": " ",
            "has_shadow": False, "id": mid, "initial_scale": 1.0, "inner_padding": -1.0,
            "is_rich_text": False, "italic_degree": 0, "ktv_color": " ", "language": " ",
            "layer_weight": 1, "letter_spacing": 0.0, "line_feed": 1, "line_max_width": 0.82,
            "line_spacing": 0.02, "multi_language_current": "none", "name": " ",
            "original_size": [], "preset_category": " ", "preset_category_id": " ",
            "preset_has_set_alignment": False, "preset_id": " ", "preset_index": 0,
            "preset_name": " ", "recognize_task_id": " ", "recognize_type": 0,
            "relevance_segment": [], "shadow_alpha": 0.0, "shadow_angle": -45.0,
            "shadow_color": "#000000", "shadow_distance": 5.0,
            "shadow_point": {"x": 0.0, "y": 0.0}, "shadow_smoothing": 0.45,
            "shape_clip_x": False, "shape_clip_y": False, "source_from": " ",
            "style_name": " ", "sub_type": 0, "subtitle_keywords": None,
            "subtitle_template_original_fontsize": 0.0, "text_alpha": 1.0,
            "text_color": "#ffffff", "text_curve": {"angle": 0.0, "enable": False},
            "text_preset_resource_id": " ", "text_size": 30, "text_to_audio_ids": [],
            "tts_auto_update": False, "type": "subtitle", "typesetting": 0,
            "underline": False, "underline_offset": 0.22, "underline_width": 0.05,
            "use_effect_default_color": True,
            "words": {"end_time": [], "start_time": [], "text": []}
        }

    def make_seg(mid, start, dur):
        return {
            "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False}, "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": POS_X, "y": POS_Y}},
            "common_keyframes": [], "extra_material_refs": [],
            "id": str(uuid.uuid4()), "material_id": mid, "render_index": 0,
            "responsive_layout": {"enable": False, "horizontal_pos_layout": 0,
                "vertical_pos_layout": 0, "size_layout": 0, "target_follow": " "},
            "reverse": False, "source_timerange": {"start": 0, "duration": dur}, "speed": 1.0,
            "target_timerange": {"start": start, "duration": dur},
            "template_id": " ", "template_scene": "default ",
            "track_attribute": 0, "track_render_index": 0,
            "uniform_scale": {"on": True, "value": 1.0}, "visible": True, "volume": 1.0
        }

    def step_addsub():
        log('💉 ③ 注入新字幕（带黑底@底部）...')
        total = 0; failed = []
        for srt in sorted(glob.glob(os.path.join(SRT_DIR, '*.srt'))):
            name = os.path.splitext(os.path.basename(srt))[0]
            p = find_draft(name)
            if not p:
                log(f'  [跳过] {name}（没有同名草稿）'); continue
            try:
                blocks = parse_srt(srt)
                if not blocks:
                    log(f'  [跳过] {name}（srt里没有时间轴或内容为空）'); continue
                with open(p, encoding='utf-8') as f: data = json.load(f)
            except Exception as e:
                log(f'  [跳过] {name}（读取出错: {e}）'); failed.append(name); continue
            try:
                mats = data.setdefault('materials', {})
                if 'texts' not in mats: mats['texts'] = []
                old_ids = set(); kept_tracks = []
                for t in data.get('tracks', []):
                    if t.get('name') == '注入字幕' and t.get('type') == 'text':
                        for seg in t.get('segments', []): old_ids.add(seg.get('material_id'))
                    else: kept_tracks.append(t)
                data['tracks'] = kept_tracks
                if old_ids:
                    mats['texts'] = [m for m in mats['texts'] if m.get('id') not in old_ids]
                new_mats, new_segs = [], []
                for s, e, text in blocks:
                    mid = str(uuid.uuid4()).upper()
                    new_mats.append(make_mat(mid, text))
                    new_segs.append(make_seg(mid, s, e - s))
                mats['texts'].extend(new_mats)
                max_ri = max([t.get('track_render_index', 0) for t in data.get('tracks', [])], default=0)
                data.setdefault('tracks', []).append({
                    "attribute": 0, "flag": 0, "id": str(uuid.uuid4()),
                    "is_default_name": True, "name": "注入字幕", "segments": new_segs,
                    "type": "text", "track_render_index": max_ri + 1})
                bak = p + '.bak_subinj_bottom'
                if not os.path.exists(bak): shutil.copy(p, bak)
                with open(p, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False)
                total += 1
                log(f'  [✅ 注入 {len(blocks)} 条黑底字幕] {name}')
            except Exception as e:
                log(f'  [❌ 处理失败] {name}: {e}'); failed.append(name)
        log(f'📊  完成：共注入 {total} 个草稿')
        if failed:
            log('⚠️ 未成功：' + ', '.join(failed))

    # ═══════════ 主流程：按顺序执行勾选的步骤 ═══════════
    try:
        log('🛠️ 一键重整开始...')
        if config["do_noaudio"]: step_noaudio()
        if config["do_delsub"]: step_delsub()
        if config["do_addsub"]: step_addsub()
        log('🎉 一键重整全部完成！重新打开剪映查看效果。')
    except Exception:
        log('❌ 出错了：')
        log(traceback.format_exc())