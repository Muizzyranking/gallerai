# Galleria

> Event photo and video sharing powered by face recognition. Find yourself in every moment.

Galleria is a full-stack event media platform that eliminates the tedious process of scrolling through hundreds of event photos to find yourself. Organizers upload photos and videos, Galleria processes them asynchronously using deep learning face recognition, and attendees scan their face once to instantly retrieve every photo they appear in.

---

## Table of Contents

- [Overview](#overview)
- [How It Works](#how-it-works)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Database Design](#database-design)
- [Storage Architecture](#storage-architecture)
- [Face Recognition Pipeline](#face-recognition-pipeline)
- [Access Control System](#access-control-system)
- [API Reference](#api-reference)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running the Application](#running-the-application)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [Design Decisions](#design-decisions)
- [Roadmap](#roadmap)

---

## Overview

Galleria solves a universal problem at events — weddings, conferences, graduations, sports days, parties. The photographer takes hundreds of photos and clips. They get uploaded somewhere. Attendees spend an hour scrolling through all of them trying to find the ten that include them.

With Galleria, that process takes ten seconds. The attendee opens the event link, taps "Find My Photos", holds their phone up to their face, and gets back a filtered gallery containing only the photos they appear in. They can download them all with one tap.

For organizers, Galleria provides a clean dashboard to create events, upload media in bulk (with live progress and an ETA), control who has access, manage co-organizers, and optionally allow attendees to contribute their own photos and videos.

---

## How It Works

### Media Processing (Organizer & Attendee Upload)

Uploads are ingested in fixed-size chunks rather than one giant batch, so a 300-file upload starts processing its first files while the last ones are still being received and hashed.

```
Organizer or attendee uploads files
        ↓
FastAPI receives files → validates type/size → streams each one to a TEMP
staging area, hashing while writing (one I/O pass, not a hash-then-write pass)
        ↓
Per chunk of ~25 files: one batched dedupe query against existing event
media, one bulk insert for the survivors (a race between two concurrent
uploaders is caught by the DB's UNIQUE constraint and retried row-by-row,
not lost)
        ↓
Celery tasks dispatched immediately for that chunk — later chunks don't
wait on earlier ones to finish uploading
        ↓
For each IMAGE, sequentially:
    1. Detect faces (RetinaFace) on the temp file — large images are
       downscaled first for speed, with results scaled back to original
       coordinates
    2. Filter out low-confidence (< FACE_DETECTION_CONFIDENCE) and tiny
       (< FACE_MIN_SIZE) detections
    3. Extract a 512-dim ArcFace embedding per remaining face, store one
       row per face in Postgres (pgvector)
    4. Promote the temp file to whichever backend is currently configured
       (local / Cloudinary / Cloudflare R2 — identical call shape for all
       three) and delete the temp copy
For each VIDEO: promote only — no face pipeline runs on video
        ↓
Progress is published to Redis, keyed by event_id (not a one-off upload id)
        ↓
Organizer or attendee watches live progress + ETA via GET
/events/{id}/media/upload-progress (Server-Sent Events) — reconnect any
time and get the current state immediately, including progress from OTHER
people uploading to the same event concurrently
```

### Face Search (Attendee Side)

```
Attendee uploads or webcam-captures a clear photo of their face
        ↓
FastAPI extracts a single 512-dim ArcFace embedding from the image
        ↓
Native pgvector cosine-similarity query against face_embeddings for this
event — the comparison runs inside Postgres, not in application memory
        ↓
Deduplicates by media — keeps highest score per photo
Filters results above similarity threshold (default: 0.6)
        ↓
Returns matched media IDs sorted by confidence score

If registered user:
    → Saves embedding to user profile
    → Upserts matched media into user_event_galleries table
    → User can revisit gallery without rescanning

If anonymous user:
    → Matched media IDs stored in Redis with 2-hour TTL
    → Returns scan_token for gallery retrieval
    → Embedding is never persisted
```

---

## Features

### For Organizers
- Create events with configurable access control (link, code, approved list, or combined)
- Bulk photo/video upload with an async, chunked, concurrent processing pipeline
- Live upload progress with ETA via Server-Sent Events — reconnect any time, see progress from all concurrent uploaders
- Co-organizer management
- Media visibility controls (public/private per item)
- Attendee upload support with optional approval workflow
- Event settings (downloads, gallery visibility, attendee uploads)

### For Attendees
- Face scan via webcam capture or photo upload
- Instant gallery of matched photos
- Download individual media or entire gallery as zip
- False match flagging with reason (not me / dislike / remove)
- Anonymous access — no account required
- Gallery claiming — convert anonymous results into a saved account gallery
- Attended events list for revisiting galleries
- Google OAuth login, alongside email/password

### Platform
- Transactional email — verification, password reset, welcome (see `templates/emails/`)
- Structured JSON logging with rotating file handler in production

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENT LAYER                             │
│   Organizer Dashboard    Event Gallery    Face Scan UI           │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP/REST + SSE
┌──────────────────────────────▼──────────────────────────────────┐
│                      FASTAPI APPLICATION                          │
│                                                                   │
│  /auth   /events   /media   /faces   /gallery   /downloads       │
│                                                                   │
│  Middleware: CORS, RequestID                                      │
│  Global exception handlers → consistent ApiResponse shape         │
└──────┬───────────┬──────────────┬──────────────┬────────────────┘
       │           │              │              │
┌──────▼──┐  ┌────▼─────┐  ┌────▼─────┐  ┌────▼─────────────────┐
│  Auth   │  │  Event   │  │  Media   │  │    Face Service       │
│ Service │  │ Service  │  │ Service  │  │  (DeepFace/ArcFace)   │
└──────┬──┘  └────┬─────┘  └────┬─────┘  └────┬─────────────────┘
       │          │              │              │
┌──────▼──────────▼──────────────▼──────────────▼─────────────────┐
│                        DATA LAYER                                 │
├───────────────────────────────────┬───────────────────────────────┤
│              PostgreSQL             │              Redis            │
│         (asyncpg + pgvector)        │                                │
│                                     │  Celery broker + result backend│
│  users, events, event_members,      │  Event-scoped upload progress  │
│  event_invites, media,              │  (live counter + pub/sub)      │
│  face_embeddings (vector(512)),     │  Anonymous scan tokens         │
│  user_event_galleries               │  Application cache             │
└───────────────────────────────────┴───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                    CELERY WORKER POOL                             │
│                                                                   │
│   process_image_batch_task   process_video_batch_task             │
│                                                                   │
│   Per image, sequential: detect faces THEN promote to storage.    │
│   Different images in a chunk still run concurrently against      │
│   each other. Face-detection model is loaded ONCE per worker      │
│   process (worker_process_init hook), not per task or per image.  │
│   Celery's own --concurrency IS the parallelism across CPU cores  │
│   for the CPU-bound detection step.                                │
└──────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                      FILE STORAGE                                 │
│                                                                   │
│   Staging (temp) and final storage are deliberately separate,     │
│   even when both happen to be local disk:                         │
│                                                                   │
│   storage/tmp/{event_id}/{key}.ext        ← every upload lands    │
│                                              here first, always    │
│                                                                   │
│   BaseStorage.save_from_path() — one call shape, three backends:  │
│     local        → storage/final/events/{event_id}/photos/{key}   │
│     cloudinary    → CDN with on-the-fly transforms (existing       │
│                      presets)                                     │
│     cloudflare    → R2, thumbnail/display variants generated once │
│                      at promotion time, served via a custom        │
│                      domain (or presigned URLs)                   │
│                                                                   │
│   The temp copy is always deleted by the SAME cleanup step after  │
│   promotion, regardless of which backend was used — "local" being │
│   a real final destination behaves identically to a cloud backend │
│   from the pipeline's point of view.                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| API Framework | FastAPI | Async support, automatic OpenAPI docs, Pydantic v2 |
| Language | Python 3.12 | Type aliases, improved generics, performance |
| Package Manager | uv | Fast, modern Python package management |
| Database | PostgreSQL 16 + pgvector | Users, events, media, galleries, AND face embeddings — one database, native vector similarity search |
| Cache / Broker | Redis 7 | Celery message broker, result backend, event-scoped upload progress (pub/sub), anonymous tokens, app cache |
| Task Queue | Celery 5 | Async media processing, chunked batch tasks, retries, worker isolation |
| Face Detection | DeepFace + RetinaFace | Best accuracy for group photos with varied angles/lighting |
| Face Recognition | ArcFace | State-of-the-art accuracy, 512-dim embeddings |
| Similarity Search | pgvector (`<=>` cosine distance) | Native in-database search — no embeddings ever leave Postgres for a query |
| ORM | SQLAlchemy 2 (async, asyncpg) | Mapped columns, type-safe async queries |
| Migrations | Alembic | Schema versioning |
| File Storage | Local / Cloudinary / Cloudflare R2 | Three symmetric backends behind one interface — swappable per deployment, and a given media item can permanently live on any one of them |
| Auth | JWT + Google OAuth 2.0 | Email/password and social login |
| Email | Jinja2 templates + your provider | Verification, password reset, welcome emails |
| Containerization | Docker + Compose | Reproducible dev and production environments |

---

## Project Structure

```
galleria/
├── app/
│   ├── admin/                       # Reserved for the admin panel — see Roadmap
│   ├── api/
│   │   ├── router/
│   │   │   ├── auth.py              # Registration, login, /me, Google OAuth
│   │   │   ├── downloads.py         # Single media and zip downloads
│   │   │   ├── event.py             # Event CRUD, access, members, invites
│   │   │   ├── faces.py             # Face scan, anonymous scan, gallery claim
│   │   │   ├── gallery.py           # Gallery retrieval, flag management
│   │   │   └── media.py             # Upload (organizer + attendee), serve, upload-progress SSE
│   │   └── dependencies.py          # FastAPI dependency injection
│   │
│   ├── core/
│   │   ├── config.py                # Pydantic Settings — .env driven
│   │   ├── enums.py                 # All domain enums (MediaStatus, StorageStatus, etc.)
│   │   ├── schemas.py               # ApiResponse[T] wrapper, ApiErrorResponse
│   │   ├── security.py              # JWT, password hashing, access code hashing
│   │   ├── logging.py               # Colored dev logging, JSON prod logging
│   │   ├── cache.py                 # Namespaced Redis cache
│   │   ├── middleware.py            # RequestID middleware
│   │   ├── exceptions.py            # Global exception handlers
│   │   ├── pagination.py            # PaginationParams dependency
│   │   └── utils.py                 # Shared helpers (utcnow, generate_uuid, ...)
│   │
│   ├── db/
│   │   ├── postgres.py              # Async SQLAlchemy engine/session, Base, BaseModel, TimestampMixin
│   │   └── redis.py                 # Async Redis client singleton
│   │
│   ├── models/                      # SQLAlchemy ORM models
│   │   ├── user.py
│   │   ├── event.py
│   │   ├── media.py                 # Media — images AND videos, storage_backend/status, temp_path
│   │   ├── face_embedding.py        # One row per detected face, vector(512), pgvector
│   │   ├── gallery.py               # UserEventGallery
│   │   ├── platform.py
│   │   └── tokens.py                # Email verification / password reset tokens
│   │
│   ├── schemas/                     # Pydantic v2 request/response schemas
│   │
│   ├── services/
│   │   ├── auth/
│   │   │   ├── auth_service.py
│   │   │   ├── google_oauth.py
│   │   │   └── utils.py
│   │   ├── storage_service/
│   │   │   ├── base.py              # BaseStorage — save_from_path() only
│   │   │   ├── temp.py              # TempStorage — staging, not a BaseStorage
│   │   │   ├── local.py             # Peer final backend, symmetric with cloud ones
│   │   │   ├── cloudinary.py
│   │   │   ├── cloudflare.py
│   │   │   ├── factory.py           # get_storage(backend) + shared temp_storage singleton
│   │   │   ├── schemas.py           # SaveResult, per-backend Extras types
│   │   │   ├── constants.py
│   │   │   └── exceptions.py
│   │   ├── media_service.py         # Chunked ingest, dedupe, bulk insert, dispatch
│   │   ├── face_service.py          # DeepFace wrapper: detect_faces_optimized, warm_up
│   │   ├── search_service.py        # pgvector cosine similarity search
│   │   ├── gallery_service.py
│   │   ├── download_service.py
│   │   ├── email_service.py
│   │   ├── event_service.py
│   │   └── platform_service.py
│   │
│   ├── templates/emails/            # base.html + verify_email, password_reset, welcome
│   │
│   ├── workers/
│   │   ├── celery_app.py            # Celery config, worker_process_init model warm-up hook
│   │   └── media_tasks.py           # process_image_batch_task, process_video_batch_task
│   │
│   └── main.py
│
├── alembic/
├── storage/                         # Local dev storage (git-ignored)
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Database Design

Galleria runs on a single PostgreSQL database. Face embeddings previously lived in MongoDB in an earlier version of this project; they now live in Postgres as `vector(512)` columns via the `pgvector` extension, queried with native `<=>` cosine-distance operators. See [Design Decisions](#design-decisions) for why.

### Key Tables

**`users`** — email, password_hash, display_name, face_embedding (nullable, for repeat scans), is_admin, is_active.

**`events`** — owner_id, title, description, event_date, status, is_private, access_mode, access_code_hash (bcrypt), settings (JSONB: allow_attendee_uploads, require_upload_approval, downloads_enabled, gallery_visible).

**`event_members`** — event_id, user_id, role (organizer/attendee), status, added_by. Unique on (event_id, user_id).

**`event_invites`** — event_id, email, invite_token, status, accepted_at.

**`media`** — event_id, uploaded_by (nullable — anonymous attendee uploads), file_hash (unique per event), filename, file_size, mime_type, media_type (image/video), width, height, storage_key (unique), **storage_backend** (nullable — `NULL` until actually promoted), **storage_status** (`PENDING` → `PROMOTED`), **temp_path** (staging location, cleared after promotion + processing both terminal), extras (JSONB, backend-specific), face_count, status (media_type-specific processing state), is_private (gallery-visibility flag, unrelated to storage access), error_message, processed_at, uploaded_at.

**`face_embeddings`** — event_id (denormalized from media, so search never joins), media_id, embedding (`vector(512)`), bounding_box (JSON), detection_confidence, face_index, model_version, created_at. Unique on (media_id, face_index).

**`user_event_galleries`** — user_id, event_id, media_id, match_score, is_flagged, flag_reason. Unique on (user_id, event_id, media_id).

**`tokens`** — email verification and password reset tokens.

**`platform_settings`** — key/value admin config (JSON string value).

---

## Storage Architecture

Three backends implement one interface (`BaseStorage.save_from_path()`), deliberately excluding "receive a raw upload" from that interface — only `TempStorage` (not a `BaseStorage`) ever touches an `UploadFile` directly. This exists because **local storage can be either scratch space or a genuine permanent destination**, and those two roles must never be conflated:

1. Every upload streams into `TempStorage` first — hashed while writing, one I/O pass. This happens regardless of the event's configured backend.
2. Face detection (for images) runs against the temp file.
3. The temp file is promoted via `save_from_path()` to whichever backend is configured — including "local," which copies it into a *separate* permanent directory tree, not the temp one.
4. The temp copy is deleted by the same cleanup step no matter which backend was used.

This means `Media.storage_backend` is `NULL` until step 3 actually succeeds — before this redesign it defaulted to `LOCAL`, which became ambiguous the moment "local" stopped meaning "not done yet" and started being a valid permanent choice.

Cloudflare R2 has no on-the-fly image transforms, so thumbnail/display variants are generated once with Pillow at promotion time and stored as separate objects, served via a custom domain (free, real CDN caching) or presigned URLs if no public domain is configured. Cloudinary keeps using on-request transformations via existing presets. Local storage serves through `GET /events/{id}/media/serve/{key}`, which also transparently covers the brief window before a file has been promoted anywhere.

---

## Face Recognition Pipeline

### Detection

RetinaFace handles multiple faces per image, varied angles, small faces, and partial occlusion. Each detected face produces a bounding box and a confidence score.

**Speed optimization:** images with a long edge above ~1600px are downscaled before detection — accuracy gains above that resolution are marginal, but detection cost scales with pixel count. Bounding boxes are scaled back to the original image's coordinates afterward, so downstream cropping/display stays correct.

**Quality filters applied before storing:**
- Detection confidence ≥ `FACE_DETECTION_CONFIDENCE` (default 0.9)
- Bounding box ≥ `FACE_MIN_SIZE` pixels (default 80×80)

Videos skip this entire stage — only images participate in face search.

### Embedding Extraction

Each valid face region is passed through ArcFace, producing a 512-dimensional embedding. Same person → geometrically close embeddings; different people → distant. Robust to lighting, minor angle changes, aging.

**Model loading:** the DeepFace model is warmed up once per Celery worker *process* (via a `worker_process_init` signal), not per task or per image — see `workers/celery_app.py`. Celery's own `--concurrency` is the source of multi-core parallelism for this CPU-bound step; there's deliberately no second layer of subprocess parallelism inside a task, which would oversubscribe cores.

### Similarity Search

pgvector's `<=>` operator computes cosine distance natively inside Postgres:

```sql
SELECT media_id::text, MAX(1 - (embedding <=> :query_vec)) AS similarity
FROM face_embeddings
WHERE event_id = :event_id
  AND 1 - (embedding <=> :query_vec) > :threshold
GROUP BY media_id
ORDER BY similarity DESC
LIMIT :limit
```

No embeddings are pulled into application memory for a search — the comparison, filtering, deduplication (`MAX` per `media_id`), and ranking all happen in one query.

### Threshold

Default: **0.6**, configurable via `FACE_SIMILARITY_THRESHOLD`. Higher = stricter (fewer false positives, more false negatives); lower = the reverse. 0.6 is conservative — false positives are worse than false negatives here.

---

## Access Control System

*(unchanged from the original design — see previous documentation for `link`, `code`, `approved_list`, and `combined` access modes, and membership persistence behavior.)*

---

## API Reference

All endpoints return `{ "message": ..., "data": ... }`; errors return `{ "message": ..., "data": null, "errors": [...] }`.

### Auth
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/register` | None | Register new account |
| POST | `/auth/login` | None | Login, receive JWT |
| GET | `/auth/google` | None | Start Google OAuth flow |
| GET | `/auth/me` | Bearer | Current user profile |

### Events
*(unchanged — see Access Control section for the access-mode-specific endpoints.)*

### Media
| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/events/{id}/media` | Organizer | Bulk upload (chunked, concurrent) |
| POST | `/events/{id}/media/attendee-upload` | Access | Attendee upload — anonymous or authenticated, subject to approval setting |
| GET | `/events/{id}/media` | Access | List media |
| GET | `/events/{id}/media/upload-progress` | Access | **New** — SSE stream, live progress + ETA, open to anyone who can access the event |
| GET | `/events/{id}/media/pending-approval` | Organizer | Pending attendee uploads |
| POST | `/events/{id}/media/{media_id}/approve` | Organizer | Approve upload |
| POST | `/events/{id}/media/{media_id}/reject` | Organizer | Reject upload |
| GET | `/events/{id}/media/serve/{key}` | Access | Serve media file — proxies while local/pending, redirects to the real backend once promoted |
| PATCH | `/events/{id}/media/{media_id}` | Organizer | Set private/public |
| DELETE | `/events/{id}/media/{media_id}` | Organizer | Delete media |

### Faces & Gallery
*(unchanged — see previous documentation.)*

### Downloads
*(unchanged — see previous documentation.)*

---

## Getting Started

### Prerequisites
- Python 3.12+
- Docker and Docker Compose
- uv

### Installation

```bash
git clone https://github.com/yourusername/galleria.git
cd galleria
cp .env.example .env
uv sync
docker compose -f docker-compose.dev.yml up -d   # PostgreSQL + Redis
uv run alembic upgrade head
```

---

## Environment Variables

```bash
# Application
APP_ENV=development
SECRET_KEY=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=60
ALGORITHM=HS256

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=galleria
POSTGRES_USER=galleria
POSTGRES_PASSWORD=galleria

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0

# File Storage
STORAGE_BACKEND=local                 # local | cloudinary | cloudflare
TEMP_STORAGE_ROOT=./storage/tmp       # staging — used regardless of final backend
LOCAL_STORAGE_FINAL_ROOT=./storage/final

CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
CLOUDINARY_FOLDER_PREFIX=galleria

CLOUDFLARE_ACCOUNT_ID=
CLOUDFLARE_ACCESS_KEY_ID=
CLOUDFLARE_SECRET_ACCESS_KEY=
CLOUDFLARE_BUCKET=
CLOUDFLARE_PUBLIC_BASE_URL=           # optional — omit to use presigned URLs

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=

# Email
EMAIL_FROM_ADDRESS=
EMAIL_PROVIDER_API_KEY=               # fill in for your provider (SES/Resend/SMTP/etc.)

# Face Recognition
FACE_DETECTOR_BACKEND=retinaface
FACE_MODEL_NAME=ArcFace
FACE_SIMILARITY_THRESHOLD=0.6
FACE_DETECTION_CONFIDENCE=0.9
FACE_MIN_SIZE=80

# Anonymous scan
ANONYMOUS_SCAN_TTL_SECONDS=7200

# Cache
DEFAULT_CACHE_TTL=300
```

> Variable names above match the concepts introduced in this rewrite — cross-check exact names against `app/core/config.py`, which wasn't part of this pass.

---

## Running the Application

### Development

```bash
docker compose -f docker-compose.dev.yml up -d
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
uv run celery -A app.workers.celery_app worker --loglevel=info --concurrency=2
```

API docs: http://localhost:8000/docs

### Full Docker

```bash
docker compose up
```

---

## Running Tests

```bash
uv run pytest
uv run pytest --cov=app --cov-report=term-missing
```

---

## Deployment

### Production Checklist
- [ ] `APP_ENV=production` — JSON logging, file rotation
- [ ] Strong random `SECRET_KEY` (32+ characters)
- [ ] `STORAGE_BACKEND=cloudinary` or `cloudflare` — size Celery `--concurrency` to available CPU cores, since that's the parallelism source for face detection
- [ ] Managed PostgreSQL with the `pgvector` extension enabled
- [ ] Managed Redis
- [ ] Mount DeepFace weights as a Docker volume to survive rebuilds
- [ ] Configure CORS `allow_origins` to your frontend domain only
- [ ] Set up log aggregation

### Docker Volume for Model Weights

```yaml
worker:
  volumes:
    - ./storage:/app/storage
    - deepface_weights:/root/.deepface

volumes:
  deepface_weights:
```

---

## Design Decisions

### Why Postgres + pgvector instead of MongoDB?

An earlier version of this project stored face embeddings in MongoDB, with similarity search done via a numpy batch cosine-similarity operation in application memory. That worked, but meant every search pulled every candidate embedding for an event out of the database first. Moving embeddings into Postgres as `vector(512)` columns lets pgvector's `<=>` operator do the comparison, filtering, and ranking natively in one SQL query — nothing leaves the database for a search, and there's only one database to operate instead of two.

### Why separate temp storage from final storage?

Local disk can be a legitimate *permanent* backend, not just scratch space — an event can be configured to store its media on local disk forever. If staging and final storage were the same thing, "is this file safe to delete" and "is this file done processing" would become the same ambiguous question. Splitting them means promotion (`save_from_path()`) behaves identically whether the target is local disk, Cloudinary, or Cloudflare — and cleanup of the temp copy is one code path, not one per backend.

### Why is `storage_backend` nullable instead of defaulting to `LOCAL`?

It used to default to `LOCAL`, which was indistinguishable from "genuinely promoted to local storage" once local became a real final destination. `NULL` now unambiguously means "not promoted yet" — `storage_status` is the source of truth for promotion state, and `storage_backend` is only meaningful once `storage_status == PROMOTED`.

### Why detect faces before promoting to cloud, not concurrently?

Within a single media item, detection runs to completion before promotion starts. This keeps the pipeline for one photo strictly ordered — a photo is never shipped to its final backend before its own face-detection pass has finished (or definitively failed). Different photos in the same batch still process concurrently against each other.

### Why chunk the upload instead of processing the whole batch at once?

Bounds the size of any single dedupe query or bulk insert regardless of how many files come in at once, and lets Celery start on the first chunk while later chunks are still being hashed — relevant once you're optimizing for hundreds of files from multiple people uploading to the same event simultaneously.

### Why event-scoped upload progress instead of a per-request batch ID?

A batch ID has to be remembered and passed around by the client, and doesn't naturally aggregate multiple people uploading to the same event at once. Keying progress by `event_id` in Redis means any client can open the SSE stream with nothing but an ID it already has, get the current state immediately on connect, and see progress from every concurrent uploader — not just its own upload.

### Why load the face-detection model once per worker process, not per task?

DeepFace's model load is the expensive part. Loading it inside the task body means paying that cost on every single Celery task; loading it once when the worker process starts (`worker_process_init`) means paying it once per process, ever, and every task that process runs afterward reuses it — while sizing Celery's `--concurrency` to available cores gives real multi-core parallelism for the CPU-bound detection step without oversubscribing them.

### Why store storage keys instead of paths?

`Media.storage_key` is an opaque identifier. The storage service resolves it internally, so migrating backends or reorganizing file layout requires zero database changes.

### Why serve media through the application instead of direct URLs?

The storage directory is never publicly mounted. `GET /events/{id}/media/serve/{key}` checks event access and the `is_private`/`status` flags, and only proxies bytes itself while a file is local or still processing — once promoted to a cloud backend, it redirects to that backend's own URL (public CDN link or presigned) rather than proxying, so the app server doesn't carry gallery bandwidth once a file has a real home.

### Why bcrypt for access codes?

Codes are secrets; bcrypt means they're never stored in readable form and brute-forcing is computationally expensive.

### Why `model_version` on face embeddings?

If the ArcFace model is ever upgraded, embeddings from the old model are incompatible with the new one — cosine similarity between them is meaningless. Storing `model_version` identifies which rows need reprocessing after an upgrade.

### Why JSONB for event settings?

New settings need no migration, can vary per event, and stay queryable — validated on write via the `EventSettings` Pydantic schema.

---

## Roadmap

- **Admin panel** — described in an earlier draft of this README as already built; it isn't yet. `app/admin/` is a placeholder and there's no `/admin` API router. Planned: user/event management, platform settings, audit trail, platform statistics.
- Reprocessing flow for embeddings after a `model_version` upgrade.
