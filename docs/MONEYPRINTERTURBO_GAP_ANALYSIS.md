# MoneyPrinterTurbo Gap Analysis cho AI Content Factory POC

Ngày audit: 2026-10-21
Reference repo: https://github.com/harry0703/MoneyPrinterTurbo (HEAD `90f7701`, 2026-10-20, package `mpt` 1.3.8 trên PyPI)
License: MIT, `Copyright (c) 2024 Harry`

## 0. Phạm vi và giới hạn của lần audit này

| Mục | Trạng thái |
|---|---|
| Clone + đọc code MoneyPrinterTurbo (MPT) | Đã làm, vào `/tmp/MoneyPrinterTurbo`, không đụng repo của bạn |
| Chạy thử renderer scene-level của MPT với fixture tự tạo | Đã làm (kết quả ở mục 2) |
| Chạy test suite của MPT | 265/265 test core (video_project, voice, llm) pass. Full suite (~1.800 test) chạy được ~90% trong giới hạn 285s của sandbox, thấy 1 fail chưa điều tra |
| Đọc `AI_Content_Factory_POC_Tracker.xlsx` | Đã đọc đủ 9 sheet (bản upload 2026-10-21), không sửa file. Đối chiếu chi tiết ở mục 10 |
| Audit repo `phongace/ai-content-factory` | **Chưa được**: repo private (GitHub trả 404 khi clone ẩn danh) |

## 1. MPT architecture (tóm tắt từ code thật)

```
main.py ─ FastAPI (app/asgi.py, app/router.py)  → /api/v1/videos, /audio, /subtitle, /tasks, /stream, /download
webui/Main.py ─ Streamlit, 8.477 dòng, 1 file
cli.py ─ CLI 1.817 dòng
app/services/
  task.py           pipeline chính: script → terms → audio → subtitle → materials → combine → final (MoviePy)
  llm.py            37 LLM provider (registry trong app/models/llm_provider.py), có "anthropic" và "claude_code"
  voice.py          TTS dispatch: edge, azure v1/v2, siliconflow, gemini, mimo, minimax, elevenlabs, fish,
                    chatterbox/kokoro (OpenAI-compatible /audio/speech), voxcpm, no-voice
  subtitle.py       faster-whisper (tuỳ chọn) + correct()
  video.py          MoviePy composer cho pipeline chính (1 audio dài, cắt material theo max_clip_duration)
  video_project.py  **renderer scene-level thuần FFmpeg, stdlib-only**: mỗi scene = footage + audio riêng,
                    loop/trim theo audio, crop cover 9:16, BGM, revision bất biến, reuse artifact theo SHA-256,
                    lock, cancel, retry
  material.py       Pexels/Pixabay/Coverr/local + AI video: Seedance, MuAPI, WaveSpeed, Ofox, Minimax, OpenAI image
  bgm.py            validate/upload BGM
  upload_post.py    cross-post qua dịch vụ trả phí Upload-Post (TikTok/IG/YouTube), default privacy = public
  state.py          task state Memory (default) hoặc Redis (enable_redis=false mặc định)
```

Điểm quan trọng nhất: **pipeline chính của MPT KHÔNG phải scene-level** (1 script dài → 1 audio dài → cắt stock footage ngẫu nhiên/tuần tự). Nhưng `video_project.py` (mới, có từ 1.3.8) thì đúng mô hình scene-level mà POC cần, và nó không phụ thuộc gì ngoài Python stdlib + FFmpeg.

## 2. Kết quả chạy thử renderer `video_project.py`

Fixture tự generate bằng FFmpeg: clip landscape 1280x720 dài 3s, clip portrait 1080x1920 dài 12s, clip 640x480 dài 4s, 3 audio 6s/7s/8s, BGM 30s, 1 file mp4 hỏng.

| Test case | Kết quả |
|---|---|
| Landscape source → 9:16 không méo | PASS (scale cover + crop) |
| Mixed resolution | PASS |
| Clip ngắn hơn audio (3s vs 6s) | PASS, loop |
| Clip dài hơn audio (12s vs 7s) | PASS, trim |
| Tổng duration = tổng audio | PASS, 21,02s = 6+7+8 |
| Output spec | PASS, H.264 1080x1920 30fps, AAC 48kHz stereo |
| BGM optional | PASS, có amix (volume cố định, **chưa có ducking/normalize**) |
| Replace 1 scene → render lại | PASS, 8 stage được reuse, render lại 9s so với 18s lần đầu |
| Corrupt input | PASS, fail rõ ràng: "Invalid data found when processing input" |
| Subtitle | **KHÔNG CÓ**, docs của module ghi rõ chưa làm subtitle |

Thử thêm subtitle ước lượng của MPT (`voice.populate_legacy_submaker_with_full_text` + `voice.create_subtitle`) với narration 1 scene + duration 6s: ra SRT 3 cue, chia theo dấu câu, không overlap, kết thúc đúng 6,000s. Dùng lại được per-scene rồi cộng offset.

## 3. Gap matrix

Status: REUSE / ADAPT / BUILD_NEW / IGNORE

| POC Requirement | MoneyPrinterTurbo Implementation | Status | Action |
|---|---|---|---|
| Content planner (P0-04, P0-05) | `llm._generate_response()` hỗ trợ 37 provider kể cả Anthropic, có retry. Nhưng `generate_script()` chỉ trả text đoạn văn, không có JSON scene | ADAPT | Reuse transport `_generate_response` + helper `_strip_code_fence`/`_extract_embedded_json`. Viết mới prompt storyboard JSON + Pydantic schema (4–6 scene, target mỗi scene 5–9s theo P0-04/TC-02, tổng 20–45s) + retry khi invalid + MockPlanner |
| Storyboard (P0-08) | Không có khái niệm storyboard/scene trong pipeline chính. `video_project` có manifest scene (id, footage, audio, narration) | BUILD_NEW | Project store JSON (`project.json` + `storyboard.json`) chứa narration, videoPrompt, targetDuration, asset state. Map sang manifest `video_project` khi render |
| Scene narration | Pipeline chính 1 narration dài. `video_project` nhận audio riêng từng scene | ADAPT | Narration nằm trong storyboard, mỗi scene sinh audio riêng |
| Voice (P0-06, P0-07, TC-10) | `voice.tts()` dispatch nhiều provider, trả file + SubMaker. Có `no-voice` (silent theo ước lượng thời lượng) dùng làm mock. Edge TTS có giọng `vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural` | REUSE | Wrapper gọi `tts()` từng scene → `audio/scene_XX.wav`, ffprobe lấy `actualAudioDuration`. Manual upload = copy file + ffprobe. `_openai_compatible_tts` chỉ log lỗi rồi trả `None` → wrapper phải raise lỗi rõ để đạt TC-10 ("stage fail rõ, retry được") |
| VoiceStudio | Không có provider tên VoiceStudio. Nhưng có `_openai_compatible_tts()` dùng chung cho Chatterbox/Kokoro (POST `{base_url}/audio/speech`). VoiceStudio (debpalash) expose API OpenAI-compatible ở `localhost:3900/v1` | ADAPT | Provider `voicestudio:<voice>` khoảng 30 dòng gọi lại `_openai_compatible_tts` (hàm private, pin version). Nếu VoiceStudio nặng quá, fallback Kokoro/Chatterbox đã có sẵn adapter |
| Manual Muse workflow (P0-08) | Không có Muse. Có `video_source = "local"` cho pipeline chính và `video_project` nhận footage local bất kỳ | BUILD_NEW (nhỏ) | UI: Copy Prompt + upload/replace clip vào `scenes/scene_XX.mp4` + ffprobe validate. Không automation. Fallback ảnh tĩnh (01_Flow bước 5): `video_project` không nhận PNG/JPG nên cần 1 lệnh FFmpeg đổi ảnh thành clip, việc nhỏ |
| Subtitle (P0-09) | Có timeline ước lượng từ text + duration (`populate_legacy_submaker_with_full_text`), có `create_subtitle` chia theo dấu câu, có Whisper tuỳ chọn. `video_project` không burn subtitle | ADAPT | Sinh SRT từng scene bằng hàm MPT, cộng offset theo `actualAudioDuration`, merge → ASS 1 style (font Be Vietnam Pro có sẵn, OFL), burn bằng filter `ass` ở pass cuối |
| FFmpeg render (P0-10, P0-11, P0-12) | `video_project.VideoProject` — đã verify ở mục 2 | REUSE | Gọi `create_revision()` + `render()` trực tiếp từ Python, không fork. Thêm 1 pass cuối: subtitle burn + audio post |
| Music | `video_project` amix BGM volume cố định, loop theo timeline. `bgm.py` validate file upload. Thư viện nhạc bundled lấy từ YouTube, bản quyền không rõ | ADAPT | Thêm sidechain ducking + loudnorm (-14 LUFS) trong pass cuối. **Không dùng `resource/songs`** cho kênh monetize |
| QC (P0-13, TC-04, TC-08) | `video_project status/plan/compare` cho biết stage nào reuse/fail. WebUI có preview video nhưng gắn với pipeline chính | ADAPT | Màn hình QC mới: preview, duration, file size, status, error, nút Replace Scene X → Render Again (dùng revision mới, reuse stage cũ) |
| YouTube upload (P0-14, P0-15, TC-09) | Chỉ có `upload_post.py` qua dịch vụ trả phí Upload-Post, mặc định public, mặc định cross-post TikTok/IG | BUILD_NEW | `YouTubePublisher` dùng YouTube Data API v3 trực tiếp: OAuth installed-app, token local, resumable upload, default private, cho phép unlisted, persist `youtubeVideoId/url/publishStatus/publishedAt`. Báo "YouTube not configured" nếu thiếu credential |
| Cost tracking (P0-16, 06_Cost_Tracking) | Không có. `_generate_response` không trả token usage | BUILD_NEW | Field cost trong `project.json`. Claude cost ước lượng theo số ký tự (≈4 ký tự/token) hoặc gọi Anthropic SDK trực tiếp nếu cần số chính xác. Muse cost nhập tay, render time đo bằng timer |
| Project storage (P0-01..03) | `video_project` đã có atomic JSON, lock file, revision. Task state pipeline chính là memory/Redis | ADAPT | Project store riêng bằng JSON file theo layout `data/projects/{id}/...`, tận dụng pattern atomic write. Không Redis |
| UI | Streamlit 8.477 dòng 1 file, bám pipeline chính | IGNORE (không fork) | Viết Streamlit app nhỏ riêng (ước 400–700 dòng) chỉ cho flow POC. Streamlit đã là dependency của MPT nên không thêm gì |

## 4. So sánh chiến lược

| Tiêu chí | A: Build từ scratch (Next.js) | B: MPT làm engine (Python) |
|---|---|---|
| Effort ước tính (min/base/max) | 33 / 60 / 90h (số của tracker) | 25 / 41,5 / 67,5h (chi tiết mục 7) |
| Code mới | ~3.500–5.000 dòng TS (renderer, TTS adapter, subtitle, UI, YouTube, storage, test) | ~1.500–2.500 dòng Python (planner, project store, wrapper, subtitle merge, UI, YouTube, test) |
| Integration risk | Thấp về dependency, **cao ở renderer** (loop/trim/crop/mixed res/audio là phần dễ bug nhất) | Renderer đã verify chạy được. Risk chuyển sang: import hàm private, MPT update rất nhanh (commit gần như hằng ngày) → phải pin version |
| Maintainability | Code của mình 100%, ít dependency | Phụ thuộc package lớn, nhưng chỉ dùng 4 module ổn định (`video_project`, `voice`, `llm`, `bgm`). Pin `mpt==1.3.8` |
| Provider flexibility | Tự viết từng adapter | 37 LLM + ~12 TTS có sẵn, đổi bằng config |
| Test coverage | Bắt đầu từ 0 | Module reuse đã có test (265 test core pass). Mình chỉ test phần mới |
| Speed to first video | Sau ~25–30h (khi renderer xong) | Ngày đầu tiên (renderer chạy ngay với fixture, voice dùng Edge TTS hoặc no-voice) |
| Technical debt | Ít | Footprint cài đặt nặng (MoviePy, faster-whisper, onnxruntime, Streamlit… cài lần đầu >5 phút), namespace `app` của MPT chiếm tên package top-level |

Tracker ghi Next.js nhưng Next.js không mang lại gì cho POC nội bộ 1 người dùng. Streamlit đủ cho storyboard + upload + preview.

## 5. License review (MIT)

MIT chỉ yêu cầu giữ copyright notice + permission notice trong "all copies or substantial portions".

| Trường hợp | Việc phải làm |
|---|---|
| Dùng MPT làm dependency (`mpt==1.3.8` từ PyPI hoặc pin git commit) — **khuyến nghị** | Wheel đã kèm `LICENSE`. Thêm `THIRD_PARTY_NOTICES.md` ở root ghi: tên project, URL, version/commit, "MIT License, Copyright (c) 2024 Harry", kèm full text MIT |
| Copy/adapt file nào (ví dụ nếu sau này phải patch `video_project.py`) | Đặt trong `vendor/moneyprinterturbo/`, giữ nguyên header, thêm dòng đầu file: `Adapted from MoneyPrinterTurbo (MIT, Copyright (c) 2024 Harry), commit <sha>, modified by Nova Digital`, copy `LICENSE` vào cùng thư mục, liệt kê file trong `THIRD_PARTY_NOTICES.md` |
| Font | Chỉ dùng Be Vietnam Pro / Charm (OFL). **Không commit/dùng** Microsoft YaHei, STHeiti, UTM Kabel KT (font thương mại có trong `resource/fonts`) |
| Nhạc | **Không dùng** `resource/songs/*` (README của MPT tự ghi nhạc lấy từ YouTube video). Dùng nguồn royalty-free có license rõ |
| VoiceStudio | AGPL-3.0, mình chỉ gọi qua HTTP local, không phân phối → không bị lây license sang code mình. Nhưng license của **từng TTS model/voice** mới quyết định dùng thương mại được không. Đây là quyết định của bạn |
| Edge TTS | Dùng endpoint không chính thức của Microsoft Edge, không có license thương mại rõ → chỉ dùng cho dev/test |

Không có file nào bị copy ở lần audit này.

## 6. Architecture đề xuất (Strategy B)

```
ai-content-factory/
  pyproject.toml            uv, Python 3.11+, dependency: mpt==1.3.8, anthropic (tuỳ), google-api-python-client, google-auth-oauthlib
  THIRD_PARTY_NOTICES.md
  CLAUDE.md                 guardrails POC
  factory/                  (KHÔNG đặt tên `app` để tránh đụng namespace của MPT)
    schema.py               Pydantic: Project, Storyboard, SceneState, Cost
    store.py                data/projects/{id}/project.json, storyboard.json, atomic write
    planner.py              StoryboardPlanner: MptLlmPlanner (dùng mpt llm._generate_response) + MockPlanner
    voice.py                per-scene: mpt voice.tts() | voicestudio (_openai_compatible_tts) | manual upload, ffprobe duration
    subtitles.py            per-scene SRT bằng mpt voice → offset → merge ASS
    render.py               storyboard → manifest → mpt VideoProject.create_revision/render → final pass (ass + ducking + loudnorm)
    publish/youtube.py      Publisher interface + YouTubePublisher + MockPublisher
    cost.py
  ui/app.py                 Streamlit: Projects, Storyboard, Voice, Render/QC, Publish
  tests/                    pytest: planner validation, store, subtitle merge, render fixtures, mock publisher
  data/                     gitignored
```

Flow:

```
Topic → planner.py (Claude JSON, validate) → storyboard.json
      → UI: sửa narration/prompt, Copy Prompt → Muse (tay) → upload clip
      → voice.py từng scene → audio/scene_XX.wav + actualAudioDuration
      → subtitles.py → subtitles/final.ass
      → render.py → VideoProject (scene render, loop/trim, concat, BGM) → final pass → renders/final-short.mp4
      → QC → Replace Scene X → revision mới → render lại (reuse stage cũ)
      → youtube.py (private/unlisted)
```

## 7. Effort ước tính lại theo từng task của tracker

Cột A là số của tracker (Next.js từ scratch). Cột B là ước tính khi dùng MPT làm engine.

| ID | Task | A tracker (min/base/max) | B MPT (min/base/max) | Vì sao khác |
|---|---|---|---|---|
| P0-01 | Bootstrap app + env + folders | 2 / 3 / 4 | 1 / 1,5 / 2 | Streamlit + uv + pin `mpt`, không dựng Next.js |
| P0-02 | FFmpeg preflight + wrapper | 1 / 2 / 3 | 0,5 / 1 / 1,5 | Dùng runner/preflight của MPT |
| P0-03 | project.json + reload | 2 / 3 / 4 | 1,5 / 2,5 / 3,5 | Vẫn build mới, chỉ mượn pattern atomic write |
| P0-04 | Planner schema + validate + retry | 2 / 4 / 6 | 2 / 3 / 5 | Reuse transport + JSON helper |
| P0-05 | Planner prompt + metadata | 1 / 2 / 3 | 1 / 2 / 3 | Như nhau |
| P0-06 | VoiceStudio integration | 2 / 4 / 6 | 1 / 2 / 4 | Reuse `_openai_compatible_tts` |
| P0-07 | Audio duration + persist | 1 / 2 / 3 | 0,5 / 1 / 1,5 | Reuse ffprobe helper |
| P0-08 | Storyboard UI + copy + upload | 2 / 4 / 5 | 2 / 3 / 5 | Streamlit nhanh hơn chút |
| P0-09 | Scene-level ASS/SRT | 1 / 2 / 3 | 0,5 / 1 / 2 | Reuse timeline ước lượng |
| P0-10 | Normalize + trim/loop + concat | 4 / 7 / 10 | 1 / 1,5 / 3 | **Đã verify chạy được**, chỉ viết manifest builder |
| P0-11 | Audio mix + ducking + normalize | 2 / 3 / 5 | 1,5 / 2 / 3 | MPT chỉ amix, phải thêm ducking/loudnorm |
| P0-12 | Burn subtitle + style | 1 / 2 / 4 | 1 / 1,5 / 3 | MPT `video_project` không có, viết pass cuối |
| P0-13 | QC + replace + re-render | 2 / 4 / 6 | 1,5 / 2,5 / 4 | Revision + reuse stage có sẵn |
| P0-14 | OAuth + token local | 2 / 4 / 6 | 2 / 4 / 6 | Build mới cả hai |
| P0-15 | Upload API + privacy + metadata | 2 / 4 / 6 | 2 / 3 / 5 | `google-api-python-client` có resumable upload sẵn |
| P0-16 | Status/error + cost | 1 / 2 / 3 | 1 / 2 / 3 | Như nhau |
| P0-17 | E2E Topic A/B/C | 3 / 5 / 8 | 3 / 5 / 8 | Như nhau |
| P0-18 | Fix blockers + runbook | 2 / 3 / 5 | 2 / 3 / 5 | Như nhau |
| **Tổng** | | **33 / 60 / 90** | **25 / 41,5 / 67,5** | Tiết kiệm ~18h base, chủ yếu ở P0-10 |

Theo milestone của tracker (base hours):

| Milestone | Task | A tracker | B MPT |
|---|---|---|---|
| M0 Bootstrap | P0-01..03 | 8 | 5 |
| M1 Plan + Voice | P0-04..07 | 12 | 8 |
| M2 Scene + Render | P0-08..12 | 18 | 9 |
| M3 QC + Publish | P0-13..15 | 12 | 9,5 |
| M4 E2E hardening | P0-16..18 | 10 | 10 |

Nhận xét thẳng: tiết kiệm effort chỉ ~30% vì OAuth, project state, UI, E2E vẫn phải tự làm dù chọn A hay B. Lợi ích lớn hơn nằm ở risk: 2 risk điểm 9 trong `07_Risks` ("FFmpeg filter/subtitle lỗi cross-platform" và "Scene/audio duration mismatch") rơi đúng vào phần MPT đã có code + test.

## 8. Bỏ qua / không dùng trong POC

`webui/Main.py` (không fork), `cli.py` pipeline chính, `task.py` pipeline chính, material search Pexels/Pixabay/Coverr, toàn bộ AI video provider (Seedance, MuAPI, WaveSpeed, Ofox, Minimax, OpenAI image, LoomLoom, TwelveLabs — để Phase 2 nếu muốn thay Muse tay), `upload_post.py` (SaaS trả phí + TikTok/IG), `state.py` RedisState + `redis_manager.py`, AI music (ElevenLabs music, Sonilo), `kimi_code_oauth.py`, `docs/skill` agent, Docker/GPU, `version_checker.py`, i18n, `resource/songs`, font thương mại.

## 9. Risk chính của Strategy B

1. MPT thay đổi nhanh → pin `mpt==1.3.8` (hoặc commit `90f7701`), chỉ nâng version có chủ đích.
2. Dùng hàm private (`_openai_compatible_tts`, `_generate_response`) → bọc trong 1 file adapter, có test.
3. Cài đặt nặng → chấp nhận được với local-first, không có service chạy nền nào (Redis tắt mặc định).
4. Cost Claude là ước lượng nếu đi qua MPT llm layer → nếu cần số chính xác, gọi Anthropic SDK trực tiếp cho riêng planner.
5. Subtitle burn bằng filter `ass` cần FFmpeg build có libass. Bản Homebrew có sẵn, nhưng preflight P0-02 phải check `ffmpeg -filters | grep ass`.

## 10. Đối chiếu với tracker

### 10.1 Test cases (`05_Test_Cases`)

| TC | Nội dung | Strategy B đáp ứng thế nào | Đã verify? |
|---|---|---|---|
| TC-01 | 4–6 scene JSON valid | Pydantic schema + retry (build mới) | Chưa |
| TC-02 | Không scene nào target >9s | Rule cứng trong schema, prompt yêu cầu 5–9s | Chưa |
| TC-03 | wav + duration từng scene | Wrapper `voice.tts()` + ffprobe | Chưa |
| TC-04 | Replace clip chỉ đổi scene đó | Revision mới, stage scene khác reuse | **PASS** (8 stage reuse) |
| TC-05 | Mixed source → 1080x1920 không méo | `video_project` scale cover + crop | **PASS** |
| TC-06 | Clip 5s, audio 7s | Loop | **PASS** (test 3s clip vs 6s audio) |
| TC-07 | Caption không overlap | Timeline ước lượng MPT + offset per scene | PASS 1 scene, merge chưa viết |
| TC-08 | Re-render sau replace đúng | Revision + render | **PASS** |
| TC-09 | Private upload có ID + URL | YouTubePublisher build mới | Chưa, cần OAuth |
| TC-10 | VoiceStudio down → fail rõ, retry | Wrapper phải raise lỗi (MPT chỉ trả `None`) | Chưa |
| TC-11 | Corrupt mp4 → lỗi hữu ích | `video_project` báo "Invalid data found when processing input" | **PASS** |
| TC-12 | 3 topic upload không sửa code | E2E | Chưa |

### 10.2 Tech decisions cần sửa nếu chọn B (`04_Tech_Decisions`)

| Area | Hiện tại | Đề xuất |
|---|---|---|
| UI | Next.js + TypeScript, KEEP | CHANGE → Streamlit (Python), vì MPT là Python và Streamlit đã là dependency |
| Render | FFmpeg CLI, KEEP | KEEP, nhưng gọi qua `mpt` `VideoProject` + 1 pass FFmpeg cuối |
| Planner | Claude structured JSON, "Not now: Multi-LLM router" | KEEP Claude mặc định. Multi-LLM có sẵn miễn phí qua MPT nhưng không expose trên UI |
| Voice | VoiceStudio, "Not now: multi-provider router" | KEEP. Router của MPT có sẵn nhưng chỉ dùng VoiceStudio + manual (+ Edge TTS cho dev) |
| Các dòng còn lại | | Giữ nguyên |

Thêm 1 dòng đề xuất: `Base engine | MoneyPrinterTurbo mpt==1.3.8 (MIT) | Reuse renderer/TTS/subtitle đã test | Fork WebUI | MPT đổi API hoặc cần patch core | Medium | NEW`.

### 10.3 Risk mới nên thêm vào `07_Risks`

| Risk | Prob | Impact | Mitigation | Fallback |
|---|---|---|---|---|
| MPT đổi API / hàm private | Medium | Medium | Pin `mpt==1.3.8`, adapter 1 file có test | Vendor đúng file cần (giữ MIT notice) |
| Dependency Python nặng, cài lần đầu lâu | High | Low | `uv` + lockfile | Không có |
| Nhạc/font bundled của MPT dính bản quyền | Medium | High | Không dùng `resource/songs`, chỉ font OFL | Nhạc royalty-free tự chọn |

### 10.4 Lỗi phát hiện trong tracker (chưa sửa file)

1. **`06_Cost_Tracking!C8` tính sai Avg cost/video.** Công thức `=AVERAGE(I4:I100)` nhưng cột I có sẵn `=SUM(D:H)` cho mọi dòng trống nên trả 0, kéo trung bình xuống. Hiện hiển thị **0,002887** trong khi đúng phải là **0,28** (0,28 ÷ 97 dòng). `C9` (Avg active min) không bị vì cột J trống thật. Ngoài ra bảng Metric nằm đè lên vùng dữ liệu I4:I100 (dòng 7–12 cũng có `=SUM`). Cách sửa: đổi thành `=IFERROR(AVERAGEIFS(I4:I100,A4:A100,"<>"),0)` và dời bảng Metric ra ngoài vùng dữ liệu.
2. Dòng `Example-001` thiếu ngày, nên xoá khi nhập data thật để không lẫn vào trung bình.

### 10.5 Phase 2 (`08_Phase2_Deferred`)

Kế hoạch B không đụng mục nào. Những thứ MPT có sẵn nhưng thuộc Phase 2 sẽ để tắt: AI video provider (≈ "Automate Muse/video provider"), Whisper (≈ "Word-level subtitles"), `video_count` (≈ "Batch generation"), Upload-Post TikTok/IG (≈ "TikTok/Facebook publisher"), Redis.

## 11. Addendum sau khi implement (2026-10-22)

Những chỗ thực tế khác với kế hoạch ở trên:

| Mục | Kế hoạch | Thực tế | Lý do |
|---|---|---|---|
| Planner transport | Dùng `llm._generate_response` của MPT | Gọi thẳng Anthropic Messages API với tool call bắt buộc | Lấy được token usage chính xác cho cost tracking, JSON đúng cấu trúc ngay từ đầu, không phải ghi API key vào `config.toml` của MPT. Muốn đổi provider thì viết thêm 1 class `ContentPlanner` |
| Subtitle | Dùng `populate_legacy_submaker_with_full_text` + `create_subtitle` | Tự viết ~90 dòng (`factory/subtitles.py`) | Hàm của MPT xoay quanh object SubMaker và ghi file SRT, bọc lại tốn công hơn viết mới cho bài toán chia câu theo scene |
| Phần MPT thực sự dùng | renderer, TTS, subtitle, LLM, BGM | renderer `VideoProject`, transport TTS (`tts`, `_openai_compatible_tts`), font Be Vietnam Pro | Phần reuse lớn nhất (renderer) đúng như dự tính, phần còn lại nhỏ hơn dự tính |
| `video_project` subtitle/ducking | Thêm 1 pass cuối | Đúng như kế hoạch: `ass` + `sidechaincompress` + `loudnorm` | |
