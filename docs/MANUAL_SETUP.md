# Manual setup — chỉ những việc cần người làm

## 1. VoiceStudio (P0-06)

Mình không cài được VoiceStudio từ sandbox, phần code adapter đã xong và đã test trường hợp VoiceStudio chưa chạy (báo lỗi rõ, retry được).

1. Cài VoiceStudio (repo `debpalash/VoiceStudio`, bản desktop hoặc chạy backend-only theo README của họ). Kiểm tra lại README vì project đổi nhanh.
2. Mở app, tải model, chọn engine và voice tiếng Việt muốn dùng.
3. **Quyết định license**: VoiceStudio là AGPL-3.0 nhưng mình chỉ gọi qua HTTP local nên không ảnh hưởng code. Thứ quyết định dùng thương mại được hay không là **license của engine/voice** đang chọn. Kiểm tra license từng engine trước khi dùng cho kênh monetize.
4. Kiểm tra API local (mặc định OpenAI-compatible ở cổng 3900):
   ```bash
   curl -s http://127.0.0.1:3900/v1/audio/speech -H "Content-Type: application/json" \
     -d '{"model":"tts-1","voice":"<voice_id>","input":"Xin chào","response_format":"mp3"}' -o test.mp3
   ```
5. Điền `.env.local`:
   ```
   VOICE_PROVIDER=voicestudio
   VOICESTUDIO_BASE_URL=http://127.0.0.1:3900/v1
   VOICESTUDIO_VOICE=<voice_id>
   VOICESTUDIO_MODEL=<model nếu VoiceStudio yêu cầu, mặc định tts-1>
   ```
6. Chạy `uv run factory voice <project_id> --scene 1 --provider voicestudio`. Lỗi gì gửi lại mình nội dung lỗi.

Fallback nếu API không khớp: export audio từ UI VoiceStudio rồi upload từng scene trong app.

## 2. Muse (mỗi video)

1. Tab Storyboard: bấm copy ở khung prompt từng scene.
2. Generate trên Muse, tải mp4.
3. Upload vào đúng scene. Ghi tổng chi phí Muse ở tab Render & QC.

## 3. Claude API key (P0-04, P0-05)

1. Tạo key ở Claude Console, điền `ANTHROPIC_API_KEY` trong `.env.local`.
2. Kiểm tra giá model hiện tại và chỉnh `ANTHROPIC_INPUT_PRICE_PER_MTOK` / `ANTHROPIC_OUTPUT_PRICE_PER_MTOK` nếu khác (chỉ dùng để ước tính chi phí).
3. Chạy plan 3 topic khác nhau để duyệt chất lượng prompt (acceptance P0-04/P0-05).

## 4. YouTube (P0-14, P0-15)

1. Google Cloud Console → tạo project → bật **YouTube Data API v3**.
2. OAuth consent screen: loại External, trạng thái Testing, thêm Gmail sở hữu kênh vào **Test users**.
3. Credentials → Create OAuth client ID → loại **Desktop app** → **Download JSON** (file có mục `installed`). Không cần khai redirect URI: flow desktop dùng `http://localhost` với một port trống tự chọn. Client loại `web` không dùng được.
4. Lưu file JSON đó đúng tại `data/secrets/youtube_client_secret.json` (đã gitignore) và `chmod 600`. Muốn để chỗ khác thì đặt `YOUTUBE_CLIENT_SECRET_FILE=<đường dẫn>` trong `.env.local` (đường dẫn tương đối tính từ root repo). Cặp `YOUTUBE_CLIENT_ID` + `YOUTUBE_CLIENT_SECRET` trong `.env.local` vẫn chạy nhưng chỉ là fallback khi file không tồn tại. Không dán nội dung file này vào chat, log hay git.
5. `uv run factory youtube-connect` (hoặc nút Connect trong UI). Lệnh in khối `HUMAN ACTION REQUIRED`, mở trình duyệt và chờ tối đa 300 giây: chọn đúng Google account quản lý kênh, bấm Continue / Allow, quay lại terminal khi Google báo authorization completed. Nếu trình duyệt không tự mở, mở URL mà lệnh in ra. Chỉ xin quyền `https://www.googleapis.com/auth/youtube.upload`.
6. Sau khi đồng ý, lệnh tự kiểm tra token bằng Google tokeninfo (có đúng scope `youtube.upload`, `expires_in > 0`) rồi mới lưu token ở `data/secrets/youtube_token.json` (mode 600) và in JSON: `state`, `scopes`, `access_token_expires_in_seconds`, `access_token_expires_at`, `has_refresh_token`. Không in token hay client secret.
7. Kiểm tra lại bất cứ lúc nào: `uv run factory youtube-status` in `connected` / `not_connected` / `not_configured`, scope đã cấp và hạn access token (exit code 0 chỉ khi `connected`). Access token hết hạn thì tự refresh, không cần connect lại.
8. Upload thử một video private từ CLI: `uv run factory publish <project_id> --privacy private --title "Test private upload" --description "Kiểm tra OAuth, xoá sau khi test"`. Lệnh in URL, `video_id`, `privacy` thực tế, `upload_status`, `published_at`; cùng các giá trị này nằm trong `data/projects/<id>/project.json`. Bỏ `--title`/`--description` thì dùng metadata của storyboard. Project đã upload rồi thì cần thêm `--force` để tạo video mới.

Vì chỉ có scope `youtube.upload`, app không gọi được API đọc (ví dụ `channels.list(mine=True)` trả insufficient scope), nên không hiển thị tên kênh: kiểm tra video đã lên đúng kênh trong YouTube Studio.

Lưu ý quan trọng:
- Google ghi rõ video upload qua `videos.insert` từ API project **chưa qua audit** (tạo sau 28/7/2020) sẽ bị khoá ở chế độ **private**. Nghĩa là upload POC sẽ luôn private (đúng mục tiêu test), chọn unlisted cũng bị ép private. Muốn upload public/unlisted tự động thì phải xin audit (Phase 2).
- App ở trạng thái Testing thì refresh token thường hết hạn sau khoảng 7 ngày, khi đó chạy lại `youtube-connect`.
- Upload tốn quota API, quota mặc định chỉ đủ một số ít video/ngày. Xem quota trong Console. Hết quota thì lệnh báo lỗi và không tự thử lại; quota reset lúc nửa đêm giờ Pacific.
- Ý nghĩa từng thông báo lỗi YouTube và cách xử lý: xem `docs/POC_RUNBOOK.md` mục "Lỗi YouTube".

## 5. Nhạc nền

Chọn nguồn royalty-free có license rõ (ví dụ YouTube Audio Library). Không dùng nhạc bundled trong MoneyPrinterTurbo.
