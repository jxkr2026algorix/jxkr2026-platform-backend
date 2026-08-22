"""ORM 모델. 이 백엔드가 소유하는 것은 '운영 상태'다.

공공데이터(관측·특보·대피소 원본)는 GB SafeData 가 소유하고 여기서는 저장하지 않는다.
여기 있는 것은 사람이 만든 결정과 그 이력이다 — 상황, 계획, 승인, 연락, 임무, 보고.
"""

from app.db.models.community import Community, Shelter
from app.db.models.contact import ContactAttempt
from app.db.models.incident import AuditEvent, Incident
from app.db.models.plan import EvacuationPlan, PlanItem
from app.db.models.prediction import PredictionRun
from app.db.models.push import PushSubscription
from app.db.models.task import FieldReport, FieldTask

__all__ = [
    "AuditEvent",
    "Community",
    "ContactAttempt",
    "EvacuationPlan",
    "FieldReport",
    "FieldTask",
    "Incident",
    "PlanItem",
    "PredictionRun",
    "PushSubscription",
    "Shelter",
]
