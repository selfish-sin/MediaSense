"""
format_converter 格式转换器测试
"""
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from pathlib import Path

from services.format_converter import (
    VIDEO_FORMATS, AUDIO_FORMATS, IMAGE_FORMATS,
    VIDEO_PRESETS, IMAGE_QUALITY, AUDIO_BITRATE, OUTPUT_TYPE,
    check_engines, classify_file, available_outputs, convert,
    _find_exe, _ffmpeg_convert, _pillow_convert,
)


class TestFormatConstants:
    def test_video_formats(self):
        assert ".mp4" in VIDEO_FORMATS
        assert ".mkv" in VIDEO_FORMATS

    def test_audio_formats(self):
        assert ".mp3" in AUDIO_FORMATS
        assert ".wav" in AUDIO_FORMATS

    def test_image_formats(self):
        assert ".png" in IMAGE_FORMATS
        assert ".jpg" in IMAGE_FORMATS

    def test_video_presets(self):
        assert "low" in VIDEO_PRESETS
        assert "medium" in VIDEO_PRESETS
        assert "high" in VIDEO_PRESETS
        assert "crf" in VIDEO_PRESETS["low"]

    def test_image_quality(self):
        assert IMAGE_QUALITY["low"] == 60
        assert IMAGE_QUALITY["medium"] == 85
        assert IMAGE_QUALITY["high"] == 95

    def test_audio_bitrate(self):
        assert AUDIO_BITRATE["low"] == "128k"
        assert AUDIO_BITRATE["high"] == "lossless"

    def test_output_type(self):
        assert OUTPUT_TYPE["mp4"] == "video"
        assert OUTPUT_TYPE["mp3"] == "audio"
        assert OUTPUT_TYPE["png"] == "image"


class TestClassifyFile:
    def test_video(self):
        assert classify_file("test.mp4") == "video"
        assert classify_file("test.mkv") == "video"
        assert classify_file("test.MOV") == "video"

    def test_audio(self):
        assert classify_file("test.mp3") == "audio"
        assert classify_file("test.WAV") == "audio"

    def test_image(self):
        assert classify_file("test.png") == "image"
        assert classify_file("test.jpg") == "image"

    def test_unknown(self):
        assert classify_file("test.txt") == "unknown"
        assert classify_file("test.docx") == "unknown"
        assert classify_file("test") == "unknown"


class TestCheckEngines:
    def test_returns_dict(self):
        result = check_engines()
        assert "ffmpeg" in result
        assert "pillow" in result
        assert isinstance(result["ffmpeg"], bool)
        assert isinstance(result["pillow"], bool)


class TestAvailableOutputs:
    def test_video_file(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            result = available_outputs("test.mp4")
            assert len(result["video"]) > 0
            assert len(result["audio"]) > 0
            assert result["image"] == []

    def test_audio_file(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            result = available_outputs("test.mp3")
            assert result["video"] == []
            assert result["image"] == []
            assert len(result["audio"]) > 0

    def test_image_file(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            result = available_outputs("test.png")
            assert result["video"] == []
            assert result["audio"] == []
            assert len(result["image"]) > 0

    def test_no_ffmpeg(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": False, "pillow": True}):
            result = available_outputs("test.mp4")
            assert result["video"] == []
            assert result["audio"] == []

    def test_no_pillow(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": False}):
            result = available_outputs("test.png")
            assert result["image"] == []

    def test_unknown_file(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            result = available_outputs("test.txt")
            assert len(result["video"]) > 0
            assert len(result["audio"]) > 0

    def test_engines_param_passed(self):
        result = available_outputs("test.mp4", engines={"ffmpeg": False, "pillow": False})
        assert result["video"] == []
        assert result["audio"] == []
        assert result["image"] == []


class TestConvert:
    @pytest.mark.asyncio
    async def test_unknown_format_raises(self):
        with pytest.raises(ValueError, match="不支持"):
            await convert("test.txt", "mp3")

    @pytest.mark.asyncio
    async def test_video_no_ffmpeg_raises(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": False, "pillow": True}):
            with pytest.raises(RuntimeError, match="FFmpeg"):
                await convert("test.mp4", "mp3")

    @pytest.mark.asyncio
    async def test_image_no_pillow_raises(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": False}):
            with pytest.raises(RuntimeError, match="Pillow"):
                await convert("test.png", "jpg")

    @pytest.mark.asyncio
    async def test_video_convert_calls_ffmpeg(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            with patch("services.format_converter._ffmpeg_convert", return_value=("output.mp3", "ffmpeg")) as mock_ff:
                result = await convert("test.mp4", "mp3", "medium")
                assert result["output_path"] == "output.mp3"
                assert result["engine"] == "ffmpeg"
                assert "elapsed" in result
                mock_ff.assert_called_once()

    @pytest.mark.asyncio
    async def test_image_convert_calls_pillow(self):
        with patch("services.format_converter.check_engines", return_value={"ffmpeg": True, "pillow": True}):
            with patch("services.format_converter._pillow_convert", return_value=("output.jpg", "pillow")) as mock_pil:
                result = await convert("test.png", "jpg", "high")
                assert result["output_path"] == "output.jpg"
                assert result["engine"] == "pillow"
                assert "elapsed" in result
                mock_pil.assert_called_once()


class TestFfmpegConvert:
    def test_no_ffmpeg_raises(self):
        with patch("services.format_converter._find_exe", return_value=None):
            with pytest.raises(RuntimeError, match="FFmpeg 未安装"):
                _ffmpeg_convert("test.mp4", "mp3", "medium", "video")

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_video_to_gif(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.gif")):
            result = _ffmpeg_convert("test.mp4", "gif", "low", "video")
            assert result[1] == "ffmpeg"
            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            assert "fps=10" in cmd_str
            assert "-vf" in cmd

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_video_to_mp4(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.mp4")):
            result = _ffmpeg_convert("test.mp4", "mp4", "medium", "video")
            assert result[1] == "ffmpeg"
            cmd = mock_run.call_args[0][0]
            assert "libx264" in cmd

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_audio_to_mp3(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.mp3")):
            result = _ffmpeg_convert("test.wav", "mp3", "medium", "audio")
            assert result[1] == "ffmpeg"
            cmd = mock_run.call_args[0][0]
            assert "libmp3lame" in cmd

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_audio_to_flac_lossless(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.flac")):
            result = _ffmpeg_convert("test.wav", "flac", "high", "audio")
            assert result[1] == "ffmpeg"
            cmd = mock_run.call_args[0][0]
            assert "flac" in cmd

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_audio_to_ogg(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.ogg")):
            result = _ffmpeg_convert("test.wav", "ogg", "medium", "audio")
            assert result[1] == "ffmpeg"
            cmd = mock_run.call_args[0][0]
            assert "libvorbis" in cmd

    @patch("services.format_converter._find_exe", return_value="/usr/bin/ffmpeg")
    @patch("services.format_converter.subprocess.run")
    def test_ffmpeg_failure_raises(self, mock_run, mock_exe):
        mock_run.return_value = MagicMock(returncode=1, stderr="error here", stdout="")
        with patch("services.format_converter.Path.with_suffix", return_value=Path("output.mp3")):
            with pytest.raises(RuntimeError, match="FFmpeg 转换失败"):
                _ffmpeg_convert("test.wav", "mp3", "medium", "audio")


class TestPillowConvert:
    def test_jpg_convert(self):
        mock_img = MagicMock()
        with patch("PIL.Image.open", return_value=mock_img):
            with patch("services.format_converter.Path.with_suffix", return_value=Path("output.jpg")):
                result = _pillow_convert("test.png", "jpg", "high")
                assert result[1] == "pillow"
                mock_img.save.assert_called_once()

    def test_jpg_rgba_convert(self):
        mock_img = MagicMock()
        mock_img.mode = "RGBA"
        with patch("PIL.Image.open", return_value=mock_img):
            with patch("services.format_converter.Path.with_suffix", return_value=Path("output.jpg")):
                _pillow_convert("test.png", "jpg", "medium")
                mock_img.convert.assert_called_once_with("RGB")

    def test_png_convert(self):
        mock_img = MagicMock()
        with patch("PIL.Image.open", return_value=mock_img):
            with patch("services.format_converter.Path.with_suffix", return_value=Path("output.png")):
                result = _pillow_convert("test.jpg", "png", "medium")
                assert result[1] == "pillow"
                mock_img.save.assert_called_once()
