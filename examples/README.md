# AI Call Quality & Coaching Platform — Public Code Examples

This folder contains simplified, sanitized examples based on implementation patterns used in the private production QA and coaching platform.

The goal is to show how I approached queueing, AI analysis, duplicate protection, coaching workflow enforcement, analytics, and live dashboard updates without publishing production source code or exposing internal QA standards, employee information, credentials, infrastructure identifiers, or company-specific business rules.

These are representative examples rather than copies of the production codebase.

---

## Included Examples

### [`cloud-task-intake.py`](cloud-task-intake.py)

Shows how finalized call recordings are turned into deterministic Google Cloud Tasks.

**Demonstrates:**

- Cloud Storage event handling
- File-type validation
- Companion metadata checks
- Immutable object-generation tracking
- Deterministic task identifiers
- At-least-once event deduplication
- OIDC-authenticated Cloud Tasks
- Burst buffering before AI analysis

---

### [`structured-qa-worker.py`](structured-qa-worker.py)

Shows the core analysis-worker pattern.

The worker validates the queued request, rejects duplicate or ineligible calls before model use, acquires an atomic processing lock, requests structured AI output, applies deterministic scoring in Python, persists the result, and only then publishes a notification-ready marker.

**Demonstrates:**

- Queue-driven background processing
- Eligibility gates before AI spend
- Atomic processing locks
- Structured LLM output
- Schema validation
- Deterministic weighted scoring
- AI / application responsibility separation
- Checkpoint-oriented retry design
- Persistence-before-notification ordering
- Integrity-based notification gating

---

### [`duplicate-safe-coaching-notifier.py`](duplicate-safe-coaching-notifier.py)

Shows how coaching notifications are protected from duplicate Eventarc delivery.

**Demonstrates:**

- Cloud event processing
- Durable idempotency locks
- At-most-once notification design
- SMTP delivery
- Sent / skipped / failed state handling
- Retry behavior
- Separation between notification state and QA analysis state

---

### [`coaching-lifecycle.sql`](coaching-lifecycle.sql)

Shows the database-enforced coaching-session lifecycle.

**Demonstrates:**

- PostgreSQL state-machine enforcement
- Immutable source identity
- Controlled status transitions
- Required scheduling and follow-up fields
- Agent acknowledgment tracking
- Append-only lifecycle events
- Trigger-based audit history
- Row Level Security

---

### [`coaching-effectiveness.sql`](coaching-effectiveness.sql)

Shows how the platform measures performance before and after a coaching session.

**Demonstrates:**

- Pre/post comparison windows
- SQL filtered aggregates
- Protection against overlapping coaching periods
- Minimum sample-size requirements
- Score deltas
- Category-level deltas
- RLS-compatible reporting functions

---

### [`realtime-call-updates.tsx`](realtime-call-updates.tsx)

Shows the dashboard pattern used to refresh call data as worker and notification updates arrive.

**Demonstrates:**

- Supabase Realtime
- Environment-scoped subscriptions
- Debounced UI refreshes
- Connection-state visibility
- React cleanup and lifecycle handling

---

## Architecture Represented

```text
Call Recording + Metadata
          ↓
Cloud Storage
          ↓
Event-Driven Intake
          ↓
Deterministic Cloud Task
          ↓
Private Analysis Worker
          ↓
Eligibility / Duplicate Checks
          ↓
Atomic Processing Lock
          ↓
Structured AI Analysis
          ↓
Deterministic Application Scoring
          ↓
PostgreSQL / Supabase
          ↓
Completion Marker
          ↓
Duplicate-Safe Coaching Notification
```

The management application reads the resulting structured data through authenticated, row-level-security-protected database access.

```text
PostgreSQL / Supabase
        ↓
Next.js Dashboard
   ┌────┼─────────────┐
   ↓    ↓             ↓
Calls  Coaching   Management Analytics
        ↓
Coaching Lifecycle
        ↓
Pre/Post Effectiveness Measurement
```

---

## Why These Examples Are Included

The production platform needed to solve several reliability and governance problems beyond simply sending an audio file to an AI model.

Examples include:

- Many recordings arriving at once
- Preventing duplicate AI processing
- Avoiding model spend on ineligible calls
- Keeping employee identity authoritative outside the model
- Requiring structured AI output instead of relying on free-form text
- Calculating official scores in application code rather than allowing the model to own the final score
- Preventing duplicate coaching emails
- Enforcing valid coaching-session transitions
- Measuring whether coaching is followed by performance change
- Keeping management dashboards current as background processing completes

These examples show selected patterns used to solve those problems.

---

## Public-Safe Scope

The examples intentionally remove, rename, or generalize:

- Company names and branding
- Production Google Cloud project identifiers
- Production bucket and queue names
- Supabase project details
- Employee names and email addresses
- Internal team names
- Exact production QA rubrics
- Scoring thresholds and proprietary QA rules
- Internal coaching language
- Credentials and secrets
- Production table/function names where unnecessary
- Infrastructure configuration that is not needed to demonstrate the design
- Full production retry, migration, and recovery logic

The complete production implementation remains private.

These examples are portfolio representations of selected engineering patterns and are not intended to be drop-in replacements for the production system.
