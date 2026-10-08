"""Request and response shapes for Astro Rooms.

The rule running through all of it: nothing here carries a birth date, a
birth time, coordinates, an email or a user id. A room is a shared surface,
so every field on it is visible to the other participant, and the only safe
way to keep the other person's details private is for them never to be in
the payload at all. The pair revision is a hash precisely so it can travel
in responses and logs without carrying what it was computed from.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.chart_schemas import Aspect, BirthData

ROOM_TOPICS = ("romance", "friendship", "understanding")

#: Version of the consent wording the member agreed to. Stored per member so
#: a later change to the text does not retroactively claim they agreed to it.
CONSENT_VERSION = "room-consent-v1"


class CreateRoomRequest(BaseModel):
    topic: str = Field(pattern="^(romance|friendship|understanding)$")
    #: Makes create idempotent. A double tap on a slow connection must not
    #: leave two rooms and two links; without one the caller gets no
    #: protection, which is why the field is optional rather than defaulted.
    client_request_id: UUID | None = None


class RoomSummary(BaseModel):
    id: UUID
    topic: str
    status: str
    #: The caller's own role. There is no list of participants: a room has at
    #: most one other person and naming them is not this endpoint's job.
    role: str
    member_count: int
    #: Whether the shared reading is open. False covers "not joined yet" and
    #: "someone withdrew" alike — the difference is in `status`, and the
    #: distinction the UI needs is "can I read it".
    both_consented: bool
    created_at: str


class CreateInvitationRequest(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationResponse(BaseModel):
    #: Returned ONCE, at creation. Only a keyed hash is stored, so this
    #: cannot be shown again — reissuing revokes the previous link.
    invite_url: str
    expires_at: str


class InvitationPreview(BaseModel):
    """What someone holding a link may see before deciding anything.

    No host name, no alias, no birth data, no room id — the topic and
    whether the invitation is still open, which is everything needed to
    decide whether to go on.
    """

    topic: str
    status: str
    expires_at: str


class AcceptInvitationRequest(BaseModel):
    #: Must be explicitly true. An unchecked box is a refusal, and a default
    #: of true would make the whole consent step decorative.
    consent: bool
    consent_version: str = CONSENT_VERSION
    #: Omitted when the invitee already has a birth profile. When present it
    #: is written to THEIR OWN profile row — the room stores only a revision
    #: number, never the data.
    birth_data: BirthData | None = None


class MemberReliability(BaseModel):
    person_a_birth_time_known: bool
    person_b_birth_time_known: bool
    #: Points dropped from the calculation because they rest on a birth time
    #: nobody supplied.
    excluded_points: list[str]
    #: Kept, but approximate — the Moon moves far enough in a day to matter.
    uncertain_points: list[str]


class FreeInsight(BaseModel):
    key: str
    title: str
    summary: str
    #: The aspects the summary rests on. Empty is a valid, honest answer.
    evidence: list[Aspect]
    #: Which rule set produced this, so a stored reading can be told apart
    #: from one the current rules would produce.
    interpretation_source: str


class PremiumReportState(BaseModel):
    status: str
    locked_section_titles: list[str] | None = None


class SharePolicy(BaseModel):
    can_export_sanitized_card: bool
    #: Always false. Stated rather than implied, because a share card is the
    #: easiest place for birth data to escape.
    include_birth_data: bool = False


class RoomResult(BaseModel):
    room_id: UUID
    status: str
    #: Opaque hash of both revisions plus the algorithm and template
    #: versions. Safe to log; carries nothing it was computed from.
    pair_revision: str
    locale: str
    reliability: MemberReliability
    free_insights: list[FreeInsight]
    premium_report: PremiumReportState
    share_policy: SharePolicy
