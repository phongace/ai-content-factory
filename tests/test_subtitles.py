"""TC-07 + P0-09."""

from __future__ import annotations

from factory.subtitles import build_cues, scene_cues, split_caption_text, write_ass, write_srt


def test_tc07_no_overlap_and_scene_boundaries():
    scenes = [
        ("Bạn đang làm sai điều này mỗi ngày mà không hề biết, và nó lấy đi kết quả của bạn.", 6.4),
        ("Phần lớn mọi người chỉ bắt đầu khi có cảm hứng.", 4.1),
        ("Người thành công thì khác.", 2.0),
        ("Chỉ mười phút mỗi sáng, sau một tháng bạn sẽ thấy khác biệt rõ ràng trong công việc.", 7.7),
        ("Theo dõi kênh.", 1.2),
    ]
    cues = build_cues(scenes, "vi")
    assert cues[0].start == 0
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.end <= b.start + 1e-9, "captions overlap"
        assert a.start < a.end
    assert abs(cues[-1].end - sum(d for _, d in scenes)) < 1e-6
    boundaries, t = [], 0.0
    for _, d in scenes:
        t += d
        boundaries.append(round(t, 3))
    for c in cues:  # no caption crosses a scene boundary
        assert not any(c.start < b < c.end - 1e-6 for b in boundaries)


def test_captions_are_short_enough():
    chunks = split_caption_text("one two three four five six seven eight nine ten eleven twelve thirteen", "en")
    assert all(len(c.split()) <= 6 for c in chunks)


def test_tiny_duration_merges_cues():
    cues = scene_cues("a, b, c, d, e, f", 0, 1.0, "en")
    assert all(c.end - c.start >= 0.5 - 1e-9 for c in cues)
    assert cues[-1].end == 1.0


def test_writers(tmp_path):
    cues = build_cues([("Xin chào {thế giới}.", 2.0)], "vi")
    write_ass(cues, tmp_path / "a.ass")
    write_srt(cues, tmp_path / "a.srt")
    ass = (tmp_path / "a.ass").read_text(encoding="utf-8")
    assert "PlayResY: 1920" in ass and "Be Vietnam Pro" in ass and "{" not in ass.split("[Events]")[1]
    assert "00:00:02,000" in (tmp_path / "a.srt").read_text(encoding="utf-8")
