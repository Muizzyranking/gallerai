from pydantic import BaseModel


class FaceScanResponse(BaseModel):
    """
    Returned after a registered user scans their face.
    Embedding is saved to their profile and gallery is built immediately.
    """

    face_detected: bool
    match_count: int


class AnonymousScanResponse(BaseModel):
    """
    Returned after an anonymous user scans their face.
    Embedding is never stored — results are held in Redis under the token.
    """

    scan_token: str
    expires_in_seconds: int
    match_count: int


class FaceMatchResult(BaseModel):
    """A single photo match from a face scan."""

    photo_id: str
    match_score: float


class ClaimGalleryRequest(BaseModel):
    """Request body to claim an anonymous gallery into a registered account."""

    scan_token: str
