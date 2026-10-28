"""Streamlit UI for the POC: `uv run streamlit run ui/studio.py`.

Flow: Discover (reference -> opportunity -> Generate Video) or Create -> Plan -> Storyboard (copy Muse prompts,
upload a video or image per scene, voice) -> Render -> QC -> Publish.
Deliberately plain: internal tool, one user, no timeline editor.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import streamlit as st

from factory import handoff, media
from factory.config import Settings, get_settings
from factory.discovery.formats import FORMATS
from factory.discovery.models import ContentOpportunity, DiscoveryCandidate
from factory.discovery.scoring import age_days
from factory.discovery.service import Discovery
from factory.discovery.youtube import NOT_CONFIGURED
from factory.pipeline import AUDIO_EXT, Factory
from factory.publish import youtube
from factory.qc import qc_warnings
from factory.render import check_ready
from factory.schema import IMAGE_EXT, VIDEO_EXT, Project, SceneState, asset_type_for
from factory.store import NotFound

st.set_page_config(page_title="AI Content Factory", page_icon="🎬", layout="wide")
F = Factory()
S = get_settings()
D = Discovery(F)


def save_upload(upload) -> Path:
    suffix = Path(upload.name).suffix.lower()
    tmp = Path(tempfile.mkdtemp()) / f"upload{suffix}"
    tmp.write_bytes(upload.getbuffer())
    return tmp


def run(label: str, fn, *args, **kwargs):
    """Run an action with a spinner; show errors instead of crashing the page."""
    try:
        with st.spinner(label):
            result = fn(*args, **kwargs)
        st.session_state["flash"] = ("success", f"{label} xong")
        return True if result is None else result
    except Exception as e:  # noqa: BLE001 - surface every stage error to the user
        st.session_state["flash"] = ("error", f"{label} lỗi: {e}")
        return None


def asset_caption(s: SceneState) -> str:
    parts = [
        f"Type: {'Image' if s.asset_type == 'image' else 'Video'}",
        f"Source: {s.asset_source or 'unknown'}",
        Path(str(s.asset_path)).name,
    ]
    if s.asset_width and s.asset_height:
        parts.append(f"{s.asset_width}x{s.asset_height}")
    if s.asset_duration:
        parts.append(f"{s.asset_duration:.1f}s")
    return " · ".join(parts)


def exts(kind: str) -> list[str]:
    return sorted(e.lstrip(".") for e in (VIDEO_EXT if kind == "Video" else IMAGE_EXT))


@st.cache_data(ttl=300)
def preflight() -> dict:
    return media.preflight()


@st.cache_data(ttl=60)
def youtube_status(settings: Settings) -> str:
    return youtube.status(settings)


def nonce(slot: str) -> int:
    return st.session_state.get(f"nonce-{slot}", 0)


def bump(slot: str) -> None:
    st.session_state[f"nonce-{slot}"] = nonce(slot) + 1


def act(label: str, fn, *args, **kwargs) -> None:
    run(label, fn, *args, **kwargs)


def act_save_scene(pid: str, order: int) -> None:
    ss = st.session_state
    run(f"Lưu scene {order}", F.edit_scene, pid, order, ss[f"n{pid}{order}"], ss[f"v{pid}{order}"])


def act_save_metadata(pid: str) -> None:
    ss = st.session_state
    run("Lưu metadata", F.edit_metadata, pid, ss[f"mt{pid}"], ss[f"md{pid}"], ss[f"mh{pid}"].split())


def act_generate_voice(pid: str, orders: list[int] | None) -> None:
    label = "Generate voice" if orders is None else f"Voice scene {orders[0]}"
    run(label, F.generate_voice, pid, orders, st.session_state[f"prov{pid}"])


def act_attach(pid: str, order: int, upload_key: str, source_key: str, slot: str) -> None:
    upload = st.session_state.get(upload_key)
    if upload is None:
        return
    if run(f"Gắn asset scene {order}", F.attach_asset, pid, order, save_upload(upload), st.session_state[source_key]):
        bump(slot)


def act_save_bible(pid: str) -> None:
    run("Lưu visual bible", F.edit_visual_bible, pid, st.session_state[f"vb{pid}"])


def act_bulk_import(pid: str, upload_key: str, slot: str) -> None:
    ss = st.session_state
    uploads = ss.get(upload_key) or []
    if not uploads:
        return
    folder = Path(tempfile.mkdtemp())
    base = time.time()
    for i, upload in enumerate(uploads):
        dst = folder / Path(upload.name).name
        dst.write_bytes(upload.getbuffer())
        os.utime(dst, (base + i, base + i))
    by_time = ss[f"bulkmode{pid}"] == "Thứ tự trong danh sách upload → scene còn trống"
    report = run("Import assets", F.import_assets, pid, [folder], by_time)
    if not isinstance(report, list):
        return
    done = [f"scene {r['scene']}" for r in report if r["result"] == "attached"]
    other = [f"{Path(r['file']).name}: {r['result']}" for r in report if r["result"] != "attached"]
    kind = "error" if any(r["result"].startswith("error") for r in report) else "success"
    ss["flash"] = (kind, "Đã gắn: " + (", ".join(done) or "không có") + ("  \n" + "  \n".join(other) if other else ""))
    bump(slot)


def act_import_voice(pid: str, order: int, upload_key: str) -> None:
    upload = st.session_state.get(upload_key)
    if upload is not None:
        run(f"Import voice scene {order}", F.import_voice, pid, order, save_upload(upload))


def render_with_music(label: str, pid: str, slot: str) -> None:
    ss = st.session_state
    bgm = ss.get(f"{slot}{nonce(slot)}")
    no_bgm = bool(ss.get(f"nobgm{slot}{nonce(slot)}"))
    if run(label, F.render, pid, save_upload(bgm) if bgm else None, ss[f"vol{pid}"], no_bgm=no_bgm):
        bump(slot)


def act_qc_replace(pid: str, order: int, upload_key: str, source_key: str, qc_slot: str, bgm_slot: str) -> None:
    upload = st.session_state.get(upload_key)
    if upload is None:
        return
    if run(f"Thay asset scene {order}", F.attach_asset, pid, order, save_upload(upload), st.session_state[source_key]):
        bump(qc_slot)
        render_with_music("Render lại", pid, bgm_slot)


def act_connect() -> None:
    run("Kết nối YouTube", youtube.connect, S)
    youtube_status.clear()


def act_upload(pid: str, force: bool) -> None:
    run(
        "Upload YouTube",
        F.publish,
        pid,
        youtube.YouTubePublisher(S),
        st.session_state[f"privacy{pid}"],
        force=force,
    )
    youtube_status.clear()


def act_discover_url() -> None:
    ss = st.session_state
    c = run("Lưu URL", D.add_url, ss["d_url"], ss["d_url_title"], ss["d_url_transcript"], ss["d_url_notes"])
    if isinstance(c, DiscoveryCandidate):
        ss["cid"] = c.id


def act_discover_content() -> None:
    ss = st.session_state
    c = run(
        "Lưu nội dung",
        D.add_content,
        ss["d_topic"],
        ss["d_title"],
        ss["d_transcript"],
        ss["d_notes"],
        ss["d_src_url"],
    )
    if isinstance(c, DiscoveryCandidate):
        ss["cid"] = c.id


def act_search_youtube() -> None:
    ss = st.session_state
    res = run(
        "Tìm trên YouTube",
        D.search_youtube,
        ss["d_niche"],
        ss["d_keywords"],
        ss["d_days"],
        ss["d_max"],
        ss["d_lang"],
    )
    if isinstance(res, tuple):
        found, cached = res
        ss["flash"] = ("success", f"{len(found)} video ({'từ cache, không tốn quota' if cached else 'YouTube API'})")


def act_edit_candidate(cid: str) -> None:
    ss = st.session_state
    run(
        "Lưu reference",
        D.edit_candidate,
        cid,
        ss[f"ct{cid}"],
        ss[f"cx{cid}"],
        ss[f"cn{cid}"],
        ss[f"cp{cid}"],
    )


def act_analyze(cid: str) -> None:
    ss = st.session_state
    ss["cid"] = cid
    o = run("Analyze", D.analyze, cid, ss.get("d_an_lang", "vi"), ss.get("d_an_niche") or None)
    if isinstance(o, ContentOpportunity):
        ss["oid"] = o.id


def act_choose(oid: str) -> None:
    ss = st.session_state
    run("Lưu lựa chọn", D.choose, oid, ss[f"oh{oid}"], ss[f"of{oid}"])


def act_save_pattern(oid: str) -> None:
    run("Save Pattern", D.save_pattern, oid)


def act_generate_video(oid: str) -> None:
    ss = st.session_state
    p = run(
        "Generate Video",
        D.generate_video,
        oid,
        ss[f"od{oid}"],
        ss[f"os{oid}"],
        hook_index=ss[f"oh{oid}"],
        format_id=ss[f"of{oid}"],
    )
    if isinstance(p, Project):
        ss["pid"] = p.id
        ss["page"] = "Projects"


def candidate_age(c: DiscoveryCandidate) -> str:
    age = age_days(c.published_at)
    if age is None:
        return "-"
    return f"{age * 24:.0f}h" if age < 1 else f"{age:.0f} ngày"


def candidate_row(c: DiscoveryCandidate) -> None:
    with st.container(border=True):
        left, mid, right = st.columns([5, 3, 1])
        title = c.label if not c.url else f"[{c.label[:90]}]({c.url})"
        left.markdown(f"**{title}**")
        left.caption(f"{c.author or c.platform or c.source} · {c.source}")
        views = c.metrics.get("views")
        vpd = c.score.raw.get("views_per_day") if c.score else None
        mid.caption(
            f"Views {views:,} · Age {candidate_age(c)}  \nViews/day {vpd:,.0f}" if views is not None and vpd else "-"
        )
        if c.score:
            sig = " · ".join(f"{k} {v:g}" for k, v in c.score.signals.items())
            mid.caption(f"**Viral {c.score.viral_score:g}/10** · {sig}")
        else:
            mid.caption("Viral: chưa có metrics")
        right.button("Analyze", key=f"an{c.id}", on_click=act_analyze, args=(c.id,))


def candidate_detail(c: DiscoveryCandidate) -> None:
    st.subheader("Reference đang chọn")
    st.caption(f"{c.id} · {c.source}" + (f" · {c.url}" if c.url else ""))
    if c.enrich_error:
        st.info(c.enrich_error)
    with st.expander("Bổ sung title / transcript / notes", expanded=not (c.title or c.transcript or c.topic)):
        st.text_input("Topic", c.topic or "", key=f"cp{c.id}")
        st.text_input("Title", c.title or "", key=f"ct{c.id}")
        st.text_area("Transcript / summary", c.transcript or "", key=f"cx{c.id}", height=120)
        st.text_area("Notes", c.notes or "", key=f"cn{c.id}", height=70)
        st.button("Lưu reference", key=f"ce{c.id}", on_click=act_edit_candidate, args=(c.id,))
    st.button("Analyze reference này", type="primary", key=f"an2{c.id}", on_click=act_analyze, args=(c.id,))


def opportunity_panel(o: ContentOpportunity) -> None:
    st.subheader(f"Opportunity · {o.scores.overall:g}/10")
    st.caption(f"{o.id} · analyzer {o.analyzer} · ${o.cost_usd:.4f}")
    st.markdown(f"**Topic:** {o.topic}  \n**Angle:** {o.angle}  \n**Hook pattern:** {o.hook_pattern}")
    hook_labels = [f"{h.overall:.1f} · {h.text}" for h in o.hooks]
    st.radio(
        "Hook (chọn 1)",
        range(len(o.hooks)),
        index=o.selected_hook,
        format_func=lambda i: hook_labels[i],
        key=f"oh{o.id}",
    )
    ids = list(FORMATS)
    st.selectbox(
        "Format",
        ids,
        index=ids.index(o.format_id),
        format_func=lambda i: f"{FORMATS[i].name} · {' → '.join(FORMATS[i].story_beats)}",
        key=f"of{o.id}",
    )
    st.markdown("**Why it may work**  \n" + "  \n".join(f"- {w}" for w in o.why_it_may_work))
    st.caption(" · ".join(f"{k} {v:g}" for k, v in o.scores.model_dump().items() if v is not None and k != "overall"))
    fmt = FORMATS[o.format_id]
    g1, g2 = st.columns(2)
    g1.slider("Thời lượng (s)", 20, 45, round(sum(fmt.target_duration) / 2), key=f"od{o.id}")
    g2.text_input("Visual style", "cinematic, realistic, soft natural light", key=f"os{o.id}")
    b1, b2, b3 = st.columns(3)
    b1.button("Generate Video", type="primary", key=f"gen{o.id}", on_click=act_generate_video, args=(o.id,))
    b2.button(
        "Pattern đã lưu" if o.saved_pattern else "Save Pattern",
        key=f"sp{o.id}",
        disabled=o.saved_pattern,
        on_click=act_save_pattern,
        args=(o.id,),
    )
    b3.button("Lưu hook/format", key=f"ch{o.id}", on_click=act_choose, args=(o.id,))
    if o.project_ids:
        st.caption("Projects đã tạo: " + ", ".join(o.project_ids))


def discover_page() -> None:
    st.header("Discover")
    src = st.radio("Source", ["YouTube", "Manual URL", "Manual Content"], horizontal=True, key="d_src")
    if src == "YouTube":
        if not D.youtube_configured:
            st.info(NOT_CONFIGURED)
        with st.container(border=True):
            y1, y2 = st.columns(2)
            y1.text_input("Niche", key="d_niche")
            y2.text_input("Keywords", key="d_keywords")
            y3, y4, y5 = st.columns(3)
            y3.slider("Lookback (ngày)", 1, 90, 30, key="d_days")
            y4.slider("Max results", 5, 50, 20, key="d_max")
            y5.selectbox("Ngôn ngữ kết quả", ["vi", "en", None], format_func=lambda x: x or "bất kỳ", key="d_lang")
            st.button("Search", type="primary", disabled=not D.youtube_configured, on_click=act_search_youtube)
    elif src == "Manual URL":
        with st.container(border=True):
            st.text_input("URL (YouTube / TikTok / Facebook / khác)", key="d_url")
            st.text_input("Title (tuỳ chọn)", key="d_url_title")
            st.text_area("Transcript / text (tuỳ chọn)", key="d_url_transcript", height=100)
            st.text_input("Notes (tuỳ chọn)", key="d_url_notes")
            st.caption("YouTube: tự lấy metadata nếu có YOUTUBE_API_KEY. Nền tảng khác: chỉ lưu URL, không crawl.")
            st.button("Lưu URL", type="primary", on_click=act_discover_url)
    else:
        with st.container(border=True):
            st.text_input("Topic", key="d_topic")
            st.text_input("Title", key="d_title")
            st.text_area("Transcript / summary", key="d_transcript", height=120)
            st.text_input("Notes", key="d_notes")
            st.text_input("Source URL (tuỳ chọn)", key="d_src_url")
            st.button("Lưu nội dung", type="primary", on_click=act_discover_content)

    a1, a2, a3 = st.columns(3)
    a1.selectbox("Ngôn ngữ video", ["vi", "en"], key="d_an_lang")
    a2.text_input("Channel niche (cho Analyze)", key="d_an_niche")
    top = a3.slider("Hiện top", 5, 50, 10, key="d_top")

    candidates = D.candidates()
    st.subheader(f"Candidates ({len(candidates)})")
    if not candidates:
        st.caption("Chưa có reference nào.")
    for c in candidates[:top]:
        candidate_row(c)

    cid = st.session_state.get("cid")
    current = D.store.find_candidate(cid) if cid else None
    if current is None:
        return
    st.divider()
    candidate_detail(current)
    opps = D.store.list_opportunities(current.id)
    if not opps:
        return
    oid = st.session_state.get("oid")
    ids = [o.id for o in opps]
    chosen = st.selectbox(
        "Opportunities của reference này",
        ids,
        index=ids.index(oid) if oid in ids else 0,
        format_func=lambda i: next(f"{o.scores.overall:g} · {o.topic[:60]}" for o in opps if o.id == i),
    )
    opportunity_panel(next(o for o in opps if o.id == chosen))


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🎬 Content Factory")
    page = st.radio("Màn hình", ["Projects", "Discover"], horizontal=True, key="page")
    pf = preflight()
    yt = youtube_status(S)
    planner_mode = "Claude" if S.anthropic_api_key and S.planner != "mock" else "Mock (chưa có ANTHROPIC_API_KEY)"
    st.caption(
        f"FFmpeg: {'✅' if not pf['problems'] else '❌ ' + ', '.join(pf['problems'])}  \n"
        f"Planner: {planner_mode}  \nVoice mặc định: {S.voice_provider}  \n"
        f"YouTube: {yt}"
    )
    with st.form("create", clear_on_submit=True):
        st.subheader("Project mới")
        topic = st.text_input("Topic")
        duration = st.slider("Thời lượng mục tiêu (s)", 20, 45, 30)
        language = st.radio("Ngôn ngữ", ["vi", "en"], horizontal=True)
        style = st.text_input("Visual style", "cinematic, realistic, soft natural light")
        bible = st.text_area(
            "Nhân vật / bối cảnh cố định (tuỳ chọn, tiếng Anh)",
            placeholder="vd: a woman in her 20s, shoulder-length black hair, beige trench coat, rainy Hanoi street",
        )
        if st.form_submit_button("Tạo project", type="primary") and topic.strip():
            p = run("Tạo project", F.create_project, topic.strip(), duration, language, style, visual_bible=bible)
            if isinstance(p, Project):
                st.session_state["pid"] = p.id
    projects = F.store.list()
    if projects:
        ids = [p.id for p in projects]
        current = st.session_state.get("pid", ids[0])
        st.session_state["pid"] = st.selectbox(
            "Projects",
            ids,
            index=ids.index(current) if current in ids else 0,
            format_func=lambda i: next(f"{p.title[:40]} · {p.status}" for p in projects if p.id == i),
        )

if "flash" in st.session_state:
    kind, msg = st.session_state.pop("flash")
    (st.success if kind == "success" else st.error)(msg)

if page == "Discover":
    discover_page()
    st.stop()

pid = st.session_state.get("pid")
if not pid:
    st.info("Tạo project ở sidebar để bắt đầu.")
    st.stop()

try:
    p = F.store.get(pid)
except (ValueError, OSError, NotFound) as e:
    st.error(f"Không đọc được project.json của {pid}: {e}")
    st.stop()
try:
    sb = F.store.get_storyboard(pid)
except (ValueError, OSError) as e:
    st.error(f"Không đọc được storyboard.json của {pid}: {e}")
    st.stop()
pdir = F.store.dir(pid)

# ---------------------------------------------------------------- header
st.header(p.title)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Status", p.status)
c2.metric("Stage", p.current_stage)
c3.metric("Est. cost", f"${p.estimated_cost:.4f}")
c4.metric("Planner runs", p.generation_count)
if p.error:
    st.error(p.error)

tab_sb, tab_render, tab_pub, tab_log = st.tabs(["1 · Storyboard", "2 · Render & QC", "3 · Publish", "Logs"])

# ---------------------------------------------------------------- storyboard
with tab_sb:
    cols = st.columns([1, 1, 2])
    cols[0].button(
        "Generate plan" if sb is None else "Re-plan (xoá clip/voice cũ)",
        type="primary",
        on_click=act,
        args=("Generate plan", F.plan, pid),
    )
    if sb is not None:
        provider = cols[1].selectbox(
            "Voice provider",
            ["mock", "edge", "voicestudio"],
            index=["mock", "edge", "voicestudio"].index(S.voice_provider)
            if S.voice_provider in ("mock", "edge", "voicestudio")
            else 0,
            key=f"prov{pid}",
        )
        cols[2].button("Generate voice cho TẤT CẢ scene", on_click=act_generate_voice, args=(pid, None))
        if provider == "edge":
            st.warning("Edge TTS chỉ để test, không dùng cho video monetize (license không rõ).")

        with st.expander("YouTube metadata", expanded=False):
            st.text_input("Title", sb.title, max_chars=100, key=f"mt{pid}")
            st.text_area("Description", sb.description, key=f"md{pid}")
            st.text_input("Hashtags", " ".join(sb.hashtags), key=f"mh{pid}")
            st.caption(f"Hook: {sb.hook}")
            st.button("Lưu metadata", on_click=act_save_metadata, args=(pid,))

        if st.toggle("Muse: tất cả prompt + import nhiều file", key=f"muse{pid}"):
            st.text_area(
                "Visual bible (đặt ở đầu mọi prompt để nhân vật/bối cảnh giống nhau giữa các clip)",
                sb.visual_bible,
                key=f"vb{pid}",
                height=80,
            )
            st.button("Lưu bible (áp vào mọi prompt)", key=f"vbs{pid}", on_click=act_save_bible, args=(pid,))
            sheet = handoff.muse_sheet(pid, p.title, sb)
            st.caption("Copy một lần cả bộ prompt (nút copy ở góc phải) hoặc tải file:")
            st.code(sheet, language=None, wrap_lines=True)
            st.download_button("Tải muse_prompts.md", sheet, file_name=f"{pid}-muse.md")
            bulk_slot = f"bulk{pid}"
            bulk_key = f"{bulk_slot}{nonce(bulk_slot)}"
            st.file_uploader(
                "Chọn nhiều video/ảnh đã tải từ Muse",
                key=bulk_key,
                type=exts("Video") + exts("Image"),
                accept_multiple_files=True,
            )
            st.radio(
                "Ghép file vào scene theo",
                [
                    "Số scene trong tên file (scene_01.mp4, canh-2.jpg, 03.mp4)",
                    "Thứ tự trong danh sách upload → scene còn trống",
                ],
                key=f"bulkmode{pid}",
            )
            st.button("Gắn tất cả", key=f"bulkgo{pid}", on_click=act_bulk_import, args=(pid, bulk_key, bulk_slot))

        total_audio = sum(s.actual_audio_duration or 0 for s in sb.scenes)
        st.caption(
            f"{len(sb.scenes)} scenes · target {sum(s.target_duration for s in sb.scenes):.1f}s · "
            f"voice thực tế {total_audio:.1f}s"
        )

        for s in sb.scenes:
            with st.container(border=True):
                head = f"**Scene {s.order}** · target {s.target_duration:.1f}s"
                if s.actual_audio_duration:
                    head += f" · voice {s.actual_audio_duration:.2f}s"
                    if s.actual_audio_duration > 10:
                        head += " ⚠️ >10s"
                if s.audio_stale:
                    head += " · ⚠️ narration đã sửa, cần tạo lại voice"
                st.markdown(head)
                left, right = st.columns([3, 2])
                with left:
                    st.text_area("Narration", s.narration, key=f"n{pid}{s.order}", height=90)
                    st.text_area("Visual prompt (Muse)", s.video_prompt, key=f"v{pid}{s.order}", height=110)
                    st.button("Lưu scene", key=f"save{s.order}", on_click=act_save_scene, args=(pid, s.order))
                    st.caption("Copy Muse Prompt (nút copy ở góc phải):")
                    st.code(s.video_prompt, language=None, wrap_lines=True)
                with right:
                    st.markdown("**Visual Asset**")
                    if s.asset_path:
                        asset_file = pdir / s.asset_path
                        if not asset_file.is_file():
                            st.warning(f"File asset bị mất: {asset_file.name}")
                        elif s.asset_type == "image":
                            st.image(str(asset_file))
                        else:
                            st.video(str(asset_file))
                        st.caption(asset_caption(s))
                    kind = st.radio("Loại asset", ["Video", "Image"], horizontal=True, key=f"k{pid}{s.order}")
                    asset_slot = f"c{pid}{s.order}"
                    upload_key = f"{asset_slot}{kind}{nonce(asset_slot)}"
                    source_key = f"src{pid}{s.order}{kind}"
                    asset = st.file_uploader(f"Upload {kind}", key=upload_key, type=exts(kind))
                    st.selectbox(
                        "Source",
                        ["muse", "upload", "generated"] if kind == "Video" else ["upload", "generated"],
                        key=source_key,
                    )
                    if asset:
                        st.button(
                            "Replace Asset" if s.asset_path else "Attach Asset",
                            key=f"ac{s.order}",
                            on_click=act_attach,
                            args=(pid, s.order, upload_key, source_key, asset_slot),
                        )
                    if s.asset_path:
                        st.button(
                            "Remove Asset",
                            key=f"rm{s.order}",
                            on_click=act,
                            args=(f"Xoá asset scene {s.order}", F.remove_asset, pid, s.order),
                        )
                    if s.audio_file:
                        st.audio(str(pdir / s.audio_file))
                        st.caption(f"voice: {s.audio_source}")
                    vc1, vc2 = st.columns(2)
                    vc1.button("Tạo voice", key=f"gv{s.order}", on_click=act_generate_voice, args=(pid, [s.order]))
                    audio_key = f"a{pid}{s.order}"
                    audio = vc2.file_uploader(
                        "hoặc upload audio",
                        key=audio_key,
                        type=[e.lstrip(".") for e in AUDIO_EXT],
                        label_visibility="collapsed",
                    )
                    if audio:
                        st.button(
                            "Dùng audio này",
                            key=f"ua{s.order}",
                            on_click=act_import_voice,
                            args=(pid, s.order, audio_key),
                        )

# ---------------------------------------------------------------- render & QC
with tab_render:
    if sb is None:
        st.info("Chưa có storyboard.")
    else:
        problems = check_ready(pdir, sb)
        for msg in problems:
            st.warning(msg)
        rc1, rc2 = st.columns(2)
        bgm_slot = f"bgm{pid}"
        rc1.file_uploader(
            "Nhạc nền mới (tuỳ chọn, royalty-free, để trống = giữ nhạc hiện tại)",
            type=["mp3", "wav", "m4a", "aac"],
            key=f"{bgm_slot}{nonce(bgm_slot)}",
        )
        rc1.checkbox("Bỏ nhạc nền", key=f"nobgm{bgm_slot}{nonce(bgm_slot)}", disabled=p.bgm is None)
        vol_default = float(p.bgm.volume if p.bgm else S.bgm_volume)
        rc2.slider("BGM volume", 0.0, max(0.6, vol_default), vol_default, 0.05, key=f"vol{pid}")
        rc2.caption(f"Nhạc nền hiện tại: {Path(p.bgm.file).name}" if p.bgm else "Chưa có nhạc nền")

        st.button(
            "Render",
            type="primary",
            disabled=bool(problems),
            on_click=render_with_music,
            args=("Render", pid, bgm_slot),
        )

        r = p.render
        if r.status == "error":
            st.error(f"Render lỗi: {r.error}")
        if r.file and (pdir / r.file).is_file():
            vcol, icol = st.columns([2, 3])
            with vcol:
                st.video(str(pdir / r.file))
            with icol:
                st.metric("Duration", f"{r.duration:.2f}s")
                st.metric("File size", f"{(r.file_size or 0) / 1e6:.1f} MB")
                st.caption(
                    f"{r.width}x{r.height} · render #{r.render_count} · "
                    f"{p.cost.render_duration_ms / 1000:.1f}s · reused stages {r.reused_stages}"
                )
                if p.render_stale:
                    st.warning("Asset đã đổi sau lần render cuối → Render lại.")
                for w in qc_warnings(p, sb, pdir):
                    st.warning(w)
                with open(pdir / r.file, "rb") as fh:
                    st.download_button("Tải final-short.mp4", fh, file_name=f"{pid}.mp4")
            st.subheader("Replace scene → Render again")
            st.caption(
                "Chỉ thay asset (video hoặc ảnh) của 1 scene. Không chạy lại planner, không tạo lại voice, "
                "các scene khác được reuse."
            )
            q1, q2 = st.columns([1, 3])
            order = q1.selectbox("Scene", [s.order for s in sb.scenes], key=f"qc_scene{pid}")
            qc_slot = f"qc{pid}{order}"
            qc_upload_key = f"qc_clip{pid}{order}{nonce(qc_slot)}"
            new_asset = q2.file_uploader(
                "Asset mới (video hoặc ảnh)",
                key=qc_upload_key,
                type=exts("Video") + exts("Image"),
            )
            new_kind = asset_type_for(new_asset.name) if new_asset else None
            qc_source_key = f"qc_src{pid}{order}{new_kind}"
            q1.selectbox(
                "Source",
                ["muse", "upload", "generated"] if new_kind == "video" else ["upload", "generated"],
                key=qc_source_key,
            )
            if new_asset:
                st.button(
                    f"Thay asset scene {order} & render lại",
                    type="primary",
                    on_click=act_qc_replace,
                    args=(pid, order, qc_upload_key, qc_source_key, qc_slot, bgm_slot),
                )
        muse = st.number_input(
            "Chi phí Muse thực tế cho video này (USD)",
            min_value=0.0,
            value=float(p.cost.muse_estimated_cost),
            step=0.05,
        )
        if muse != p.cost.muse_estimated_cost:
            st.button("Lưu chi phí Muse", on_click=act, args=("Lưu chi phí", F.set_muse_cost, pid, muse))

# ---------------------------------------------------------------- publish
with tab_pub:
    if yt == "not_configured":
        st.info(
            "YouTube not configured: lưu file OAuth client JSON (Desktop app) vào "
            f"{S.youtube_client_secret_file} hoặc điền YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET trong .env.local "
            "(xem docs/MANUAL_SETUP.md). Render vẫn dùng bình thường."
        )
    else:
        if yt == "not_connected":
            st.warning("Chưa kết nối kênh (hoặc token đã hết hạn/bị thu hồi): bấm Connect YouTube.")
        st.button("Connect YouTube (mở trình duyệt)", on_click=act_connect)
        st.radio("Privacy", ["private", "unlisted"], horizontal=True, key=f"privacy{pid}")
        st.caption("Dự án Google API chưa được xác minh sẽ bị YouTube ép video về private, kể cả khi chọn unlisted.")
        force = bool(p.youtube_video_id) and st.checkbox(
            "Upload lại (tạo thêm một video mới trên YouTube)", key=f"reupload-{p.youtube_video_id}"
        )
        already_uploaded = bool(p.youtube_video_id) and not force
        can_upload = yt == "connected" and p.render.status == "done" and not already_uploaded
        st.button(
            "Upload lên YouTube",
            type="primary",
            disabled=not can_upload,
            on_click=act_upload,
            args=(pid, force),
        )
    if p.youtube_url:
        st.success(f"Đã upload (privacy thực tế: {p.publish_privacy}): {p.youtube_url}")
    if p.publish_error:
        st.error(p.publish_error)

# ---------------------------------------------------------------- logs
with tab_log:
    log = pdir / "logs" / "events.log"
    st.code("\n".join(log.read_text(encoding="utf-8").splitlines()[-60:]) if log.is_file() else "(trống)")
    st.json(p.cost.model_dump())
