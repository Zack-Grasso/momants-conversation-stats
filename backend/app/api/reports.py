import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.report import ReportContextResponse
from app.services.report_service import ReportService
from app.utils.report_storage import load_report_pdf, save_report_pdf

router = APIRouter()


def get_service(db: Session = Depends(get_db)) -> ReportService:
    return ReportService(db)


@router.get("/context", response_model=ReportContextResponse)
def get_report_context(
    agent_id: str = Query(..., min_length=1, max_length=36),
    event_name: str | None = Query(default=None, max_length=255),
    service: ReportService = Depends(get_service),
) -> ReportContextResponse:
    context = service.build_context(agent_id, event_name)
    return ReportContextResponse(**context)


@router.get("/preview", response_class=HTMLResponse)
def preview_report(
    agent_id: str = Query(..., min_length=1, max_length=36),
    event_name: str | None = Query(default=None, max_length=255),
    service: ReportService = Depends(get_service),
) -> HTMLResponse:
    try:
        html = service.render_html(agent_id, event_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return HTMLResponse(content=html)


@router.get("/pdf")
def export_report_pdf(
    agent_id: str = Query(..., min_length=1, max_length=36),
    event_name: str | None = Query(default=None, max_length=255),
    inline: bool = Query(default=False),
    service: ReportService = Depends(get_service),
) -> Response:
    pdf = load_report_pdf(agent_id)
    if pdf is None:
        try:
            pdf = service.render_pdf(agent_id, event_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"PDF conversion service failed: {exc}",
            ) from exc
        save_report_pdf(agent_id, pdf)

    filename = f"momants-report-{agent_id[:8]}.pdf"
    disposition = "inline" if inline else "attachment"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )
