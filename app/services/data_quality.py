from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app.core.config import Settings
from app.domain.schemas import DataQualityCheck, DataQualityReport


class _DbtRunResult(BaseModel):
    unique_id: str
    status: str
    message: str | None


class _DbtRunResultsMetadata(BaseModel):
    generated_at: datetime


class _DbtRunResultsFile(BaseModel):
    metadata: _DbtRunResultsMetadata
    results: list[_DbtRunResult]


class DataQualityService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_latest_report(self) -> DataQualityReport:
        raw = Path(self._settings.dbt_run_results_path).read_text()
        parsed = _DbtRunResultsFile.model_validate_json(raw)

        checks = [
            DataQualityCheck(
                unique_id=result.unique_id,
                status=result.status,
                message=result.message,
            )
            for result in parsed.results
        ]

        return DataQualityReport(
            generated_at=parsed.metadata.generated_at,
            passed=all(check.status == "success" for check in checks),
            checks=checks,
        )
