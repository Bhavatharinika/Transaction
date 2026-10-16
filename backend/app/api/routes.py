"""HTTP routes. Thin: validation + delegation."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from app.core.metrics import render_metrics
from app.schemas.transaction import TransactionAccepted, TransactionCreate, TransactionDetail, TransactionPage, TransactionStatus
from app.services.transaction_service import TransactionService

router = APIRouter()


def _svc(request: Request) -> TransactionService:
    return request.app.state.transactions


@router.post("/api/transactions", response_model=TransactionAccepted, status_code=202, tags=["transactions"],
             responses={200: {"description": "Idempotent replay of an already-accepted transaction"}, 409: {"description": "Same id, different payload"},
                        503: {"description": "Overloaded; see Retry-After"}})
async def submit_transaction(body: TransactionCreate, request: Request, response: Response) -> TransactionAccepted:
    result = await _svc(request).submit(body)
    if not result.created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return TransactionAccepted(transaction_id=result.transaction_id, status=result.status, submitted_at=result.submitted_at)


@router.get("/api/transactions", response_model=TransactionPage, tags=["transactions"])
async def list_transactions(
    request: Request,
    status: Literal["pending", "processing", "completed", "failed"] | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort: Literal["submittedAt", "completedAt", "amount", "retryCount", "status"] = "submittedAt",
    order: Literal["asc", "desc"] = "desc",
) -> TransactionPage:
    return await _svc(request).list(status=status, q=q, limit=limit, offset=offset, sort=sort, descending=order == "desc")


@router.get("/api/transactions/{transaction_id}", response_model=TransactionDetail, tags=["transactions"])
async def get_transaction(transaction_id: UUID, request: Request) -> TransactionDetail:
    return await _svc(request).get(transaction_id)


@router.get("/api/health", tags=["operations"])
async def health(request: Request) -> JSONResponse:
    body, ok = await request.app.state.observability.health()
    return JSONResponse(body, status_code=200 if ok else 503)


@router.get("/api/stats", tags=["operations"])
async def stats(request: Request) -> dict:
    return await request.app.state.observability.stats()


@router.get("/api/stats/timeseries", tags=["operations"])
async def stats_timeseries(request: Request, minutes: int = Query(default=30, ge=5, le=240)) -> dict:
    return {"minutes": minutes, "points": await request.app.state.observability.timeseries(minutes)}


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    body, content_type = render_metrics()
    return Response(body, media_type=content_type)
