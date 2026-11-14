from pydantic import BaseModel, Field, field_validator


class TTSSynthesizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("播报文本不能为空")
        return normalized


class TTSSynthesizeResponse(BaseModel):
    audio: str
    format: str
    sample_rate: int
