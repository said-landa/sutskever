# Zone Engine

## Purpose

The Zone Engine transforms visual geometry into explicit spatial context.

It answers one question:

> Where is this observed or tracked entity relative to the business-defined zones for this camera?

The Zone Engine evaluates observations and tracks against versioned zone geometry.

It never detects objects.

It never identifies people.

It never generates business Events.

---

# Responsibilities

The Zone Engine is responsible for:

- Loading and validating zone configuration snapshots.
- Comparing compatible visual geometry with configured zones.
- Assessing stateless observations that do not require tracking.
- Maintaining zone membership for confirmed tracks.
- Producing technical enter, exit, unavailable and resumed transitions.
- Handling overlapping zones deterministically.
- Handling track loss, recovery and termination without inventing movement.
- Handling zone configuration changes without creating false transitions.
- Preserving source, geometry and configuration provenance.
- Reporting zone-processing health and performance.

It is not responsible for:

- Capturing or decoding video.
- Detecting or classifying objects.
- Associating observations across frames.
- Identifying people.
- Joining entities across cameras.
- Counting queues or measuring business performance.
- Deciding whether a spatial fact is important.
- Generating business Events.
- Evaluating customer rules.
- Sending notifications.
- Storing permanent history.

---

# Philosophy

Zones are business-defined geometry.

They are not produced by an AI model.

Example

The business defines:

Checkout Zone

The Vision Engine observes:

Person

The Tracking Engine maintains:

Track 17

The Zone Engine determines:

Track 17 is inside Checkout Zone

The Event Engine may later interpret that spatial fact.

The Rule Engine may later decide whether an action is required.

The Zone Engine only describes spatial relationships.

---

# Zone

A zone is a named region defined by the business for one camera view.

Examples

- Entrance
- Checkout
- Kitchen
- Dining Area
- Storage Area

A zone does not contain:

- detector logic
- tracking logic
- event logic
- rule thresholds
- notification settings

A zone only defines where a spatial relationship may be evaluated.

---

# Camera Scope

Every zone is scoped to exactly one:

- organization
- site
- camera

The initial Zone Engine operates in camera image space.

Two zones attached to different cameras are unrelated even if they represent the same physical room.

The Zone Engine never merges memberships across cameras.

Cross-camera or world-coordinate zones require a separate calibrated capability and are out of scope.

---

# Zone Configuration Snapshot

The Zone Engine receives immutable, versioned Zone Configuration Snapshots.

Each snapshot contains:

- snapshot identifier
- organization identifier
- site identifier
- camera identifier
- source geometry version
- effective timestamp
- effective source stream session identifier, when known
- effective frame sequence or activation boundary
- subject routing configuration
- zero or more zone definitions

Each zone definition contains:

- zone identifier
- zone activation identifier
- zone version
- name
- enabled state
- geometry
- coordinate space
- applicable subject types and their membership policy references

The snapshot identifier represents the complete active zone configuration for one camera.

Partial configuration updates are never applied.

The zone identifier remains stable across edits.

The zone activation identifier remains stable while a zone stays enabled and changes whenever a removed or disabled zone is activated again.

---

# Zone Geometry

The initial geometry type is a closed polygon.

A valid polygon must:

- contain at least three distinct vertices
- contain only finite coordinates
- use one declared coordinate space
- remain inside the declared frame bounds when required by that coordinate space
- not self-intersect
- not collapse to zero area
- define boundary behavior explicitly

If polygon holes or multiple polygons are supported later, their semantics must be explicit and versioned.

The engine must reject invalid geometry.

It must never silently repair, reorder or reinterpret invalid vertices.

The last valid snapshot remains active when a replacement snapshot is rejected.

---

# Coordinate Space

Zone geometry and source geometry may be compared only when their coordinate metadata is compatible.

Coordinate metadata includes:

- units, such as normalized or pixel coordinates
- frame width and height
- orientation
- transform or calibration version
- source geometry version

The Zone Engine must never guess a coordinate conversion.

When coordinate metadata is incompatible:

- the geometry is not evaluated
- existing tracked membership becomes unavailable
- the mismatch is reported
- no outside, exit or entry conclusion is produced

A geometry-affecting camera change requires a new source geometry version and a compatible Zone Configuration Snapshot.

---

# Membership Policy

A Membership Policy defines how subject geometry is compared with a zone polygon.

Possible policies include:

- anchor point inside polygon
- bounding-box overlap ratio
- segmentation-mask overlap ratio
- keypoint inside polygon

Possible anchor points include:

- center
- bottom center
- a named keypoint

Every applicable observation type must use an explicit policy.

The engine must never choose a policy by guessing from the object label.

Each policy defines:

- geometry method
- enter threshold
- exit threshold
- boundary inclusion rule
- numerical tolerance
- stabilization policy
- update emission policy

Membership policy is technical configuration.

It must not contain business conditions such as:

- more than six people
- longer than three minutes
- after closing time
- restricted to employees

Those conditions belong to later modules.

---

# Boundary Semantics

Points and geometry exactly on a zone boundary must have deterministic behavior.

The default policy treats the polygon boundary as inside.

Numerical tolerance must be:

- explicit
- versioned
- consistent across replay

The same geometry, zone version and policy version must produce the same spatial result.

---

# Input

The Zone Engine receives four logical inputs.

## Observation Batches

The original immutable Observation Batch stream from the Vision Engine.

Each batch preserves:

- camera identifier
- source stream session identifier
- frame identifier or monotonic frame sequence
- capture timestamp
- ingestion timestamp
- coordinate space
- zero or more observations

Empty batches are valid and advance the source timeline.

## Track Updates

Track Updates from the Tracking Engine.

Each update preserves:

- update identifier
- canonical track identity
- monotonic track revision
- observation type
- track state
- update kind
- source observation identifier, when applicable
- latest observed geometry, when applicable
- geometry observation timestamp, when applicable
- coordinate space, when applicable
- tracking session identifier
- tracker provenance

## Tracking Session Updates

Tracking Session Updates communicate:

- session started
- session ended
- clean or unclean invalidation reasons

They prevent stale track memberships from surviving a tracking restart or stream discontinuity.

## Zone Configuration Snapshots

Zone Configuration Snapshots define the complete versioned geometry and membership policy for one camera.

All inputs are immutable.

The Zone Engine produces new spatial records instead of modifying its inputs.

---

# Contract Dependencies

Before implementation, upstream contracts must provide enough immutable context to bind every spatial record safely.

In particular:

- Tracking Session Updates must expose the source stream session identifier or an equivalent immutable binding.
- Track Updates must preserve compatible coordinate-space and source-geometry metadata whenever geometry is present.
- Zone configuration delivery must provide an authoritative organization, site and camera binding.

The Zone Engine must reject spatial evaluation when it cannot prove the camera, stream, geometry and tenant scope.

It must never synthesize missing provenance.

---

# Subject Routing

Each observation type uses exactly one subject mode.

Possible modes are:

- observation
- track

## Observation Mode

Observation mode is used for facts that do not require temporal identity.

Examples

- Fire
- Smoke
- a visible object state

Each observation is evaluated independently.

No persistent membership is created.

## Track Mode

Track mode is used for entities whose spatial continuity matters.

Examples

- Person
- Vehicle
- Package

Only confirmed public tracks create persistent membership.

The original observation is not independently emitted as a second spatial subject.

This prevents one entity from being counted twice downstream.

## Routing Invariant

For one observation identifier, the Zone Engine produces spatial context through at most one subject mode.

If routing configuration is missing or ambiguous, the input is rejected for spatial evaluation.

The engine must never guess whether an observation should be treated as stateless or tracked.

---

# Output

The Zone Engine publishes spatial records alongside the original source streams.

It never replaces or destroys:

- Observation Batches
- Track Updates
- Tracking Session Updates

The public outputs are:

- Observation Zone Assessments
- Track Zone Membership Updates
- Zone Processing Session Updates

These outputs are technical interface records.

They are not business Events.

---

# Observation Zone Assessment

An Observation Zone Assessment describes the spatial relationship between one stateless observation and the applicable zones for one frame.

Each assessment contains:

- assessment identifier
- organization identifier
- site identifier
- camera identifier
- source stream session identifier
- frame identifier or sequence
- capture timestamp
- observation identifier
- observation type
- source detector and model provenance
- Zone Configuration Snapshot identifier
- Zone Engine identifier and version
- evaluation status
- zero or more zone relations
- emission timestamp

Each zone relation contains:

- zone identifier
- zone activation identifier
- zone version
- relation
- spatial score
- coordinate-space reference
- membership policy identifier and version

Possible relations include:

- inside
- outside
- intersects

Possible evaluation statuses include:

- complete
- incompatible geometry
- invalid input
- no applicable zones

An assessment is immutable and frame-scoped.

It never creates persistent identity.

---

# Track Zone Membership Update

A Track Zone Membership Update describes the current relationship between one confirmed track and one zone.

Each update contains:

- update identifier
- Zone Processing Session identifier
- organization identifier
- site identifier
- camera identifier
- tracking session identifier
- track identifier
- source track update identifier
- source track revision
- observation type
- zone identifier
- zone activation identifier
- zone version
- Zone Configuration Snapshot identifier
- monotonic membership revision
- membership state
- update kind
- effective timestamp
- emission timestamp
- source observation identifier, when applicable
- geometry observation timestamp, when applicable
- spatial score, when applicable
- coordinate-space reference, when applicable
- membership policy identifier and version
- reason code, when applicable
- Zone Engine identifier and version

The canonical identity of a tracked zone membership is:

Zone Processing Session identifier

+

organization identifier

+

site identifier

+

camera identifier

+

tracking session identifier

+

track identifier

+

zone identifier

+

zone activation identifier

---

# Membership States

The public membership states are:

- Inside
- Outside
- Unavailable
- Ended

Inside means compatible observed geometry satisfies the enter or retain policy.

Outside means compatible observed geometry satisfies the exit or remain-outside policy.

Unavailable means the current spatial relationship cannot be determined safely.

Ended is terminal for the canonical membership identity.

Unavailable is never equivalent to Outside.

Ended is never equivalent to Exited.

---

# Membership Lifecycle

The valid public transitions are:

- No membership → Inside or Outside: publish initialized.
- No membership → Unavailable when spatial evaluation cannot be completed safely: publish unavailable with a reason code.
- Outside → Inside: publish entered after the enter policy is satisfied.
- Inside → Outside: publish exited after the exit policy is satisfied.
- Inside → Inside: publish updated only when materially required.
- Outside → Outside: publish updated only when materially required.
- Inside or Outside → Unavailable: publish unavailable.
- Unavailable → Inside or Outside: publish resumed.
- Inside, Outside or Unavailable → Inside or Outside after a retained-zone configuration change: publish recomputed.
- Any nonterminal state → Ended: publish ended with a reason code.

Initialized is not an entry.

Resumed is not an entry or exit.

Recomputed is not an entry or exit.

Ended is not an exit.

Only observed compatible geometry may produce entered or exited.

Criteria for an updated record must be explicit and versioned in the update emission policy.

---

# Track Loss and Recovery

A lost track does not prove that an entity left a zone.

When the Tracking Engine publishes lost:

- current memberships become Unavailable
- the last known geometry remains historical provenance only
- no exited update is produced

When the same track is recovered:

- current compatible geometry is evaluated
- membership becomes Inside or Outside
- the update kind is resumed
- no entered or exited conclusion is inferred across the invisible gap

A later observed transition may produce entered or exited normally.

---

# Track and Session Termination

When a track ends, every nonterminal membership for that track becomes Ended.

The reason code must preserve the upstream cause when available.

Examples

- track ended
- tracking session ended
- source stream changed
- zone disabled
- zone removed
- Zone Engine state reset

Termination never proves that the entity physically exited a zone.

On a new Tracking Session for a camera, memberships from older Tracking Sessions are invalidated.

---

# Zone Processing Session

A Zone Processing Session represents one continuous period of valid Zone Engine state for one camera.

A new session begins when:

- zone processing starts
- the Zone Engine restarts without restoring exact state
- the source geometry version changes
- an incompatible Zone Engine configuration changes
- state corruption or replay gaps prevent safe continuity

Possible Zone Processing Session update kinds include:

- session started
- state reset
- session ended

Every session update contains:

- update identifier
- Zone Processing Session identifier
- organization identifier
- site identifier
- camera identifier
- transition timestamp
- emission timestamp
- active Zone Configuration Snapshot identifier
- reason code
- Zone Engine identifier and version

A new session started or state reset update invalidates memberships from older Zone Processing Sessions for that camera.

On a clean shutdown, terminal membership updates precede session ended.

After an unclean failure, the next session started update invalidates stale downstream state.

---

# Hysteresis and Stability

Minor detector movement near a boundary must not create rapid enter and exit oscillation.

Membership Policies may use technical stabilization such as:

- different enter and exit overlap thresholds
- an inner and outer boundary tolerance
- a bounded confirmation interval on source time
- a bounded number of ordered observations

Stabilization must be:

- deterministic
- bounded
- versioned
- independent of business meaning

Technical stabilization must not become a hidden customer rule.

The Event Engine and Rule Engine remain responsible for business duration and threshold logic.

---

# Overlapping Zones

Zones may overlap.

One subject may be Inside multiple zones at the same time.

The Zone Engine evaluates each applicable zone independently.

It does not assume:

- exclusivity
- priority
- hierarchy
- containment semantics

If a future product requires mutually exclusive or hierarchical zones, that behavior must be explicit, versioned configuration.

It must never be inferred from zone names or polygon size.

---

# Zone Configuration Changes

Zone configuration changes are applied atomically at a source-frame boundary.

Every input is evaluated against exactly one Zone Configuration Snapshot.

Geometry is evaluated against the snapshot active for the source observation's frame boundary, never merely the snapshot current at processing arrival time.

Timer-only or control updates without new observed geometry do not trigger geometric reevaluation.

When a snapshot changes:

- in-flight inputs finish with the previous snapshot
- a retained zone identifier preserves its membership identity
- a retained enabled zone preserves its zone activation identifier
- applicable confirmed tracks are reevaluated against retained zone identifiers
- retained-zone results are published as recomputed with a higher membership revision
- newly added zone identifiers are published as initialized for applicable confirmed tracks
- newly enabled or recreated zones receive a new zone activation identifier
- removed or disabled zone identifiers become Ended with an explicit reason
- subject mode changes end affected tracked memberships with reason routing changed
- membership policy changes are applied through recomputed updates

A configuration change must never produce entered or exited.

Changing a polygon does not mean that an entity moved.

Disabling or deleting a zone ends its memberships with an explicit reason.

Changing only a zone name preserves geometric membership and publishes metadata only when required, while the new zone version remains traceable.

---

# Ordering and Idempotency

Observation Zone Assessments are ordered by:

- camera identifier
- source stream session identifier
- monotonic frame sequence

Track Zone Membership Updates are ordered by:

- canonical membership identity
- monotonic membership revision

Consumers must tolerate duplicate delivery.

An update or assessment identifier supports deduplication.

A higher membership revision supersedes a lower revision for the same canonical membership identity.

Late Track Updates must never modify an Ended membership.

Track revisions lower than or equal to the last processed revision are ignored or recorded as duplicates.

Configuration snapshots older than the active effective version are rejected or recorded as stale.

Previously accepted historical snapshots remain available for the bounded reorder and replay window so late in-window source records use the snapshot active at their frame boundary.

---

# Time Semantics

The Zone Engine uses source capture time for spatial transitions and technical stabilization.

It uses emission time only for operational observability.

It never uses processing delay as evidence that an entity remained in a zone.

Empty Observation Batches advance the source timeline but do not create spatial conclusions.

Source silence does not imply exit.

Tracking Session and Zone Processing Session updates control invalidation during silence, restart or discontinuity.

Business dwell time belongs to the Event Engine or Rule Engine.

---

# State and Persistence

Tracked membership state is bounded and operational.

The engine stores at most the state required for:

- active confirmed tracks
- applicable zones
- technical stabilization
- idempotency and ordering windows

Observation Zone Assessments are stateless.

The Zone Engine is not a permanent spatial-history database.

Permanent history belongs to downstream storage components.

Video frames must never be retained merely to evaluate zone membership.

On restart, the engine must either:

- restore exact versioned state from a valid checkpoint and replay
- or start a new Zone Processing Session and invalidate stale state

It must never silently continue from incomplete state.

---

# Camera and Tenant Isolation

Each camera has independent:

- Zone Configuration Snapshot
- Zone Processing Session
- input queue
- membership state
- ordering state
- health state
- metrics

A failure in one camera must never stop another camera.

Zone configuration from one organization or site must never be applied to another.

All scope identifiers must match before geometry is evaluated.

---

# Privacy

Zone membership is pseudonymous operational metadata.

It may reveal movement patterns and must be handled accordingly.

The Zone Engine does not store:

- names
- customer identities
- employee identities
- face profiles
- biometric identities

Active membership state must be retained only as long as operationally required.

The engine must not export additional appearance information or video.

---

# Performance

The Zone Engine should operate in real time.

Performance goals include:

- low spatial-evaluation latency
- bounded memory usage
- independent camera processing
- spatial indexing for large zone sets
- evaluation only against applicable zones
- efficient cleanup of ended memberships

Input queues must remain bounded.

Under overload, the engine must use an explicit whole-record backpressure or drop policy.

It must never partially evaluate an Observation Batch or Track Update.

Correct spatial state is more important than maximizing throughput.

---

# Failure Handling

Possible failures include:

- invalid zone geometry
- incompatible coordinate space
- missing configuration snapshot
- stale configuration
- unknown camera or tenant scope
- duplicate input
- out-of-order Track Update
- missing source observation
- Zone Engine restart
- state corruption

Invalid input must not corrupt existing membership state.

The engine must fail closed for incompatible geometry.

It must never convert an evaluation failure into Outside or Exited.

If exact continuity cannot be recovered, a new Zone Processing Session must begin and stale state must be invalidated.

---

# Versioning

Every spatial output records or references:

- Zone Engine identifier
- Zone Engine version
- Zone Configuration Snapshot identifier
- zone identifier, activation identifier and version
- membership policy identifier and version
- coordinate-space and source geometry versions
- source Observation or Track Update provenance
- Zone Processing Session identifier, when stateful

Given the same ordered inputs, geometry, configuration and engine version, output should be reproducible.

This allows auditing, replay and safe geometry-engine replacement.

---

# Observability

The Zone Engine exposes metrics for each camera.

Examples

- Observation Batches received
- observations assessed
- Track Updates received
- active memberships
- initialized memberships
- entered transitions
- exited transitions
- unavailable transitions
- resumed transitions
- ended memberships
- coordinate mismatches
- invalid zone definitions
- rejected configuration snapshots
- stale and duplicate inputs
- spatial-evaluation latency
- input queue depth
- Zone Processing Session resets

Metrics describe technical health.

They never change spatial or business behavior.

---

# Public Interface

Inputs

- Observation Batches
- Track Updates
- Tracking Session Updates
- Zone Configuration Snapshots

↓

Zone Engine

Outputs

- Original source streams remain available
- Observation Zone Assessments
- Track Zone Membership Updates
- Zone Processing Session Updates

↓

Event Engine

The Event Engine receives spatial context with complete source and configuration provenance.

No downstream module should need to know:

- which geometry library is used
- how polygons are indexed
- how numerical tolerance is implemented
- how active membership state is stored
- how technical stabilization is calculated internally

Those implementation details belong exclusively to the Zone Engine.

---

# Design Principles

## Business-Defined Geometry

Zones come from explicit configuration, never from inferred business meaning.

---

## Immutable Inputs

The engine adds spatial context without modifying observations or tracks.

---

## Explicit Uncertainty

Unknown or incompatible geometry becomes Unavailable, never Outside.

---

## No Invented Movement

Loss, restart, termination and configuration changes never imply entry or exit.

---

## Deterministic Evaluation

The same versioned geometry and input produce the same spatial result.

---

## Camera Local

Membership never crosses camera boundaries.

---

## Replaceability

Geometry implementations may change while the public contract remains stable.

---

# Out of Scope

The Zone Engine does not:

- detect or classify objects
- create or associate tracks
- identify people
- recognize faces
- merge entities across cameras
- calibrate cameras into world coordinates
- infer zones from video
- draw zones in a user interface
- count queues or occupancy for business use
- measure business dwell time
- determine whether a zone is restricted
- decide whether behavior is suspicious
- generate business Events
- evaluate customer rules
- send alerts or notifications
- store permanent history

Its only responsibility is transforming compatible geometry into explicit, versioned spatial context.
