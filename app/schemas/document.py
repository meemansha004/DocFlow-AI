import uuid

from pydantic import BaseModel

from app.models.document import SensitivityLevel, DocumentStatus


class UploadResponse(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    original_filename: str
    status: DocumentStatus
    sensitivity_level: SensitivityLevel
    uploaded_as_team_id: uuid.UUID

    model_config = {"from_attributes": True}
