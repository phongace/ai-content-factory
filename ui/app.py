"""Streamlit UI for the POC: `uv run streamlit run ui/app.py`.

Flow: Create -> Plan -> Storyboard (copy Muse prompts, upload a video or image per scene, voice) -> Render -> QC
-> Publish.
Deliberately plain: internal tool, one user, no timeline editor.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from factory import media
from factory.config import get_settings
from factory.pipeline import AUDIO_EXT, Factory
from factory.publish import youtube
from factory.qc import qc_warnings
from factory.render import check_ready
from factory.schema import IMAGE_EXT, VIDEO_EXT, Project, SceneState

st.set_page_config(page_title="AI Content Factory", page_icon="🎬", layout="wide")
F = Factory()
S = get_settings()


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


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🎬 Content Factory")
    pf = preflight()
    planner_mode = "Claude" if S.anthropic_api_key and S.planner != "mock" else "Mock (chưa có ANTHROPIC_API_KEY)"
    st.caption(
        f"FFmpeg: {'✅' if not pf['problems'] else '❌ ' + ', '.join(pf['problems'])}  \n"
        f"Planner: {planner_mode}  \nVoice mặc định: {S.voice_provider}  \n"
        f"YouTube: {youtube.status(S)}"
    )
    with st.form("create", clear_on_submit=True):
        st.subheader("Project mới")
        topic = st.text_input("Topic")
        duration = st.slider("Thời lượng mục tiêu (s)", 20, 45, 30)
        language = st.radio("Ngôn ngữ", ["vi", "en"], horizontal=True)
        style = st.text_input("Visual style", "cinematic, realistic, soft natural light")
        if st.form_submit_button("Tạo project", type="primary") and topic.strip():
            p = run("Tạo project", F.create_project, topic.strip(), duration, language, style)
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

pid = st.session_state.get("pid")
if not pid:
    st.info("Tạo project ở sidebar để bắt đầu.")
    st.stop()

p = F.store.get(pid)
sb = F.store.get_storyboard(pid)
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
    if cols[0].button("Generate plan" if sb is None else "Re-plan (xoá clip/voice cũ)", type="primary"):
        run("Generate plan", F.plan, pid)
        st.rerun()
    if sb is not None:
        provider = cols[1].selectbox(
            "Voice provider",
            ["mock", "edge", "voicestudio"],
            index=["mock", "edge", "voicestudio"].index(S.voice_provider)
            if S.voice_provider in ("mock", "edge", "voicestudio")
            else 0,
        )
        if cols[2].button("Generate voice cho TẤT CẢ scene"):
            run("Generate voice", F.generate_voice, pid, None, provider)
            st.rerun()
        if provider == "edge":
            st.warning("Edge TTS chỉ để test, không dùng cho video monetize (license không rõ).")

        with st.expander("YouTube metadata", expanded=False):
            title = st.text_input("Title", sb.title, max_chars=100)
            desc = st.text_area("Description", sb.description)
            tags = st.text_input("Hashtags", " ".join(sb.hashtags))
            st.caption(f"Hook: {sb.hook}")
            if st.button("Lưu metadata"):
                run("Lưu metadata", F.edit_metadata, pid, title, desc, tags.split())
                st.rerun()

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
                    narration = st.text_area("Narration", s.narration, key=f"n{pid}{s.order}", height=90)
                    prompt = st.text_area("Visual prompt (Muse)", s.video_prompt, key=f"v{pid}{s.order}", height=110)
                    if st.button("Lưu scene", key=f"save{s.order}"):
                        run(f"Lưu scene {s.order}", F.edit_scene, pid, s.order, narration, prompt)
                        st.rerun()
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
                    asset = st.file_uploader(f"Upload {kind}", key=f"c{pid}{s.order}{kind}", type=exts(kind))
                    source = st.selectbox(
                        "Source",
                        ["muse", "upload", "generated"] if kind == "Video" else ["upload", "generated"],
                        key=f"src{pid}{s.order}{kind}",
                    )
                    if asset and st.button("Replace Asset" if s.asset_path else "Attach Asset", key=f"ac{s.order}"):
                        run(f"Gắn asset scene {s.order}", F.attach_asset, pid, s.order, save_upload(asset), source)
                        st.rerun()
                    if s.asset_path and st.button("Remove Asset", key=f"rm{s.order}"):
                        run(f"Xoá asset scene {s.order}", F.remove_asset, pid, s.order)
                        st.rerun()
                    if s.audio_file:
                        st.audio(str(pdir / s.audio_file))
                        st.caption(f"voice: {s.audio_source}")
                    vc1, vc2 = st.columns(2)
                    if vc1.button("Tạo voice", key=f"gv{s.order}"):
                        run(f"Voice scene {s.order}", F.generate_voice, pid, [s.order], provider)
                        st.rerun()
                    audio = vc2.file_uploader(
                        "hoặc upload audio",
                        key=f"a{pid}{s.order}",
                        type=[e.lstrip(".") for e in AUDIO_EXT],
                        label_visibility="collapsed",
                    )
                    if audio and st.button("Dùng audio này", key=f"ua{s.order}"):
                        run(f"Import voice scene {s.order}", F.import_voice, pid, s.order, save_upload(audio))
                        st.rerun()

# ---------------------------------------------------------------- render & QC
with tab_render:
    if sb is None:
        st.info("Chưa có storyboard.")
    else:
        problems = check_ready(pdir, sb)
        for msg in problems:
            st.warning(msg)
        rc1, rc2 = st.columns(2)
        bgm = rc1.file_uploader("Nhạc nền (tuỳ chọn, royalty-free)", type=["mp3", "wav", "m4a", "aac"])
        vol = rc2.slider("BGM volume", 0.0, 0.6, float(S.bgm_volume), 0.05)
        if st.button("Render", type="primary", disabled=bool(problems)):
            run("Render", F.render, pid, save_upload(bgm) if bgm else None, vol)
            st.rerun()

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
            new_asset = q2.file_uploader(
                "Asset mới (video hoặc ảnh)", key=f"qc_clip{pid}{order}", type=exts("Video") + exts("Image")
            )
            if new_asset and st.button(f"Thay asset scene {order} & render lại", type="primary"):
                if run(f"Thay asset scene {order}", F.attach_asset, pid, order, save_upload(new_asset)):
                    run("Render lại", F.render, pid, save_upload(bgm) if bgm else None, vol)
                st.rerun()
        muse = st.number_input(
            "Chi phí Muse thực tế cho video này (USD)",
            min_value=0.0,
            value=float(p.cost.muse_estimated_cost),
            step=0.05,
        )
        if muse != p.cost.muse_estimated_cost and st.button("Lưu chi phí Muse"):
            run("Lưu chi phí", F.set_muse_cost, pid, muse)
            st.rerun()

# ---------------------------------------------------------------- publish
with tab_pub:
    yt = youtube.status(S)
    if yt == "not_configured":
        st.info(
            "YouTube not configured: lưu file OAuth client JSON (Desktop app) vào "
            f"{S.youtube_client_secret_file} hoặc điền YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET trong .env.local "
            "(xem docs/MANUAL_SETUP.md). Render vẫn dùng bình thường."
        )
    else:
        if yt == "not_connected":
            st.warning("Chưa kết nối kênh (hoặc token đã hết hạn/bị thu hồi): bấm Connect YouTube.")
        if st.button("Connect YouTube (mở trình duyệt)"):
            run("Kết nối YouTube", youtube.connect, S)
            st.rerun()
        privacy = st.radio("Privacy", ["private", "unlisted"], horizontal=True)
        st.caption("Dự án Google API chưa được xác minh sẽ bị YouTube ép video về private, kể cả khi chọn unlisted.")
        force = bool(p.youtube_video_id) and st.checkbox(
            "Upload lại (tạo thêm một video mới trên YouTube)", key=f"reupload-{p.youtube_video_id}"
        )
        already_uploaded = bool(p.youtube_video_id) and not force
        can_upload = yt == "connected" and p.render.status == "done" and not already_uploaded
        if st.button("Upload lên YouTube", type="primary", disabled=not can_upload):
            run("Upload YouTube", F.publish, pid, youtube.YouTubePublisher(S), privacy, force=force)
            st.rerun()
    if p.youtube_url:
        st.success(f"Đã upload (privacy thực tế: {p.publish_privacy}): {p.youtube_url}")
    if p.publish_error:
        st.error(p.publish_error)

# ---------------------------------------------------------------- logs
with tab_log:
    log = pdir / "logs" / "events.log"
    st.code("\n".join(log.read_text(encoding="utf-8").splitlines()[-60:]) if log.is_file() else "(trống)")
    st.json(p.cost.model_dump())
