# Implementation status

Cập nhật: 2026-10-22, branch `feature/poc-video-factory-v1`. Stack: Python + Streamlit + `mpt==1.3.8`.
Status: DONE / PARTIAL / BLOCKED_EXTERNAL / NOT_STARTED.
Kiểm chứng: 75 test tự động pass (66 logic + 9 render thật bằng FFmpeg), ruff + pyright sạch.

## Backlog (02_Backlog)

| Task ID | Status | Notes | Blocker | Manual Action |
|---|---|---|---|---|
| P0-01 | DONE | Streamlit app + CLI + env + folder local. Đổi Next.js → Python theo quyết định 2026-10-22 | | |
| P0-02 | DONE | `factory/media.py`: ffprobe/ffmpeg wrapper + preflight kiểm libx264, aac, ass, sidechaincompress, loudnorm | | Mac: `brew install ffmpeg` |
| P0-03 | DONE | `project.json` + `storyboard.json`, atomic write, reload sau restart, list, bỏ qua folder hỏng | | |
| P0-04 | PARTIAL | Schema + validate (4–6 scene, 5–9s, tổng ±25%) + retry gửi lỗi về cho Claude (tool call ép JSON). Test bằng HTTP giả + MockPlanner | Chưa có API key để chạy thật | Điền `ANTHROPIC_API_KEY`, chạy 3 topic |
| P0-05 | PARTIAL | Prompt + title/description/hashtags trong cùng 1 call | Cần đánh giá chất lượng output thật | Duyệt 3 storyboard thật |
| P0-06 | BLOCKED_EXTERNAL | Adapter VoiceStudio (OpenAI-compatible, dùng lại transport của MPT), báo lỗi rõ khi server tắt (TC-10 pass). Upload audio tay chạy được | VoiceStudio chưa cài | Xem MANUAL_SETUP §1 |
| P0-07 | DONE | ffprobe đo `actual_audio_duration` từng scene, lưu cùng narration gốc để phát hiện voice cũ | | |
| P0-08 | DONE | Storyboard UI: sửa narration/prompt, nút copy prompt, upload/thay clip, preview, ảnh tĩnh fallback | | |
| P0-09 | DONE | Phụ đề theo scene từ narration + duration thật, không overlap, không vượt ranh giới scene, ASS + SRT | | |
| P0-10 | DONE | MPT `VideoProject`: crop 9:16 không méo, loop clip ngắn, trim clip dài, concat, 30fps H.264/AAC | | |
| P0-11 | DONE | Pass cuối: sidechain ducking nhạc dưới voice + loudnorm -14 LUFS (đo được -14.0). Không nhạc vẫn render | | Chọn nhạc royalty-free |
| P0-12 | DONE | Burn ASS, font Be Vietnam Pro (OFL) có dấu tiếng Việt, đặt cao tránh UI Shorts | Chưa xem trên điện thoại thật | Xem thử 1 video trên phone |
| P0-13 | DONE | QC: preview, duration, size, status, lỗi, cảnh báo. Thay clip 1 scene → render lại, reuse 9 stage, không chạy lại planner/voice | | |
| P0-14 | BLOCKED_EXTERNAL | OAuth desktop flow, token local, tự refresh, trạng thái not_configured / not_connected | Chưa có OAuth client | MANUAL_SETUP §4 |
| P0-15 | BLOCKED_EXTERNAL | Upload resumable, private mặc định, unlisted cho phép, public bị chặn, lưu video ID/URL. Test bằng MockPublisher | Chưa test API thật | Sau §4: upload 1 video private |
| P0-16 | DONE | `logs/events.log`, status/stage/error theo stage, cost Claude/Muse/Voice/render ms | | |
| P0-17 | PARTIAL | E2E offline (`factory demo`) + test tự động. E2E thật Topic A/B/C chưa chạy | Cần key, VoiceStudio, clip Muse, OAuth | Chạy 3 topic thật |
| P0-18 | PARTIAL | RUNBOOK, MANUAL_SETUP, CLAUDE.md xong | Fix blocker sau lần chạy thật | |

## Test cases (05_Test_Cases)

| TC | Status | Evidence |
|---|---|---|
| TC-01 | PASS (mock) | `tests/test_planner.py::test_tc01_*` (vi/en × 20/30/45s) |
| TC-02 | PASS | `test_tc02_scene_over_9s_rejected`, `test_scene_plan_respects_limits` (20→45s) |
| TC-03 | PASS (mock voice) | `tests/test_voice.py::test_tc03_*` |
| TC-04 | PASS | `tests/test_render.py::test_tc04_tc08_*` (audio các scene khác không đổi hash) |
| TC-05 | PASS | `test_tc05_output_spec_mixed_sources` (1280x720, 1080x1920, 640x480, ảnh PNG) |
| TC-06 | PASS | `test_tc06_timing_follows_audio` (clip 3s < audio, clip 14s > audio) |
| TC-07 | PASS | `tests/test_subtitles.py::test_tc07_*` |
| TC-08 | PASS | `test_tc04_tc08_*` (render #2, reuse ≥9 stage) |
| TC-09 | PASS (mock) | `tests/test_publish.py::test_tc09_*`, API thật chờ OAuth |
| TC-10 | PASS | `test_tc10_voicestudio_down_fails_clearly_and_retry_works` |
| TC-11 | PASS | `test_tc11_*` (từ chối lúc upload + báo đúng scene lúc render) |
| TC-12 | NOT_STARTED | Cần đủ credential + clip thật |

## Đã biết / chưa xác minh

- Edge TTS (giọng Việt miễn phí, chỉ để dev) **chưa test được** trong sandbox vì proxy SSL. Trên máy thật thường chạy, nếu lỗi thì dùng mock hoặc upload audio.
- Phụ đề chia theo số chữ nên có lúc ngắt giữa từ ghép tiếng Việt (ví dụ "bắt / đầu"). Word-level là Phase 2.
- Upload YouTube từ project chưa audit sẽ bị khoá private (chính sách Google), xem MANUAL_SETUP §4.
