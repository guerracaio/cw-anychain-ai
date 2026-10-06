from uuid import uuid4

from fastapi import APIRouter, Request, Response

from app.domain.analysis import AnalysisResponse, AnalyzeRequest
from app.services.http import request_id

router = APIRouter(prefix="/api")


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(body: AnalyzeRequest, request: Request, response: Response) -> AnalysisResponse:
    identifier = uuid4().hex
    token = request_id.set(identifier)
    request.state.request_id = identifier
    response.headers["X-Request-ID"] = identifier
    try:
        return await request.app.state.analysis.analyze(body.tx_hash, identifier, body.mode)
    finally:
        request_id.reset(token)
