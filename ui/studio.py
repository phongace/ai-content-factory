"""Streamlit UI for the POC: `uv run streamlit run ui/studio.py`.

Flow: Create -> Plan -> Storyboard (copy Muse prompts, upload a video or image per scene, voice) -> Render -> QC
-> Publish.
Deliberately plain: internal tool, one user, no timeline editor.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from factory import media
from factory.config import Settings, get_settings
from factory.pipeline import AUDIO_EXT, Factory
from factory.publish import youtube
from factory.qc import qc_warnings
from factory.render import check_ready
from factory.schema import IMAGE_EXT, VIDEO_EXT, Project, SceneState, asset_type_for
from factory.store import NotFound

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


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("🎬 Content Factory")
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
