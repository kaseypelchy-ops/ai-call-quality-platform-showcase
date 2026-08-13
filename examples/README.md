# AI Call Quality & Coaching Platform — Public Code Examples

This folder contains simplified, sanitized examples based on implementation patterns used in the private production QA and coaching platform.

The purpose of these examples is to show how I approached some of the harder engineering problems behind the system — queueing, duplicate prevention, structured AI analysis, deterministic scoring, coaching workflow enforcement, effectiveness measurement, and live dashboard updates — without publishing production source code or exposing internal QA standards, employee information, credentials, infrastructure identifiers, or company-specific business rules.

These are representative examples, not copies of the production codebase.

---

## Included Examples

### [`cloud-task-intake.py`](cloud-task-intake.py)

Demonstrates how finalized call recordings enter the analysis pipeline.

Instead of sending recordings directly to an AI worker, the intake process validates the incoming object, confirms that its companion metadata exists, and creates a deterministic Google Cloud Task for background processing.

Using deterministic task identifiers helps make repeated Cloud Storage or Eventarc delivery safe.

**Demonstrates:**

- Google Cloud Storage event handling
- File-type validation
- Companion metadata checks
- Immutable object-generation tracking
- Deterministic task identifiers
- At-least-once event deduplication
- OIDC-authenticated Cloud Tasks
- Queue-based workload buffering

Conceptually:

```text
Call Recording
      +
Metadata
      ↓
Cloud Storage Finalize Event
      ↓
Validate Input
      ↓
Generate Deterministic Task ID
      ↓
Cloud Tasks
      ↓
Analysis Worker
```

---

### [`structured-qa-worker.py`](structured-qa-worker.py)

Demonstrates the core background-analysis workflow.

The worker does more than send a recording to an AI model. It first verifies whether the call should be processed, rejects duplicate work, confirms the representative against authoritative application data, acquires an atomic processing lock, requests structured AI output, applies deterministic scoring in application code, persists the result, and only then allows downstream notification processing to begin.

**Demonstrates:**

- Queue-driven background processing
- Eligibility checks before AI model use
- Duplicate prevention
- Atomic processing locks
- Structured LLM output
- Schema validation
- Deterministic weighted scoring
- AI / application responsibility separation
- Checkpoint-oriented retry design
- Persistence-before-notification ordering
- Integrity-based notification gating

One of the important design decisions is separating what the AI is allowed to determine from what the application owns.

```text
Authoritative Application Data
        ↓
Representative Identity
        ↓
AI Analyzes Interaction
        ↓
Structured Category Results
        ↓
Application Calculates Official Score
        ↓
Persisted QA Record
```

The model provides structured analysis and evidence. The application remains responsible for employee identity, scoring rules, workflow state, and whether the result is eligible for official QA use.

---

### [`duplicate-safe-coaching-notifier.py`](duplicate-safe-coaching-notifier.py)

Demonstrates how coaching notifications are protected from duplicate cloud-event delivery.

Event-driven systems can deliver the same event more than once. The notification workflow therefore acquires a durable idempotency lock before contacting the mail server.

If another copy of the same event arrives later, it cannot send the same coaching message again.

**Demonstrates:**

- Cloud event processing
- Durable idempotency locks
- Duplicate-send prevention
- SMTP delivery
- Sent / skipped / failed states
- Retry behavior
- Notification receipts
- Separation between QA-processing state and notification state

Conceptually:

```text
Analysis Complete
      ↓
Completion Marker
      ↓
Notification Event
      ↓
Acquire Durable Lock
      ↓
Already Locked?
   /            \
 Yes             No
  ↓               ↓
Skip            Send Email
                  ↓
             Record Receipt
```

---

### [`coaching-lifecycle.sql`](coaching-lifecycle.sql)

Demonstrates how coaching sessions are enforced as a real workflow at the database level.

The application does not rely only on the front end to decide whether a coaching session can move from one state to another. PostgreSQL validates the transition and records lifecycle activity.

A simplified lifecycle looks like:

```text
Draft
  ↓
Scheduled
  ↓
Held
  ↓
Acknowledged
  ↓
Follow-Up Due
  ↓
Closed
```

Cancellation paths and other valid transitions can also be handled where appropriate.

**Demonstrates:**

- PostgreSQL workflow enforcement
- State-machine design
- Immutable source identity
- Controlled status transitions
- Required scheduling fields
- Required follow-up fields
- Agent acknowledgment tracking
- Append-only lifecycle events
- Trigger-based audit history
- Row Level Security

This keeps important workflow rules in the database instead of depending entirely on browser validation.

---

### [`coaching-effectiveness.sql`](coaching-effectiveness.sql)

Demonstrates how the platform measures whether coaching is followed by a measurable change in QA performance.

The query compares scored calls before and after a coaching session within defined time windows.

It also accounts for later coaching sessions so performance after a second intervention is not incorrectly attributed to an earlier one.

**Demonstrates:**

- Pre/post performance comparison
- Configurable comparison windows
- SQL filtered aggregates
- Minimum sample-size requirements
- Overall score deltas
- Category-level deltas
- Protection against overlapping coaching periods
- RLS-compatible reporting functions

Conceptually:

```text
Calls Before Coaching
        ↓
Average Performance
        ↓
Coaching Session
        ↓
Calls After Coaching
        ↓
Average Performance
        ↓
Compare Results
        ↓
Improved / Stable / Declined
```

The goal is to move coaching from a documented activity to something that can also be evaluated using measurable follow-up data.

---

### [`realtime-call-updates.tsx`](realtime-call-updates.tsx)

Demonstrates how the management application stays current as background processing completes.

The dashboard subscribes to relevant PostgreSQL changes through Supabase Realtime and refreshes the server-rendered data when new QA results or status updates arrive.

Closely spaced database events are debounced so one call moving through several background states does not cause unnecessary repeated page refreshes.

**Demonstrates:**

- React / Next.js
- Supabase Realtime
- PostgreSQL change subscriptions
- Environment-scoped updates
- Debounced UI refreshes
- Connection-state visibility
- React lifecycle cleanup

---

## Architecture Represented

Together, the examples represent several connected parts of the production analysis pipeline.

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

The structured QA records then feed the management and coaching application.

```text
PostgreSQL / Supabase
        ↓
Next.js Management Application
   ┌────┼─────────────────┐
   ↓    ↓                 ↓
Calls  Coaching      Management Analytics
        ↓
Coaching Lifecycle
        ↓
Follow-Up
        ↓
Pre/Post Effectiveness Measurement
```

---

## Reliability Patterns

The platform needed to handle several conditions that are common in event-driven and AI-processing systems.

### Duplicate Events

Cloud events and queued work can be delivered more than once.

The platform uses deterministic task identifiers, processing locks, and notification locks so repeated delivery does not automatically create duplicate analysis or duplicate coaching notifications.

### Burst Workloads

Call recordings do not always arrive at a steady rate.

Cloud Tasks acts as a buffer between incoming recordings and the analysis workers so ingestion does not depend on the AI worker processing every recording immediately.

### AI Cost Control

Calls are checked for duplicate state and representative eligibility before model processing begins.

This avoids unnecessary AI requests for work that should not be analyzed.

### Structured AI Output

The platform does not depend on free-form AI responses for operational reporting.

The model is asked to return a predictable structured response that can be validated before it is stored.

### Deterministic Scoring

The AI provides category-level analysis, but application code calculates the official weighted score.

This makes the scoring logic repeatable, testable, and versionable independently from the model prompt.

### Workflow Integrity

Coaching sessions have defined states and transitions.

Important lifecycle rules are enforced in PostgreSQL so invalid workflow changes cannot be introduced simply by bypassing the user interface.

### Notification Idempotency

The notification system reserves a durable state before SMTP delivery.

This protects representatives from receiving repeated coaching emails when cloud events are retried.

---

## Why These Examples Are Included

The production platform needed to solve problems beyond simply sending an audio recording to an AI model.

Examples include:

- Handling many recordings arriving close together
- Preventing duplicate AI processing
- Avoiding unnecessary model spend
- Validating representative eligibility before analysis
- Keeping employee identity authoritative outside the AI model
- Requiring structured AI output
- Keeping official scoring deterministic
- Persisting analysis safely before downstream actions begin
- Preventing duplicate coaching notifications
- Enforcing valid coaching-session transitions
- Maintaining an audit history
- Measuring coaching effectiveness over time
- Keeping management dashboards current as background work completes

These examples show selected patterns I used to solve those problems.

---

## Public-Safe Scope

The examples intentionally remove, rename, or generalize production-specific information, including:

- Company names and branding
- Google Cloud project identifiers
- Production bucket names
- Production queue names
- Production service names
- Supabase project details
- Employee names and email addresses
- Internal team names
- Exact production QA rubrics
- Proprietary scoring rules
- Internal coaching language
- Credentials and secrets
- Production table and function names where unnecessary
- Infrastructure configuration that is not required to demonstrate the design
- Full retry and recovery logic
- Production migration history

The complete production implementation remains private.

---

## Production Source

The full QA and coaching platform repository is maintained privately because it contains internal QA standards, employee workflows, infrastructure configuration, production integrations, operational data models, and company-specific business logic.

These examples are portfolio representations of selected implementation patterns.

They are intended to show how I designed and built the underlying system without exposing the production environment or proprietary operational data.
