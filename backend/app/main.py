from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.app.intake import extract_brief
from backend.app.llm import LLMError
from backend.app.schemas import IntakeResult, RawBrief

app = FastAPI(title='DC Build Planner')


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'dc-build-planner'}


class IntakeRequest(BaseModel):
    """Free text pasted at intake, or the extracted text of uploaded documents."""

    text: str = Field(min_length=1)
    source_ref: str = 'raw_brief'
    attachments: list[str] = Field(default_factory=list)


@app.post('/intake', response_model=IntakeResult)
def intake(request: IntakeRequest) -> IntakeResult:
    """Read a raw brief into a structured Brief with a citation per field and questions.

    Returns 200 with questions[] populated when fields are missing — an incomplete brief is a
    normal outcome, not an error. The planner answers the questions and resubmits.
    """
    try:
        return extract_brief(
            RawBrief(
                text=request.text,
                source_ref=request.source_ref,
                attachments=request.attachments,
            )
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=f'Extraction provider failed: {exc}') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
