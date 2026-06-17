"""Persist generated rapport PDFs for preview and download."""

from pathlib import Path

from app.config import get_settings


def reports_dir() -> Path:
    path = Path(get_settings().report_exports_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_pdf_path(agent_id: str) -> Path:
    return reports_dir() / f"{agent_id.strip()}.pdf"


def save_report_pdf(agent_id: str, content: bytes) -> Path:
    path = report_pdf_path(agent_id)
    path.write_bytes(content)
    return path


def load_report_pdf(agent_id: str) -> bytes | None:
    path = report_pdf_path(agent_id)
    if path.is_file():
        return path.read_bytes()
    return None


def report_pdf_exists(agent_id: str) -> bool:
    return report_pdf_path(agent_id).is_file()
