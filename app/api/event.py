from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import (
    DB,
    CurrentUser,
    EventOr404,
    OrganizerEvent,
)
from app.core.schemas import ApiResponse
from app.schemas.event import (
    EventAccessVerify,
    EventCreate,
    EventResponse,
    EventUpdate,
    InviteCreate,
    MemberAdd,
)
from app.services import event_service

router = APIRouter()


@router.post(
    "",
    response_model=ApiResponse[EventResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_event(payload: EventCreate, current_user: CurrentUser, db: DB):
    event = await event_service.create_event(payload, current_user, db)
    return ApiResponse[EventResponse](
        message="Event created successfully",
        data=EventResponse.model_validate(event),
    )


@router.get(
    "",
    response_model=ApiResponse[list[EventResponse]],
    summary="List events I manage",
)
async def list_managed_events(
    current_user: CurrentUser,
    db: DB,
) -> ApiResponse[list[EventResponse]]:
    """
    Return all events the current user owns or co-organizes.
    Used for the organizer dashboard.
    """
    events = await event_service.get_managed_events(current_user, db)
    return ApiResponse(
        message=f"{len(events)} event(s) found",
        data=events,
    )


@router.get(
    "/attending",
    response_model=ApiResponse[list[EventResponse]],
    summary="List events I am attending",
)
async def list_attended_events(
    current_user: CurrentUser,
    db: DB,
) -> ApiResponse[list[EventResponse]]:
    """
    Return all events the current user is an attendee of.
    Does not include events the user organizes.
    """
    events = await event_service.get_attended_events(current_user, db)
    return ApiResponse(
        message=f"{len(events)} event(s) found",
        data=events,
    )


@router.get("/{event_id}", response_model=ApiResponse[EventResponse])
async def get_event(event: EventOr404):
    return ApiResponse[EventResponse](
        message="Event retrieved successfully",
        data=EventResponse.model_validate(event),
    )


@router.patch("/{event_id}", response_model=ApiResponse[EventResponse])
async def update_event(payload: EventUpdate, event: EventOr404, db: DB):
    event = await event_service.update_event(event, payload, db)
    return ApiResponse[EventResponse](
        message="Event updated successfully",
        data=EventResponse.model_validate(event),
    )


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event: OrganizerEvent, db: DB):
    await event_service.delete_event(event, db)


@router.post("/{event_id}/access/verify", status_code=status.HTTP_200_OK)
async def verify_access(payload: EventAccessVerify, event: EventOr404):
    """Verify an access code. Returns 200 if valid, 403 if not."""
    event_service.verify_event_access_code(event, payload.access_code)
    return {"message": "Access granted"}


@router.post("/{event_id}/members", status_code=status.HTTP_201_CREATED)
async def add_co_organizer(
    payload: MemberAdd,
    event: OrganizerEvent,
    current_user: CurrentUser,
    db: DB,
):
    member = await event_service.add_co_organizer(event, payload, current_user, db)
    return ApiResponse(
        message="Co-organizer added successfully",
        data={"member_id": member.id},
    )


@router.delete("/{event_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    user_id: str,
    event: OrganizerEvent,
    db: DB,
):
    await event_service.remove_member(event, user_id, db)


@router.post("/{event_id}/invites", status_code=status.HTTP_201_CREATED)
def add_invites(payload: InviteCreate, event: OrganizerEvent, db: DB):
    invites = event_service.add_invites(event, payload, db)
    return {"message": f"{len(invites)} invite(s) created", "count": len(invites)}


@router.delete("/{event_id}/invites/{email}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_invite(email: str, event: OrganizerEvent, db: DB):
    event_service.revoke_invite(event, email, db)


@router.delete(
    "/{event_id}/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Leave an event",
)
async def leave_event(
    event: EventOr404,
    current_user: CurrentUser,
    db: DB,
) -> None:
    """
    Leave an event as an attendee.
    Owners cannot leave their own event — they must delete it or transfer ownership.
    """
    if event.owner_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event owners cannot leave their own event. Delete the event instead.",
        )
    await event_service.remove_member(event, current_user.id, db)
