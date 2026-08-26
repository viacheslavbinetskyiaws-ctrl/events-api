from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas import DataQualityCheck, DataQualityReport
from app.repositories.models import DataQualityRunORM


class DataQualityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_latest_report(self) -> DataQualityReport | None:
        stmt = select(DataQualityRunORM).where(DataQualityRunORM.id == 1)
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            return None

        return DataQualityReport(
            generated_at=row.generated_at,
            passed=row.passed,
            checks=[DataQualityCheck.model_validate(check) for check in row.checks],
        )
