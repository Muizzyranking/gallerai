# Galleria

> Event photo sharing powered by face recognition. Find yourself in every moment.

Galleria is a full-stack event photo platform that eliminates the tedious process of scrolling through hundreds of event photos to find yourself. Organizers upload photos, Galleria processes them asynchronously using deep learning face recognition, and attendees scan their face once to instantly retrieve every photo they appear in.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Database Design](#database-design)
- [Face Recognition Pipeline](#face-recognition-pipeline)
- [Access Control System](#access-control-system)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [Design Decisions](#design-decisions)

---

## Overview

Galleria solves a universal problem at events — weddings, conferences, graduations, sports days, parties. The photographer takes hundreds of photos. They get uploaded somewhere. Attendees spend an hour scrolling through all of them trying to find the ten that include them.

With Galleria, that process takes ten seconds. The attendee opens the event link, taps "Find My Photos", holds their phone up to their face, and gets back a filtered gallery containing only the photos they appear in. They can download them all with one tap.

For organizers, Galleria provides a clean dashboard to create events, upload photos in bulk, control who has access, manage co-organizers, and optionally allow attendees to contribute their own photos.

---

## How It Works

### Photo Processing (Organizer Side)

```
Organizer uploads photos
        ↓
FastAPI receives files → validates type and size → saves to storage
        ↓
Creates Photo records in PostgreSQL (status: pending)
        ↓
Dispatches one Celery task per photo to Redis queue
        ↓
Celery worker picks up task:
    → Loads image from storage
    → Runs RetinaFace to detect all faces in the image
    → Filters out low-confidence detections (< 0.9) and tiny faces (< 80px)
    → For each valid face: runs ArcFace to extract 512-dimensional embedding
    → Stores each embedding as a document in MongoDB
    → Updates Photo status to "processed" with face count
        ↓
Organizer polls GET /events/{id}/photos/status to track progress
```

### Face Search (Attendee Side)

```
Attendee uploads or webcam-captures a clear photo of their face
        ↓
FastAPI extracts a single 512-dim ArcFace embedding from the image
        ↓
Fetches all face embedding documents for this event from MongoDB
        ↓
Runs batched cosine similarity:
    user_embedding (1 × 512) · event_embeddings (N × 512) = scores (N,)
        ↓
Deduplicates by photo — keeps highest score per photo
Filters results above similarity threshold (default: 0.6)
        ↓
Returns matched photo IDs sorted by confidence score

If registered user:
    → Saves embedding to user profile
    → Upserts matched photos into user_event_galleries table
    → User can revisit gallery without rescanning

If anonymous user:
    → Matched photo IDs stored in Redis with 2-hour TTL
    → Returns scan_token for gallery retrieval
    → Embedding is never persisted
```

---

## Features

### For Organizers
- Create events with configurable access control (link, code, approved list, or combined)
- Bulk photo upload with async face processing pipeline
- Real-time processing status tracking
- Co-organizer management
- Photo visibility controls (public/private per photo)
- Attendee upload support with optional approval workflow
- Event settings (downloads, gallery visibility, attendee uploads)
- Admin panel for platform management

### For Attendees
- Face scan via webcam capture or photo upload
- Instant gallery of matched photos
- Download individual photos or entire gallery as zip
- False match flagging with reason (not me / dislike / remove)
- Anonymous access — no account required
- Gallery claiming — convert anonymous results into a saved account gallery
- Attended events list for revisiting galleries

### Platform
- Admin dashboard with user and event management
- Platform-wide settings (pricing enforcement, maintenance mode)
- Audit trail for admin actions
- Structured JSON logging with rotating file handler in production

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│   Organizer Dashboard    Event Gallery    Face Scan UI           │
│        (Next.js)            (Next.js)       (Next.js)           │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP/REST
┌──────────────────────────────▼──────────────────────────────────┐
│                      FASTAPI APPLICATION                          │
│                                                                   │
│  /auth   /events   /photos   /faces   /gallery   /admin          │
│                                                                   │
│  Middleware: CORS, RequestID                                      │
│  Global exception handlers → consistent ApiResponse shape         │
└──────┬───────────┬──────────────┬──────────────┬────────────────┘
       │           │              │              │
┌──────▼──┐  ┌────▼─────┐  ┌────▼─────┐  ┌────▼─────────────────┐
│  Auth   │  │  Event   │  │  Photo   │  │    Face Service       │
│ Service │  │ Service  │  │ Service  │  │  (DeepFace/ArcFace)   │
└──────┬──┘  └────┬─────┘  └────┬─────┘  └────┬─────────────────┘
       │          │              │              │
┌──────▼──────────▼──────────────▼──────────────▼─────────────────┐
│                        DATA LAYER                                 │
├─────────────────┬──────────────────┬────────────────────────────┤
│   PostgreSQL    │     MongoDB       │           Redis            │
│                 │                  │                            │
│  users          │  face_embeddings  │  Celery broker (db 0)     │
│  events         │  (512-dim float   │  Celery results (db 1)    │
│  event_members  │   arrays per      │  Anonymous scan tokens    │
│  event_invites  │   detected face)  │  Application cache        │
│  photos         │                  │                            │
│  user_event_    │                  │                            │
│  galleries      │                  │                            │
│  platform_      │                  │                            │
│  settings       │                  │                            │
└─────────────────┴──────────────────┴────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                    CELERY WORKER POOL                             │
│                                                                   │
│   process_photo_task    warmup_models_task                        │
│                                                                   │
│   concurrency=2 (CPU-bound face detection)                        │
│   prefetch_multiplier=1 (one task at a time per worker)           │
│   acks_late=True (only ack after completion)                      │
└──────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                      FILE STORAGE                                 │
│                                                                   │
│   Local (dev) → abstracted BaseStorage → S3 (prod)               │
│                                                                   │
│   storage/                                                        │
│   └── events/                                                     │
│       └── {event_id}/                                             │
│           ├── photos/     ← organizer + approved attendee photos  │
│           └── faces/      ← temporary scan uploads (deleted       │
│                              after embedding extraction)          │
└──────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| API Framework | FastAPI | Async support, automatic OpenAPI docs, Pydantic v2 |
| Language | Python 3.12 | Type aliases, improved generics, performance |
| Package Manager | uv | Fast, modern Python package management |
| Relational DB | PostgreSQL 16 | Users, events, photos, galleries — structured relational data |
| Document DB | MongoDB 7 | Face embeddings — variable-length float arrays, event-scoped queries |
| Cache / Broker | Redis 7 | Celery message broker, result backend, anonymous tokens, app cache |
| Task Queue | Celery 5 | Async photo processing, retries, worker isolation |
| Face Detection | DeepFace + RetinaFace | Best accuracy for group photos with varied angles/lighting |
| Face Recognition | ArcFace | State-of-the-art accuracy, 512-dim embeddings |
| Similarity Search | NumPy (cosine) | Batch matrix operations — handles 8,000 embeddings in <100ms |
| ORM | SQLAlchemy 2 | Mapped columns, type-safe queries |
| Migrations | Alembic | Schema versioning |
| Async Mongo | Motor | Non-blocking MongoDB queries in FastAPI |
| File Storage | Local → S3 | Abstracted behind BaseStorage — swappable |
| Containerization | Docker + Compose | Reproducible dev and production environments |

---

## Project Structure

```
galleria/
├── app/
│   ├── api/                        # Route handlers
│   │   ├── auth.py                 # Registration, login, /me
│   │   ├── events.py               # Event CRUD, access, members, invites
│   │   ├── photos.py               # Upload, serve, approve, reject
│   │   ├── faces.py                # Face scan, anonymous scan, gallery claim
│   │   ├── gallery.py              # Gallery retrieval, flag management
│   │   ├── downloads.py            # Single photo and zip downloads
│   │   ├── admin.py                # Admin user/event/settings management
│   │   └── dependencies.py         # FastAPI dependency injection
│   │
│   ├── core/                       # Domain-agnostic utilities
│   │   ├── config.py               # Pydantic Settings — .env driven
│   │   ├── enums.py                # All domain enums (EventStatus, PhotoStatus, etc.)
│   │   ├── schemas.py              # ApiResponse[T] wrapper, ApiErrorResponse
│   │   ├── security.py             # JWT, password hashing, access code hashing
│   │   ├── logging.py              # Colored dev logging, JSON prod logging
│   │   ├── cache.py                # Namespaced Redis cache (event_cache, gallery_cache)
│   │   ├── middleware.py           # RequestID middleware
│   │   ├── exceptions.py           # Global exception handlers
│   │   └── pagination.py           # PaginationParams dependency
│   │
│   ├── db/                         # Database clients
│   │   ├── postgres.py             # SQLAlchemy engine, session, Base, BaseModel, TimestampMixin
│   │   ├── mongo.py                # Motor async client singleton
│   │   └── redis.py                # Async Redis client singleton
│   │
│   ├── models/                     # SQLAlchemy ORM models
│   │   ├── user.py                 # User (organizer + attendee, is_admin flag)
│   │   ├── event.py                # Event, EventMember, EventInvite
│   │   ├── photo.py                # Photo (storage_key, mime_type, face_count)
│   │   ├── gallery.py              # UserEventGallery (match_score, flag_reason)
│   │   └── platform.py             # PlatformSettings (key/value admin config)
│   │
│   ├── schemas/                    # Pydantic v2 request/response schemas
│   │   ├── user.py
│   │   ├── event.py                # Includes EventSettings schema
│   │   ├── photo.py                # PhotoSchema with computed url field
│   │   ├── face.py
│   │   ├── gallery.py
│   │   └── admin.py
│   │
│   ├── services/                   # Business logic layer
│   │   ├── auth_service.py         # Registration, login
│   │   ├── event_service.py        # Event lifecycle, membership, invites
│   │   ├── photo_service.py        # Upload, approval flow, status tracking
│   │   ├── face_service.py         # DeepFace wrapper, cosine similarity
│   │   ├── search_service.py       # MongoDB embedding search, deduplication
│   │   ├── gallery_service.py      # Gallery upsert, claim, flag management
│   │   ├── storage_service.py      # BaseStorage ABC, LocalStorage implementation
│   │   ├── download_service.py     # Single photo and streaming zip downloads
│   │   └── platform_service.py     # Platform settings CRUD
│   │
│   ├── workers/                    # Celery tasks
│   │   ├── celery_app.py           # Celery configuration, warmup signal
│   │   └── photo_tasks.py          # process_photo_task, warmup_models_task
│   │
│   └── main.py                     # FastAPI app, middleware, routers, lifespan
│
├── alembic/                        # Database migrations
│   ├── versions/                   # Migration files
│   └── env.py                      # Alembic environment (reads from settings)
│
├── tests/                          # Test suite
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_events.py
│   ├── test_photos.py
│   ├── test_face_search.py
│   └── test_gallery.py
│
├── docker-compose.yml              # All services
├── docker-compose.dev.yml          # Infrastructure only (dev)
├── Dockerfile
├── pyproject.toml                  # uv dependencies
├── alembic.ini
└── .env.example
```

---

## Database Design

### PostgreSQL Schema

**`users`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| email | VARCHAR(255) | Unique, indexed |
| password_hash | VARCHAR(255) | bcrypt via pwdlib |
| display_name | VARCHAR(255) | Optional |
| face_embedding | FLOAT8[] | 512-dim ArcFace embedding, nullable |
| face_updated_at | TIMESTAMPTZ | When embedding was last updated |
| is_admin | BOOLEAN | Admin panel access |
| is_active | BOOLEAN | Account active state |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**`events`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| owner_id | UUID FK | References users |
| title | VARCHAR(255) | |
| description | TEXT | Optional |
| event_date | TIMESTAMPTZ | Optional |
| status | ENUM | active, archived, deleted |
| is_private | BOOLEAN | |
| access_mode | ENUM | link, code, approved_list, combined |
| access_code_hash | VARCHAR(255) | bcrypt hashed, nullable |
| settings | JSONB | allow_attendee_uploads, require_upload_approval, downloads_enabled, gallery_visible |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**`event_members`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| event_id | UUID FK | References events, CASCADE |
| user_id | UUID FK | References users, CASCADE |
| role | ENUM | organizer, attendee |
| status | ENUM | active, removed |
| added_by | UUID FK | References users, nullable |
| created_at | TIMESTAMPTZ | |

Unique constraint on (event_id, user_id).

**`event_invites`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| event_id | UUID FK | References events, CASCADE |
| email | VARCHAR(255) | Indexed |
| invite_token | VARCHAR(255) | Unique, for email links |
| status | ENUM | pending, accepted, revoked |
| created_at | TIMESTAMPTZ | |
| accepted_at | TIMESTAMPTZ | Nullable |

**`photos`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| event_id | UUID FK | References events, CASCADE |
| uploaded_by | UUID FK | References users, nullable |
| storage_key | VARCHAR(255) | Opaque key — storage service resolves to file path |
| filename | VARCHAR(255) | Original filename |
| file_size | INTEGER | Bytes |
| mime_type | VARCHAR(50) | image/jpeg, image/png, image/webp |
| width | INTEGER | Pixels |
| height | INTEGER | Pixels |
| face_count | INTEGER | Valid faces detected |
| status | ENUM | pending_approval, rejected, pending, processing, processed, failed |
| is_private | BOOLEAN | |
| error_message | TEXT | Populated on failure |
| processed_at | TIMESTAMPTZ | Nullable |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

**`user_event_galleries`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| user_id | UUID FK | References users, CASCADE |
| event_id | UUID FK | References events, CASCADE |
| photo_id | UUID FK | References photos, CASCADE |
| match_score | FLOAT | Cosine similarity score (0-1) |
| is_flagged | BOOLEAN | Hidden from normal gallery view |
| flag_reason | ENUM | not_me, dislike, removed — nullable |
| flagged_at | TIMESTAMPTZ | Nullable |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

Unique constraint on (user_id, event_id, photo_id).

**`platform_settings`**
| Column | Type | Description |
|---|---|---|
| id | UUID | Primary key |
| key | VARCHAR(100) | Unique setting key |
| value | TEXT | JSON string |
| description | TEXT | Human-readable description |
| updated_by | UUID FK | Admin who last changed this |
| updated_at | TIMESTAMPTZ | |

### MongoDB Schema

**Collection: `face_embeddings`**

One document per detected face per photo. A photo with 5 people = 5 documents.

```json
{
  "_id": "ObjectId",
  "event_id": "uuid-string",
  "photo_id": "uuid-string",
  "embedding": [0.231, -0.445, 0.112, "...512 floats total"],
  "bounding_box": {
    "x": 120,
    "y": 45,
    "width": 80,
    "height": 95
  },
  "detection_confidence": 0.994,
  "face_index": 0,
  "model_version": "ArcFace",
  "created_at": "ISODate"
}
```

**Indexes:**
- `event_id` (single)
- `photo_id` (single)
- `(event_id, photo_id)` (compound)

The compound index is the hot path — every face search fetches all embeddings for an event.

---

## Face Recognition Pipeline

### Detection

Galleria uses **RetinaFace** as the face detector. RetinaFace is a single-stage dense face localisation method that handles:
- Multiple faces per image (group photos)
- Varied face angles and orientations
- Small faces relative to image size
- Partial occlusion

Each detected face produces a **bounding box** (x, y, width, height) and a **detection confidence score** (0-1).

**Quality filters applied before storing:**
- Detection confidence must be ≥ 0.9 (configurable via `FACE_DETECTION_CONFIDENCE`)
- Bounding box must be ≥ 80×80 pixels (configurable via `FACE_MIN_SIZE`)

These filters eliminate background faces that are too distant or blurry for reliable matching.

### Embedding Extraction

After detection, each valid face region is passed through **ArcFace** (Additive Angular Margin Loss), a deep CNN that maps a face image to a point in 512-dimensional space.

Key properties of ArcFace embeddings:
- Same person across different photos → embeddings are geometrically close
- Different people → embeddings are geometrically distant
- Robust to lighting changes, minor angle variations, aging
- 512 float values per face, ~2KB per embedding

### Similarity Search

When a user scans their face, their embedding is compared against all embeddings in the event using **cosine similarity**:

```
similarity = (A · B) / (|A| × |B|)
```

Range: -1 (opposite) to 1 (identical). In practice, same-person matches score 0.7-0.95, different-person scores 0.0-0.5.

The search uses NumPy batch matrix multiplication for efficiency:

```python
# All N event embeddings loaded as a matrix
matrix = np.array([doc["embedding"] for doc in candidates])  # shape: (N, 512)
query = np.array(user_embedding)                              # shape: (512,)

# Normalize both
query_norm = query / np.linalg.norm(query)
matrix_norm = matrix / np.linalg.norm(matrix, axis=1, keepdims=True)

# Compute all similarities in one operation
scores = matrix_norm @ query_norm  # shape: (N,)
```

At 1,000 photos × 8 faces average = 8,000 embeddings, this runs in under 100ms on CPU.

**Deduplication:** A photo with 5 faces produces 5 embedding documents. If the user matches 3 of them (different photos of the same face), we keep only the highest score per photo.

### Threshold

Default similarity threshold: **0.6**. Configurable per deployment via `FACE_SIMILARITY_THRESHOLD`.

- Too high (> 0.8): misses valid matches (false negatives)
- Too low (< 0.4): returns wrong people (false positives)
- 0.6 is conservative — false positives are worse than false negatives in this context

---

## Access Control System

Events support four access modes:

### `link`
Anyone with the event URL can view the gallery. Logged-in users are automatically added as attendee members on first visit — the event appears in their "Attending" list.

### `code`
Attendees must enter a correct access code. The code is bcrypt-hashed at rest — even admins cannot read the plaintext. Logged-in users are added as members after successful verification — they never need to enter the code again.

### `approved_list`
Only emails explicitly added by the organizer can access the event. Requires login. Logged-in users on the list are automatically added as members. Organizers can add emails individually or in bulk, and revoke access at any time.

### `combined`
Either an approved list email (auto-grant) or a correct access code. Approved list users bypass the code entirely.

### Membership Persistence
For logged-in users, all access modes grant a permanent `EventMember` record after first successful access. This means:
- Returning visits require no re-verification
- The event appears in the user's attending list
- Organizers can remove members to revoke access
- Members can leave events themselves

---

## API Reference

All endpoints return:
```json
{
  "message": "Human-readable status message",
  "data": { }
}
```

Error responses:
```json
{
  "message": "Error description",
  "data": null,
  "errors": [
    { "field": "email", "message": "value is not a valid email", "type": "value_error" }
  ]
}
```

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | None | Register new account |
| POST | `/auth/login` | None | Login, receive JWT |
| GET | `/auth/me` | Bearer | Current user profile |

### Events
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/events` | Bearer | Create event |
| GET | `/events` | Bearer | My managed events |
| GET | `/events/attending` | Bearer | Events I attend |
| GET | `/events/{id}` | None | Event details |
| PATCH | `/events/{id}` | Organizer | Update event |
| DELETE | `/events/{id}` | Organizer | Soft delete event |
| POST | `/events/{id}/access/verify` | None | Verify access code |
| POST | `/events/{id}/members` | Organizer | Add co-organizer |
| DELETE | `/events/{id}/members/{uid}` | Organizer | Remove member |
| POST | `/events/{id}/invites` | Organizer | Add approved emails |
| DELETE | `/events/{id}/invites/{email}` | Organizer | Revoke invite |
| DELETE | `/events/{id}/leave` | Bearer | Leave event |

### Photos
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/events/{id}/photos` | Organizer | Bulk upload |
| POST | `/events/{id}/photos/attendee` | Attendee | Attendee upload |
| GET | `/events/{id}/photos` | Access | List photos |
| GET | `/events/{id}/photos/status` | Organizer | Processing status |
| GET | `/events/{id}/photos/pending-approval` | Organizer | Pending uploads |
| POST | `/events/{id}/photos/{pid}/approve` | Organizer | Approve upload |
| POST | `/events/{id}/photos/{pid}/reject` | Organizer | Reject upload |
| GET | `/events/{id}/photos/serve/{pid}` | Access | Serve photo file |
| PATCH | `/events/{id}/photos/{pid}` | Organizer | Set private/public |
| DELETE | `/events/{id}/photos/{pid}` | Organizer | Delete photo |

### Faces & Gallery
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/events/{id}/faces/scan` | Bearer | Scan face, build gallery |
| POST | `/events/{id}/faces/scan/anonymous` | None | Anonymous face scan |
| POST | `/events/{id}/faces/claim` | Bearer | Claim anonymous gallery |
| GET | `/events/{id}/gallery` | Access | Full event gallery |
| GET | `/events/{id}/gallery/me` | Bearer | My matched gallery |
| GET | `/events/{id}/gallery/anonymous` | None + token | Anonymous gallery |
| POST | `/events/{id}/gallery/{pid}/flag` | Bearer | Flag photo |
| DELETE | `/events/{id}/gallery/{pid}/flag` | Bearer | Unflag photo |

### Downloads
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/events/{id}/photos/{pid}/download` | Access | Download single photo |
| GET | `/events/{id}/gallery/me/download` | Bearer | Download gallery as zip |

### Admin
| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/admin/users` | Admin | List all users |
| GET | `/admin/users/{id}` | Admin | User detail |
| PATCH | `/admin/users/{id}` | Admin | Update user |
| GET | `/admin/events` | Admin | List all events |
| DELETE | `/admin/events/{id}` | Admin | Force delete event |
| GET | `/admin/settings` | Admin | All platform settings |
| PUT | `/admin/settings/{key}` | Admin | Update setting |
| GET | `/admin/stats` | Admin | Platform statistics |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/galleria.git
cd galleria

# Copy environment file and edit values
cp .env.example .env

# Install dependencies
uv sync

# Start infrastructure (PostgreSQL, MongoDB, Redis)
docker compose -f docker-compose.dev.yml up -d

# Run database migrations
uv run alembic upgrade head
```

---

## Environment Variables

```bash
# Application
APP_ENV=development                  # development | production
SECRET_KEY=your-secret-key          # JWT signing key — use a long random string
ACCESS_TOKEN_EXPIRE_MINUTES=60
ALGORITHM=HS256

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=galleria
POSTGRES_USER=galleria
POSTGRES_PASSWORD=galleria

# MongoDB
MONGO_HOST=localhost
MONGO_PORT=27017
MONGO_DB=galleria

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# Celery (derived from Redis settings in config.py)
# No separate env vars needed

# File Storage
STORAGE_BACKEND=local                # local | s3
LOCAL_STORAGE_PATH=./storage

# Face Recognition
FACE_DETECTOR_BACKEND=retinaface     # retinaface | mtcnn | opencv
FACE_MODEL_NAME=ArcFace
FACE_SIMILARITY_THRESHOLD=0.6        # 0.0 - 1.0, higher = stricter matching
FACE_DETECTION_CONFIDENCE=0.9        # minimum detector confidence to store embedding
FACE_MIN_SIZE=80                     # minimum face bounding box size in pixels

# Anonymous scan
ANONYMOUS_SCAN_TTL_SECONDS=7200      # 2 hours

# Cache
DEFAULT_CACHE_TTL=300                # 5 minutes
```

---

## Running the Application

### Development (recommended)

Run infrastructure in Docker, application locally for fast reload:

```bash
# Start infrastructure only
docker compose -f docker-compose.dev.yml up -d

# Start FastAPI application
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Start Celery worker (in separate terminal)
uv run celery -A app.workers.celery_app worker --loglevel=info --concurrency=2
```

API docs available at: http://localhost:8000/docs

### Full Docker

```bash
docker compose up
```

### Create First Admin User

After running migrations and starting the app, promote a user to admin:

```bash
docker compose exec postgres psql -U galleria -d galleria -c \
  "UPDATE users SET is_admin=true WHERE email='your@email.com';"
```

---

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=app --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_face_search.py -v
```

---

## Deployment

### Production Checklist

- [ ] Set `APP_ENV=production` — enables JSON logging and file rotation
- [ ] Use a strong random `SECRET_KEY` (minimum 32 characters)
- [ ] Set `STORAGE_BACKEND=s3` and configure AWS credentials
- [ ] Use managed PostgreSQL (AWS RDS, Supabase, etc.)
- [ ] Use managed MongoDB (Atlas)
- [ ] Use managed Redis (ElastiCache, Upstash, etc.)
- [ ] Mount DeepFace weights as a Docker volume to survive container rebuilds
- [ ] Set `FACE_SIMILARITY_THRESHOLD` based on your accuracy requirements
- [ ] Configure CORS `allow_origins` to your frontend domain only
- [ ] Run Celery with at least 2 workers for parallel photo processing
- [ ] Set up log aggregation (Datadog, Loki, CloudWatch)

### Docker Volume for Model Weights

Add to `docker-compose.yml` to persist DeepFace model weights across rebuilds:

```yaml
worker:
  volumes:
    - ./storage:/app/storage
    - deepface_weights:/root/.deepface

volumes:
  deepface_weights:
```

The weights (~256MB for ArcFace + RetinaFace) download once and are reused. Without this, every container rebuild triggers a ~8 minute re-download.

---

## Design Decisions

### Why two databases?

**PostgreSQL** handles relational data — users, events, memberships, photos metadata. It's ideal for structured queries with foreign keys, joins, and constraints.

**MongoDB** handles face embeddings. Each photo generates a variable number of embedding documents (0 to N faces). The embedding itself is a 512-element float array that PostgreSQL's ARRAY type could technically store, but MongoDB's document model is a better fit for:
- Storing arbitrary metadata alongside each embedding (bounding box, confidence, model version)
- Scoping queries by event_id without touching photo metadata
- Future migration to MongoDB Atlas Vector Search for approximate nearest neighbor

### Why Celery over FastAPI background tasks?

FastAPI background tasks run in the same process as the web server. Face detection (DeepFace + TensorFlow) is CPU-intensive and would block request handling. Celery runs in separate worker processes with:
- Isolated memory (TensorFlow model loaded once per worker)
- Configurable concurrency (CPU-bound tasks benefit from process-level parallelism)
- Retry logic with exponential backoff
- Task state tracking
- `acks_late=True` ensuring a photo is never lost if a worker crashes mid-processing

### Why store storage keys instead of paths?

The `Photo.storage_key` is an opaque identifier (UUID hex). The storage service resolves it to an actual file path internally. This means:
- Migrating from local to S3 storage requires zero database changes
- The database doesn't encode assumptions about storage structure
- Reorganizing file layout (e.g., by year/month) doesn't break existing records

### Why serve photos through the application instead of direct URLs?

The storage directory is never publicly mounted. Every photo request goes through `GET /events/{id}/photos/serve/{photo_id}` which:
- Checks event access (link/code/approved list)
- Checks `is_private` flag
- Checks `status` (rejected photos return 404)
- Returns `Cache-Control` headers centrally

This means private photos are actually private — not just hidden from the UI but inaccessible via direct URL guessing.

### Why bcrypt for access codes?

Access codes are secrets. If the database is compromised, plaintext codes would immediately expose every event. Bcrypt hashing means:
- Codes are never stored in readable form
- Verifying a code requires the same bcrypt check as passwords
- Brute-forcing is computationally expensive

### Why `model_version` on face embeddings?

ArcFace models improve over time. If we ever upgrade from the current model version, embeddings generated by the old model are incompatible with the new one — cosine similarity between embeddings from different model versions is meaningless. Storing `model_version` means we can identify which embeddings need reprocessing after an upgrade.

### Why JSONB for event settings?

Event settings (`allow_attendee_uploads`, `require_upload_approval`, etc.) are stored as JSONB rather than individual columns. This means:
- Adding a new setting requires no migration
- Settings can vary per event without schema changes
- The column is queryable and indexable in PostgreSQL

The tradeoff is losing column-level type safety — mitigated by the `EventSettings` Pydantic schema that validates all writes.
