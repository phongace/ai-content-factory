# Third-party notices

## MoneyPrinterTurbo

- Used as a pinned dependency: PyPI package `mpt==1.3.8` (upstream https://github.com/harry0703/MoneyPrinterTurbo).
- No MoneyPrinterTurbo source files are copied into this repository. We import:
  `app.services.video_project` (scene renderer) and `app.services.voice` (`tts`, `_openai_compatible_tts`).
  Fonts are NOT taken from the `mpt` wheel any more: see "Bundled font" below.
- If any file is ever vendored/modified, put it under `vendor/moneyprinterturbo/`, keep its header, add
  "Adapted from MoneyPrinterTurbo (MIT, Copyright (c) 2024 Harry), commit <sha>, modified by Nova Digital",
  and list it here.

```
MIT License

Copyright (c) 2024 Harry

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bundled font

- `factory/assets/fonts/BeVietnamPro-Bold.ttf`: Be Vietnam Pro Bold, Copyright 2021 The Be Vietnam Pro Project
  Authors (https://github.com/bettergui/BeVietnamPro), licensed under the SIL Open Font License 1.1.
- Licence text: `factory/assets/fonts/OFL.txt` (copied verbatim from `ofl/bevietnampro/OFL.txt` in
  https://github.com/google/fonts). It must stay next to the font file.
- The TTF is the copy shipped in `mpt==1.3.8`; it is the only face the caption style uses (libass selects
  `Be Vietnam Pro`, weight 700). libass reads fonts only from this directory (`fontsdir` in `factory/render.py`).
- The directory lives inside the `factory` package, so the wheel build and an editable checkout both carry it.

## Assets we deliberately do NOT use

- `mpt` bundled music (`resource/songs`): upstream README says it comes from YouTube videos, copyright unclear.
- Proprietary fonts bundled in `mpt` (Microsoft YaHei, STHeiti, UTM Kabel KT). They are never copied here and the
  `mpt` fonts folder is not on libass's font path, so a caption can never pick them up. Captions use Be Vietnam Pro.

## Services called over HTTP (not distributed)

- VoiceStudio (AGPL-3.0): called via its local HTTP API only. Commercial use depends on the license of the
  specific TTS engine/voice you pick inside VoiceStudio.
- Microsoft Edge read-aloud (via `edge-tts` in mpt): no clear commercial license, dev/test only.
