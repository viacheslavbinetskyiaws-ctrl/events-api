"""Unit tests for AnalyticsService. Same idea as test_event_service.py —
mock_analytics_repository (see tests/conftest.py) stands in for
AnalyticsRepository, so only AnalyticsService's own logic is under test.

Your turn to write the test bodies.

test_get_daily_counts_delegates_to_repository:
  - Set `mock_analytics_repository.get_daily_counts.return_value` to some
    fixed list of DailyEventCount objects.
  - Call `await service.get_daily_counts()`.
  - Assert `mock_analytics_repository.get_daily_counts.assert_awaited_once()`.
  - Assert the result returned by the service is exactly what the
    repository returned — AnalyticsService.get_daily_counts is a pure
    passthrough (no business rules like EventService has), so this one
    test is really checking two things at once: that the call happens,
    and that nothing gets lost or mutated on the way back out.
"""

from datetime import date
from uuid import uuid4

from app.domain.schemas import DailyEventCount
from app.services.analytics import AnalyticsService

TENANT_ID = uuid4()


async def test_get_daily_counts_delegates_to_repository(mock_analytics_repository):
    service = AnalyticsService(mock_analytics_repository)

    expected = mock_analytics_repository.get_daily_counts.return_value = [
        DailyEventCount(
            tenant_id=TENANT_ID, event_type="signup", utc_date=date(2026, 11, 11), event_count=2
        ),
        DailyEventCount(
            tenant_id=TENANT_ID, event_type="logout", utc_date=date(2026, 11, 12), event_count=3
        ),
    ]

    result = await service.get_daily_counts(TENANT_ID)

    mock_analytics_repository.get_daily_counts.assert_awaited_once_with(TENANT_ID)

    assert result == expected
