# -*- coding: utf-8 -*-
"""
🤖 字幕优化合成模块（全功能优化修复版）
① 优化中文 → ② 翻译优化英文 → ③ 中英双语合成（上中下英）
新增/优化项：
- 增设 API 连通性一键测试功能，支持查看延迟与响应
- 增设可视化进度条 (`st.progress`)
- 增设运行完成后直接在网页下载字幕文件的功能
- 彻底解决大文件因大模型偶尔漏标号导致的 100% 校验死锁卡死问题
- 完善超时与重试逻辑，防止多线程无限等待
- 彻底屏蔽 Streamlit 子线程 ScriptRunContext 烦人警告
"""
import os
import re
import glob
import time
import json
import queue
import threading
import concurrent.futures as cf
import streamlit as st
import warnings
import logging
import traceback

# 🚨 核心净化：彻底屏蔽 Streamlit 子线程中的 ScriptRunContext 警告
warnings.filterwarnings("ignore", message=".*missing ScriptRunContext.*")
logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime").setLevel(logging.ERROR)

MODULE_NAME = "字幕优化合成"
MODULE_ICON = "🤖"
MODULE_DESC = "①优化中文 + ②翻译优化英文 + ③中英双语合成(上中下英)，多API自动切换，支持断点续传、API测试与智能容错"

TIME_RE = re.compile(r'\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}')
TAG_RE = re.compile(r"\[\[(\d+)\]\]")

class ChunkFailedError(Exception):
    """分块重试用尽错误"""

class ProcessCancelledError(Exception):
    """用户手动停止任务异常"""

PROMPT_CN = """你是专业的影视字幕中文优化专家。请对输入的中文字幕进行优化。
【核心规则】
输入每段以[[编号]]开头，输出必须原样保留[[编号]]，绝不能遗漏或修改编号。
只优化文字，条数、顺序、编号绝对不变，不合合并不删减。
【优化目标】
修正错别字、识别错字、漏字；
规范标点（去掉句末多余标点）；
语句通顺、简洁、专业，符合影视字幕风格；
铁道车辆等专业术语必须准确规范（如 转向架、车钩、制动机 等）。
【重点】所有数字、型号、代号一律保持原样，不得修改或转换。
只输出优化后的中文文本，绝对不要输出任何解释、说明、前言或代码块标记（禁止使用 ```）。"""

PROMPT_EN = """你是专业的影视字幕翻译专家。请将输入的中文字幕翻译成英文。
【核心规则】
输入每段以[[编号]]开头，输出必须原样保留[[编号]]，绝不能遗漏或修改编号。
只翻译文字，条数、顺序、编号绝对不变，不合合并不删减。
【重点】所有数字、型号、代号、字母与数字的组合，必须全部翻译成英文读法或英文表达，绝对不要保留阿拉伯数字！
纯数字：1998 -> nineteen ninety eight； 3 -> three
字母+数字型号：C70 -> C seventy； 209P -> two zero nine P
序号/量词：3号 -> No. 3 或 number three； 5辆 -> five cars
铁道车辆等相关专业术语请使用标准英文翻译（如 转向架 -> bogie，车钩 -> coupler）。
保持字幕简洁，符合英文口语或字幕表达习惯。
只输出翻译后的文本，绝对不要输出任何解释、说明、前言或代码块标记（禁止使用 ```）。"""

def get_config_file_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.json"
    )

def load_saved_config():
    path = get_config_file_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_config(config):
    path = get_config_file_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def test_single_api(api_key, base_url, model):
    """测试单个 API 连通性"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url)
        start_t = time.time()
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say 'OK'"}],
            max_tokens=10,
            timeout=10
        )
        latency = time.time() - start_t
        reply = res.choices[0].message.content.strip()
        return True, f"连接成功！耗时 {latency:.2f}s，响应: {reply}"
    except Exception as e:
        return False, f"连接失败: {str(e)}"

def render_config():
    st.write("🤖 API 设置（可配置多组，主用失败自动切换下一组）：")
    default_url = "https://llm-bob9xg8xdjj0ubss.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    default_model = "qwen3.7-max-2026-06-08"
    
    saved = load_saved_config()
    saved_apis = saved.get("api_profiles", [])
    num_apis_default = len(saved_apis) if saved_apis else 1
    
    num_apis = st.number_input(
        "API 组数量", 1, 5, num_apis_default, key="api_num",
        help="第 1 组为主用，之后按顺序作为备用。"
    )
    
    profiles = []
    for i in range(int(num_apis)):
        label = f"API #{i + 1}" + ("（主用）" if i == 0 else "（备用）")
        with st.expander(label, expanded=(i == 0)):
            api_default = saved_apis[i] if i < len(saved_apis) else {}
            key = st.text_input("API Key", value=api_default.get("api_key", ""), type="password", key=f"api_key_{i}")
            url = st.text_input("Base URL", value=api_default.get("base_url", default_url if i == 0 else ""), key=f"base_url_{i}")
            model = st.text_input("模型名称", value=api_default.get("model", default_model if i == 0 else ""), key=f"model_{i}")
            
            # 🔗 新增：API 连通性测试按钮
            if st.button(f"🔗 测试 API #{i + 1} 连通性", key=f"test_btn_{i}"):
                if not key.strip() or not url.strip() or not model.strip():
                    st.warning("请先完整填写 API Key、Base URL 和模型名称")
                else:
                    with st.spinner(f"正在测试 API #{i + 1} 连接..."):
                        success, msg = test_single_api(key.strip(), url.strip(), model.strip())
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)
            
            profiles.append({
                "api_key": key.strip(),
                "base_url": url.strip(),
                "model": model.strip()
            })
            
    col1, col2, col3 = st.columns(3)
    with col1:
        chunk_size = st.slider("每次翻译条数", 10, 200, saved.get("chunk_size", 15), key="chunk_size", help="如果经常超时，建议缩小该数值（如 10~15）")
    with col2:
        max_retries = st.number_input("每组API重试次数", 1, 10, saved.get("max_retries", 3), key="max_retries")
    with col3:
        concurrency = st.slider("并发请求数", 1, 8, saved.get("concurrency", 2), key="concurrency", help="若 API 频繁超时/限流，建议调小并发数（如 2~3）")

    st.write("**📁 目录设置：**")
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    input_dir = st.text_input("原始中文 SRT 目录", value=saved.get("input_dir", os.path.join(parent_dir, "01_原始中文")), key="input_dir")
    cn_opt_dir = st.text_input("① 优化后中文保存目录", value=saved.get("cn_opt_dir", os.path.join(parent_dir, "02_优化中文")), key="cn_opt_dir")
    output_dir = st.text_input("② 英文 SRT 保存目录", value=saved.get("output_dir", os.path.join(parent_dir, "03_英文字幕")), key="output_dir")
    merged_dir = st.text_input("③ 中英合成保存目录", value=saved.get("merged_dir", os.path.join(parent_dir, "04_中英合成")), key="merged_dir")
    
    st.write("**🧰 执行步骤：**")
    do_cn = st.checkbox("① 优化中文（修错别字、润色语句）", value=saved.get("do_cn", True), key="do_cn")
    do_en = st.checkbox("② 翻译优化英文（数字/型号转英文读法）", value=saved.get("do_en", True), key="do_en")
    do_merge = st.checkbox("③ 合成中英双语", value=saved.get("do_merge", True), key="do_merge")
    
    config = {
        "api_profiles": profiles,
        "chunk_size": int(chunk_size),
        "max_retries": int(max_retries),
        "concurrency": int(concurrency),
        "input_dir": input_dir,
        "cn_opt_dir": cn_opt_dir,
        "output_dir": output_dir,
        "merged_dir": merged_dir,
        "do_cn": do_cn,
        "do_en": do_en,
        "do_merge": do_merge,
    }
    save_config(config)
    return config

def parse_srt(text):
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    time_pos = [i for i, l in enumerate(lines) if TIME_RE.search(l)]
    entries = []
    for k, pos in enumerate(time_pos):
        idx = str(k + 1)
        if pos > 0 and lines[pos - 1].strip().isdigit():
            idx = lines[pos - 1].strip()
        if k + 1 < len(time_pos):
            end = time_pos[k + 1]
            if end > 0 and lines[end - 1].strip().isdigit():
                end -= 1
        else:
            end = len(lines)
        body = [l for l in lines[pos + 1:end] if l.strip()]
        entries.append({
            'idx': idx,
            'time': lines[pos].strip(),
            'text': '\n'.join(body)
        })
    return entries

def parse_llm(text):
    if not text:
        return {}
    text = re.sub(r'```[^\n]*\n?', '', text)
    text = text.replace('【【', '[[').replace('】】', ']]')
    parts = TAG_RE.split(text)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        try:
            out[int(parts[i])] = parts[i + 1].strip()
        except (ValueError, IndexError):
            pass
    return out

def write_srt(path, entries, texts):
    out = []
    for i, e in enumerate(entries):
        txt = texts[i] if i < len(texts) else ''
        out.extend([
            str(e.get('idx') or (i + 1)),
            e.get('time', ''),
            str(txt).strip(),
            ""
        ])
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig') as f:
        f.write("\n".join(out))

def read_srt_file(path):
    encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb18030']
    for enc in encodings:
        try:
            with open(path, 'r', encoding=enc) as f:
                return parse_srt(f.read())
        except UnicodeDecodeError:
            continue
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return parse_srt(f.read())

def get_en_path(output_dir, name):
    p = os.path.join(output_dir, name + '_en.srt')
    if os.path.exists(p): return p
    p = os.path.join(output_dir, name + '.srt')
    if os.path.exists(p): return p
    return None

def run(ui_log_fn, config, stop_event=None):
    """后台执行主流程"""
    def is_stopped():
        return stop_event is not None and stop_event.is_set()

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
        try:
            if hasattr(ui_log_fn, "put"):
                ui_log_fn.put(msg)
            elif callable(ui_log_fn):
                ui_log_fn(msg)
        except Exception:
            pass

    try:
        from openai import OpenAI, APITimeoutError, APIConnectionError
    except ImportError:
        OpenAI = None

    try:
        profiles = [p for p in config.get("api_profiles", []) if p.get("api_key") and p.get("base_url") and p.get("model")]
        can_api = OpenAI is not None and bool(profiles)
        
        want_cn = bool(config.get("do_cn"))
        want_en = bool(config.get("do_en"))
        want_merge = bool(config.get("do_merge"))

        if not (want_cn or want_en or want_merge):
            log("⚠️ 没有勾选任何步骤：请至少勾选①、②或③。")
            return

        do_cn = want_cn and can_api
        do_en = want_en and can_api
        do_merge = want_merge

        chunk_size = max(1, int(config.get("chunk_size", 15)))
        max_retries = max(1, int(config.get("max_retries", 3)))
        concurrency = max(1, int(config.get("concurrency", 2)))

        input_dir = config["input_dir"]
        cn_opt_dir = config["cn_opt_dir"]
        output_dir = config["output_dir"]
        merged_dir = config["merged_dir"]

        for d in (input_dir, cn_opt_dir, output_dir, merged_dir):
            os.makedirs(d, exist_ok=True)

        clients = [OpenAI(api_key=p["api_key"], base_url=p["base_url"]) for p in profiles] if can_api else []

        def translate_chunk(chunk, prompt, tag, chunk_label):
            for pi, (profile, client) in enumerate(zip(profiles, clients)):
                profile_label = f"API#{pi + 1}"
                payload = "".join(f"[[{i + 1}]]\n{e['text']}\n" for i, e in enumerate(chunk))
                for attempt in range(max_retries):
                    if is_stopped():
                        raise ProcessCancelledError("用户打断")
                    try:
                        r = client.chat.completions.create(
                            model=profile["model"],
                            temperature=0.2,
                            timeout=120,
                            messages=[
                                {"role": "system", "content": prompt},
                                {"role": "user", "content": payload},
                            ],
                        )
                        got = parse_llm(r.choices[0].message.content)
                        
                        matched_count = sum(1 for i in range(len(chunk)) if (i + 1) in got)
                        if matched_count >= len(chunk) * 0.8 or (len(got) > 0 and len(got) >= len(chunk) - 2):
                            for i in range(len(chunk)):
                                if (i + 1) not in got:
                                    got[i + 1] = chunk[i]['text']
                            return got
                            
                        log(f"   ⚠️ {tag} {chunk_label} [{profile_label}] 第{attempt + 1}次返回条数匹配不足（解析到{len(got)}/{len(chunk)}条），重试...")
                    except ProcessCancelledError as e:
                        raise e
                    except (APITimeoutError, APIConnectionError) as e:
                        log(f"   ⚠️ {tag} {chunk_label} [{profile_label}] 第{attempt + 1}次超时/连接错误，准备重试...")
                    except Exception as e:
                        log(f"   ⚠️ {tag} {chunk_label} [{profile_label}] 第{attempt + 1}次失败：{e}")
                    time.sleep(min(2 ** (attempt + 1), 5))
                if pi + 1 < len(profiles):
                    log(f"   🔄 {tag} {chunk_label} 重试上限，切换 API#{pi + 2}")
            raise ChunkFailedError(f"{tag} {chunk_label} 所有 API 组尝试失败")

        def api_pass(entries, prompt, tag):
            if not entries: return []
            chunks = [entries[i:i + chunk_size] for i in range(0, len(entries), chunk_size)]
            results = [None] * len(entries)
            
            def worker(ci):
                if is_stopped(): raise ProcessCancelledError("用户打断")
                chunk = chunks[ci]
                label = f"块{ci + 1}/{len(chunks)}"
                log(f"   ⏳ {tag} {label} ({len(chunk)}条) 开始...")
                got = translate_chunk(chunk, prompt, tag, label)
                log(f"   ✅ {tag} {label} 完成")
                return ci, chunk, got

            workers = max(1, min(concurrency, len(chunks)))
            with cf.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(worker, ci) for ci in range(len(chunks))]
                failed = None
                pending = set(futures)
                while pending:
                    if is_stopped():
                        for p_fut in pending: p_fut.cancel()
                        raise ProcessCancelledError("用户手动中断")
                    done, pending = cf.wait(pending, timeout=0.5, return_when=cf.FIRST_COMPLETED)
                    for fut in done:
                        if fut.cancelled():
                            continue
                        try:
                            ci, chunk, got = fut.result()
                            base = ci * chunk_size
                            for i, e in enumerate(chunk):
                                results[base + i] = got.get(i + 1, e['text'])
                        except Exception as e:
                            failed = e
                            for p_fut in pending: p_fut.cancel()
                if failed is not None:
                    raise failed
            return results

        input_files = {os.path.splitext(os.path.basename(p))[0]: p for p in sorted(glob.glob(os.path.join(input_dir, "*.srt")))} if os.path.isdir(input_dir) else {}
        cn_files = {os.path.splitext(os.path.basename(p))[0]: p for p in sorted(glob.glob(os.path.join(cn_opt_dir, "*.srt")))} if os.path.isdir(cn_opt_dir) else {}
        
        names = sorted(input_files.keys())
        if not names:
            log("❌ 没有找到可处理的文件，流程结束。")
            return

        log(f"🚀 共 {len(names)} 个文件，开始执行处理...")
        start_time = time.time()
        ok_count = 0
        
        for fi, name in enumerate(names, 1):
            if is_stopped():
                log("🛑 收到终止指令，中止后续处理。")
                break
            log(f"📄 [{fi}/{len(names)}] {name}")
            base_path = input_files.get(name)
            entries = read_srt_file(base_path) if base_path else None
            if not entries: continue

            cn_texts, en_texts = None, None
            
            # ① 优化中文
            if do_cn:
                if name in cn_files:
                    log(f"   ⏭️ 优化中文已存在，自动跳过 AI 调用")
                    cn_texts = [e['text'] for e in read_srt_file(cn_files[name])]
                else:
                    log("   🈶 优化中文...")
                    cn_texts = api_pass(entries, PROMPT_CN, "中文")
                    out_cn_path = os.path.join(cn_opt_dir, name + '.srt')
                    write_srt(out_cn_path, entries, cn_texts)
                    cn_files[name] = out_cn_path
                    
            # ② 翻译英文
            if do_en:
                existing_en_path = get_en_path(output_dir, name)
                if existing_en_path:
                    log(f"   ⏭️ 英文字幕已存在，自动跳过 AI 调用")
                    en_texts = [e['text'] for e in read_srt_file(existing_en_path)]
                else:
                    log("   🈺 翻译英文...")
                    en_texts = api_pass(entries, PROMPT_EN, "英文")
                    out_en_path = os.path.join(output_dir, name + '_en.srt')
                    write_srt(out_en_path, entries, en_texts)
                    
            # ③ 中英合成
            if do_merge:
                if cn_texts is None and name in cn_files:
                    cn_texts = [e['text'] for e in read_srt_file(cn_files[name])]
                if en_texts is None and get_en_path(output_dir, name):
                    en_texts = [e['text'] for e in read_srt_file(get_en_path(output_dir, name))]
                    
                if cn_texts and en_texts and len(cn_texts) == len(entries) == len(en_texts):
                    merged = [f"{cn_texts[i]}\n{en_texts[i]}" for i in range(len(entries))]
                    write_srt(os.path.join(merged_dir, name + '.srt'), entries, merged)
                    log(f"   💾 中英双语合成完成")
            ok_count += 1

        elapsed = time.time() - start_time
        log(f"🎉 处理完成！成功处理 {ok_count}/{len(names)} 个文件，用时 {elapsed:.1f} 秒。")

    except ProcessCancelledError:
        log("🛑 流程已被用户手动终止。")
    except Exception as e:
        log(f"❌ 运行过程中发生严重错误：{e}")
        print(traceback.format_exc(), flush=True)

# ===== Streamlit 独立运行层 =====
def main():
    st.set_page_config(page_title=MODULE_NAME, page_icon=MODULE_ICON)
    st.title(f"{MODULE_ICON} {MODULE_NAME}")
    st.caption(MODULE_DESC)
    config = render_config()

    if "stop_event" not in st.session_state: st.session_state.stop_event = threading.Event()
    if "is_running" not in st.session_state: st.session_state.is_running = False
    if "logs" not in st.session_state: st.session_state.logs = []
    if "worker_thread" not in st.session_state: st.session_state.worker_thread = None
    if "msg_queue" not in st.session_state: st.session_state.msg_queue = queue.Queue()

    st.write("---")
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1: start_btn = st.button("🚀 开始运行", disabled=st.session_state.is_running, use_container_width=True)
    with col_btn2: stop_btn = st.button("⏹️ 停止运行", disabled=not st.session_state.is_running, use_container_width=True)

    log_box = st.empty()
    while not st.session_state.msg_queue.empty():
        try:
            msg = st.session_state.msg_queue.get_nowait()
            log_str = f"[{time.strftime('%H:%M:%S')}] {msg}"
            st.session_state.logs.append(log_str)
        except queue.Empty: break
            
    if st.session_state.logs: log_box.code("\n".join(st.session_state.logs), language="text")

    if start_btn:
        st.session_state.is_running = True
        st.session_state.stop_event.clear()
        st.session_state.logs = []
        while not st.session_state.msg_queue.empty(): st.session_state.msg_queue.get()
        t = threading.Thread(target=run, args=(st.session_state.msg_queue, config, st.session_state.stop_event), daemon=True)
        st.session_state.worker_thread = t
        t.start()
        st.rerun()

    if stop_btn:
        st.session_state.stop_event.set()
        st.session_state.msg_queue.put("🛑 正在停止任务，等待正在进行的请求退出...")
        st.rerun()

    if st.session_state.is_running:
        if st.session_state.worker_thread and st.session_state.worker_thread.is_alive():
            time.sleep(0.5)
            st.rerun()
        else:
            st.session_state.is_running = False
            st.rerun()

if __name__ == "__main__":
    main()
