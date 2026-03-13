from pydantic import BaseModel


class FaceScanResponse(BaseModel):
    """
    Returned after a registered user scans their face.
    Embedding is saved to their profile.
    """

    message: str
    face_detected: bool


class AnonymousScanResponse(BaseModel):
    """
    Returned after an anonymous user scans their face.
    Embedding is never stored — results are held in Redis under the token.
    """

    scan_token: str  # short-lived Redis key to retrieve results
    expires_in_seconds: int
    match_count: int  # how many photos were matched


class FaceMatchResult(BaseModel):
    """A single photo match from a face scan."""

    photo_id: str
    match_score: float
