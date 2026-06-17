from unittest.mock import patch

from app.config import Settings
from app.utils.report_storage import load_report_pdf, report_pdf_exists, save_report_pdf


def test_save_and_load_report_pdf(tmp_path):
    settings = Settings(report_exports_dir=str(tmp_path))
    with patch("app.utils.report_storage.get_settings", return_value=settings):
        save_report_pdf("agent-abc", b"%PDF-1.4")
        assert report_pdf_exists("agent-abc")
        assert load_report_pdf("agent-abc") == b"%PDF-1.4"
        assert load_report_pdf("missing") is None


def test_reports_dir_is_created_on_save(tmp_path):
    exports_dir = tmp_path / "nested" / "reports"
    settings = Settings(report_exports_dir=str(exports_dir))
    with patch("app.utils.report_storage.get_settings", return_value=settings):
        save_report_pdf("agent-abc", b"%PDF-1.4")
    assert exports_dir.is_dir()
    assert (exports_dir / "agent-abc.pdf").is_file()
