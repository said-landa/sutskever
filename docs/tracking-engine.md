# Tracking Engine

## Purpose

The Tracking Engine transforms independent visual observations into temporary tracks.

It answers one question:

> Which observations most likely represent the same visible entity over time?

The Tracking Engine assigns a stable, temporary identifier within a bounded continuity window for a single camera.

It never identifies a real person.

It never understands zones.

It never makes business decisions.

---

# Responsibilities

The Tracking Engine is responsible for:

- Receiving frame-scoped observation batches from the Vision Engine.
- Associating new observations with existing tracks.
- Creating tracks for unmatched observations.
- Maintaining temporary identity across frames.
- Handling short gaps and occlusions.
- Ending tracks that can no longer be recovered reliably.
- Publishing track updates to downstream systems.
- Reporting tracking health and performance.

It is not responsible for:

- Capturing or decoding video.
- Detecting or classifying objects.
- Recognizing people.
- Matching entities across cameras.
- Understanding zones.
- Generating business events.
- Evaluating rules.
- Sending notifications.
- Storing permanent history.

---

# Philosophy

Observations describe what is visible in one frame.

Tracks describe continuity across frames.

Example

Frame 101

↓

Person observed at position A

Frame 102

↓

Person observed at position B

Frame 103

↓

Person observed at position C

The Tracking Engine determines that all three observations belong to:

Track 17

Track 17 is not a known person.

It is only a temporary visual identity.

---

# Input

The Tracking Engine receives atomic Observation Batches from the Vision Engine.

One batch represents the complete Vision Engine output for one frame.

Observation Batch is a technical transport envelope.

Observation remains the canonical domain concept.

Empty batches are valid and required.

They allow the tracker to advance time when a frame contains no observations.

Every batch must include:

- camera identifier
- source stream session identifier
- frame identifier or monotonic frame sequence
- capture timestamp
- ingestion timestamp
- coordinate space
- zero or more observations

The source stream session identifier is generated upstream.

It must change when a reconnect, reset or geometry-affecting source change invalidates frame continuity.

Coordinate space must declare:

- units, such as normalized or pixel coordinates
- frame width and height
- orientation
- transform or calibration version

Every observation used for tracking must include:

- observation identifier
- observation type
- detector confidence
- geometry
- detector identifier
- detector version
- model version

Geometry may contain:

- bounding box
- segmentation mask
- keypoints
- object center

Batch context may be referenced by each observation instead of repeated inside it.

This is the required boundary contract even if upstream components use a different internal representation.

Before implementation, the Video Gateway and Vision Engine public contracts must preserve and propagate this complete envelope.

The Tracking Engine must reject incomplete input instead of inventing missing identity, time or coordinate metadata.

Observation Batches and their observations are immutable.

The Tracking Engine may reference them, but it must never modify them.

---

# Output

The Tracking Engine publishes track updates alongside the immutable Observation Batch stream.

It never replaces or destroys the original observations.

This guarantees that fire, smoke and other observations that do not require temporal identity remain available to downstream systems.

Each track update contains:

- update identifier
- camera identifier
- tracking session identifier
- track identifier
- monotonic track revision
- observation type
- track state
- update kind
- transition timestamp
- emission timestamp
- first observed timestamp
- last observed timestamp
- latest observation identifier, when applicable
- latest observed geometry, when applicable
- geometry observation timestamp, when applicable
- coordinate space, when applicable
- observation count
- association confidence, when applicable
- track confidence
- tracker identifier
- tracker version
- tracker configuration version
- reason code, when applicable

Possible update kinds include:

- confirmed
- updated
- lost
- recovered
- ended

Lost and ended updates may carry the last known observation and geometry.

They must never present that geometry as a new observation.

The engine also publishes tracking session updates.

Track updates and tracking session updates are technical interface records, not new business concepts.

Possible session update kinds include:

- session started
- session ended

Session updates contain an update identifier, camera identifier, tracking session identifier, transition timestamp, emission timestamp and reason code.

Consumers must tolerate duplicate delivery.

An update identifier supports deduplication.

A higher track revision supersedes a lower revision for the same track.

A track update or session update is not a business Event.

It is technical state used by downstream components.

---

# Track

A track represents one visible entity during a bounded period of time.

Example

Track 17

- type: Person
- camera: cam-entrance-01
- first observed: 12:01:03
- last observed: 12:01:18
- state: Confirmed
- observations: 24

A track contains temporal identity.

It does not contain personal identity.

The identifier must be treated as opaque.

No consumer may infer meaning from its format.

---

# Identity Scope

Track identity is local to:

- one camera
- one tracking session

The canonical identity of a track is:

camera identifier

+

tracking session identifier

+

track identifier

Two tracks from different cameras are never assumed to represent the same entity.

Example

Camera A

Track 17

Camera B

Track 17

These are unrelated tracks.

Track identifiers must never be reused within the same tracking session.

When a tracking session ends, identity continuity ends with it.

---

# Tracking Session

A tracking session represents one continuous period of tracker operation for one camera.

It is bound to exactly one upstream source stream session.

A new session begins when:

- tracking starts for a camera
- the upstream source stream session changes
- the tracker restarts
- the stream has a discontinuity that invalidates temporal state
- the tracking configuration changes incompatibly

The system must never silently preserve identity across a session boundary.

On a clean shutdown, the engine must publish terminal updates for public tracks before publishing session ended.

After an unclean failure, the next session started update invalidates every track from an older session for that camera.

Downstream components must scope all active state by the canonical track identity and current tracking session.

If continuity is uncertain, a new track must be created.

False continuity is more dangerous than a new identifier.

---

# Track Lifecycle

Every track follows an explicit lifecycle.

The public states are:

- Confirmed
- Lost
- Ended

Tentative is an internal state.

Recovered is an update kind, not a state.

The valid transitions are:

- No track → Tentative: no public update.
- Tentative → Confirmed: publish confirmed.
- Tentative → Discarded: publish nothing and delete internal state.
- Confirmed → Confirmed after association: publish updated.
- Confirmed → Lost after the loss threshold: publish lost.
- Lost → Confirmed after association: publish recovered.
- Lost → Ended after the recovery window: publish ended.
- Confirmed or Lost → Ended on a clean session close: publish ended with a reason code.

Ended is terminal.

---

## Tentative

An unmatched observation creates a tentative track.

Tentative tracks require additional evidence before they become stable public tracks.

This reduces tracks created by brief false detections.

A tentative track that fails the confirmation policy is discarded.

Tentative tracks are never visible to downstream components.

---

## Confirmed

A tentative track becomes confirmed after satisfying the configured confirmation policy.

A confirmed track represents the engine's current best estimate of temporal continuity.

Confirmed does not mean that the entity's real identity is known.

---

## Lost

A confirmed track becomes lost when no compatible observation is received within the configured tolerance.

A lost track may remain recoverable for a short period.

Lost state is not proof that the entity left the scene.

It only means that the tracker cannot currently observe it reliably.

---

## Recovery

A lost track becomes confirmed again when a compatible observation is associated within the recovery window.

The same track identifier is retained.

Recovery must require sufficient evidence.

The engine publishes a recovered update for this transition.

---

## Ended

A lost track becomes ended when its recovery window expires.

Ended tracks are final.

They must never be reopened.

A later observation creates a new track.

---

# Observation Association

Association determines whether an observation belongs to an existing track.

Possible signals include:

- geometric overlap
- distance
- direction of movement
- velocity
- observation type
- time since last observation
- short-lived appearance features

The public contract must not depend on a specific association algorithm.

Possible implementations include:

- ByteTrack
- BoT-SORT
- SORT
- future tracking algorithms

Replacing the algorithm must not change downstream interfaces.

---

# Association Invariants

Within one frame:

- one observation may update at most one track
- one track may receive at most one observation
- observations from different cameras may never be associated
- semantically incompatible observation types may never be associated

Association must use deterministic tie-breaking when multiple candidates have equal scores.

Every accepted association must remain traceable to the observation that produced it.

---

# Observation Types

Tracking state is partitioned by:

- camera identifier
- source stream session identifier
- observation type
- tracker configuration

Examples

- Person
- Vehicle
- Package
- Table

A Person observation must never update a Vehicle track.

Different detectors may produce the same observation type.

For each tracked observation type, configuration must select:

- one authoritative detector stream
- or one upstream fused and deduplicated stream

Raw duplicate outputs from multiple detectors must not enter the same association domain.

The tracker must preserve detector provenance without exposing detector-specific behavior to downstream modules.

Observation types that do not require temporal identity may remain independent observations.

The Tracking Engine must not invent business categories.

---

# Time and Ordering

Tracking depends on ordered Observation Batches.

Observation Batches must be processed independently for each camera and source stream session.

The engine should use:

- monotonic frame sequence for source order
- capture timestamp for trajectory, loss and recovery time
- a local monotonic clock for operational lag and source-silence timeouts

Capture timestamp represents when the source produced the frame.

It must advance monotonically within one source stream session.

Ingestion timestamp represents when Vision received the frame context.

Loss and recovery thresholds advance with completed Observation Batches on the source timeline.

Total source silence is not evidence that an entity disappeared.

If source silence exceeds the health timeout, the tracking session must end instead of inventing a movement conclusion.

Duplicate batches or observations must not update a track twice.

Out-of-order batches may be accepted only within a bounded, explicit reordering window.

The window must be defined by configuration and advanced with a watermark.

Batches arriving behind the watermark must be rejected or recorded as late.

An empty batch advances source order exactly like a non-empty batch.

An ended track must never be changed by late data.

---

# Variable Frame Rate

The Tracking Engine must not assume that every source produces 30 frames per second.

Frames may be:

- sampled
- delayed
- dropped
- received at an irregular rate

Timeouts and recovery windows should use elapsed time whenever possible.

They should not depend only on a fixed number of frames.

This allows the platform to analyze one, two or five frames per second without changing the public contract.

---

# Occlusion

Temporary occlusion is expected.

Examples

- one person walks behind another
- an entity passes behind furniture
- the detector misses a frame
- lighting changes briefly

The tracker may use internal motion estimates to recover a track.

Predicted positions are estimates, not observations.

They must never be presented as directly visible facts.

When a track is lost, the last observed geometry must remain clearly distinguished from any internal prediction.

---

# Geometry and Trajectory

A track retains the latest observed geometry and a bounded trajectory.

The trajectory may contain:

- timestamp
- observation identifier
- position
- size
- direction
- velocity estimate

The Tracking Engine preserves the coordinate space declared by the Observation Batch.

Every public geometry must carry or reference the same coordinate-space metadata.

The Zone Engine may compare geometry with a zone only when their units, orientation and transform version are compatible.

A geometry-affecting source change must create a new source stream session and tracking session.

It never interprets geometry as a business zone.

Trajectory history must be bounded.

The Tracking Engine is not a permanent movement database.

---

# Confidence

The system must distinguish between:

- detector confidence
- association confidence
- track confidence

Detector confidence describes certainty about what was observed.

Association confidence describes certainty that an observation belongs to a track.

Track confidence describes the stability of temporal continuity.

These values must never be combined without preserving their meaning.

Business thresholds do not belong in the Tracking Engine.

---

# Camera Isolation

Each camera must have independent tracking state.

A failure in one camera must never stop tracking for another camera.

Each camera should have independent:

- input queue
- active tracks
- tracking session
- health state
- metrics
- recovery behavior

This isolation allows the same architecture to support one camera or one hundred cameras.

---

# Cross-Camera Tracking

Cross-camera identity is out of scope.

The Tracking Engine never claims that:

> The person in Camera A is the same person in Camera B.

That problem requires a separate, explicit capability with different privacy, accuracy and security requirements.

No downstream component may join tracks across cameras using only visual similarity.

---

# Privacy

The Tracking Engine maintains ephemeral, pseudonymous identifiers.

It does not store:

- names
- customer identities
- employee identities
- face recognition profiles
- biometric identities

If short-lived appearance features are used internally for association, they must:

- remain local
- never be written to application logs
- never be exported through the public interface
- be retained no longer than the active or recoverable track
- be deleted when the track or tracking session ends
- never be exposed as personal identity
- never become part of the public track identifier

---

# Configuration

Tracking configuration may include technical parameters such as:

- confirmation window
- recovery window
- association threshold
- compatible observation types
- maximum bounded trajectory length
- reordering tolerance

Every configuration must have a version.

Configuration changes that invalidate active state must start a new tracking session.

Business rules such as queue limits or restricted areas do not belong here.

---

# Performance

The Tracking Engine should operate in real time.

Performance goals include:

- low association latency
- bounded memory usage
- independent camera processing
- graceful handling of dropped frames
- efficient cleanup of ended tracks

Correct identity continuity is more important than maximizing track count.

Input queues must remain bounded.

Under overload, the engine must apply an explicit whole-batch backpressure or drop policy.

It must never process half of an Observation Batch or allow memory and latency to grow without limit.

---

# Failure Handling

Tracking failures must be isolated by camera.

Examples

- invalid observation
- duplicate frame
- out-of-order input
- tracker timeout
- internal algorithm failure
- worker restart

An invalid observation must not corrupt active tracks.

If temporal state cannot be recovered safely, the current tracking session must end and a new one must begin.

The system must never pretend that identity survived an unknown failure.

If a failure prevents terminal updates, the next session started update invalidates the previous session for that camera.

---

# State and Persistence

Active tracking state is bounded and operational.

It may live in memory or another low-latency local store.

The ended update is the final public summary of a track.

The Tracking Engine is not responsible for storing that summary long term.

Permanent history belongs to downstream storage components.

Video frames must not be retained merely to maintain a track.

---

# Versioning

Every track update must record:

- tracker identifier
- tracker version
- tracker configuration version
- tracking session identifier

The latest observation retains its own detector and model provenance.

Given the same ordered observations, tracker version and configuration, tracking behavior should be reproducible.

This allows auditing, replay and safe algorithm replacement.

---

# Observability

The Tracking Engine should expose metrics for each camera.

Examples

- observations received
- observations rejected
- unmatched observations
- tentative tracks
- confirmed tracks
- lost tracks
- recovered tracks
- ended tracks
- association latency
- duplicate observations
- late observations
- input queue depth
- tracking session restarts

Metrics describe system health.

They never change business behavior.

---

# Public Interface

Input

Observation Batch

↓

Tracking Engine

Outputs

- Original Observation Batch
- Track Updates
- Tracking Session Updates

↓

Zone Engine

The original Observation Batch remains available even when none of its observations are trackable.

Track Updates add temporary identity without replacing visual facts.

Tracking Session Updates let downstream components invalidate stale state after restarts or discontinuities.

Ordering rules

- A session started update precedes every track update in that session.
- Track revisions increase monotonically within one canonical track identity.
- An ended update is the final revision of a track.
- A session ended update follows all clean terminal track updates.
- A new session started update invalidates older sessions for the same camera.

No downstream module should know:

- which tracking algorithm is used
- how motion is predicted
- how association scores are calculated
- whether appearance features are used internally
- how active state is stored

Those implementation details belong exclusively to the Tracking Engine.

---

# Design Principles

## Temporary Identity

A track represents visual continuity, not a real-world identity.

---

## Camera Local

Identity never crosses camera boundaries.

---

## Observation Provenance

Every track update is traceable to immutable observations.

---

## Bounded State

Memory and trajectory history must remain bounded.

---

## Replaceability

Tracking algorithms are replaceable.

The public contract remains stable.

---

## Conservative Continuity

When identity continuity is uncertain, create a new track.

Never invent certainty.

---

# Out of Scope

The Tracking Engine does not:

- detect objects
- classify objects
- identify people
- recognize faces
- maintain biometric profiles
- re-identify entities across cameras
- understand zones
- determine entries or exits from business areas
- calculate queue length
- detect suspicious behavior
- generate events
- evaluate rules
- send alerts
- store permanent history

Its only responsibility is maintaining temporary visual identity over time.
