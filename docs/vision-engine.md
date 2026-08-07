# Vision Engine

## Purpose

The Vision Engine transforms raw video frames into structured visual observations.

It is the only component responsible for understanding visual content.

The Vision Engine never makes business decisions.

It never evaluates rules.

It never sends notifications.

It only answers one question:

> What does the camera currently see?

---

# Responsibilities

The Vision Engine is responsible for:

- Analyzing video frames.
- Detecting objects.
- Classifying objects.
- Estimating confidence.
- Producing visual observations.
- Running multiple detectors.
- Publishing observations to downstream systems.

It is not responsible for:

- Tracking identities.
- Understanding business logic.
- Evaluating rules.
- Generating alerts.
- Sending notifications.

---

# Philosophy

The Vision Engine observes.

It never interprets.

Everything produced by this module is factual.

Examples:

✓ Person detected.

✓ Fire detected.

✓ Smoke detected.

✓ Vehicle detected.

Examples that do NOT belong here:

✗ Restaurant is busy.

✗ Queue is too long.

✗ Employee abandoned workstation.

Those require business context and belong to later modules.

---

# Input

The Vision Engine receives frames from the Video Gateway.

Each frame contains:

- image
- timestamp
- camera identifier
- frame identifier

The Vision Engine does not know how the frame was captured.

---

# Output

The output is a collection of observations.

Example

Observation

- type
- confidence
- bounding box
- timestamp
- camera id

Multiple observations may exist in the same frame.

---

# Observation

An observation represents something directly visible.

Examples

Person

Chair

Table

Vehicle

Fire

Smoke

Helmet

Backpack

Phone

Every observation is independent.

Observations do not persist across frames.

Temporal reasoning belongs to the Tracking Engine.

---

# Detector

A detector converts frames into observations.

Examples

Person Detector

↓

Person Observation

Fire Detector

↓

Fire Observation

Smoke Detector

↓

Smoke Observation

Every detector is independent.

Detectors may be added or removed without changing the architecture.

---

# Detector Pipeline

A single frame may pass through multiple detectors.

Frame

↓

Person Detector

↓

Vehicle Detector

↓

Fire Detector

↓

Helmet Detector

↓

Observations

The execution order should not affect the final result.

---

# Confidence

Every observation includes a confidence score.

Confidence expresses the detector's certainty.

Business logic should decide how confidence is used.

The Vision Engine never filters observations based on business rules.

---

# Model Independence

The architecture must never depend on a specific machine learning model.

Possible implementations include:

- YOLO
- RT-DETR
- Grounding DINO
- Future models

Replacing one model with another must not affect downstream components.

Only observations are exposed.

---

# Extensibility

New detectors should be added without modifying existing detectors.

Examples

Today

Person Detector

Tomorrow

Animal Detector

Next year

Flood Detector

The architecture should remain unchanged.

---

# Performance

The Vision Engine should support real-time inference.

Performance goals include:

- Efficient memory usage
- Batched inference when appropriate
- Hardware acceleration when available
- Parallel detector execution when possible

Correctness is more important than maximum throughput.

---

# Failure Handling

A detector may fail.

Other detectors must continue operating.

Failures should be isolated.

A single model failure must never stop the entire Vision Engine.

---

# Versioning

Each detector should expose:

- detector identifier
- detector version
- model version

Every observation should record which detector produced it.

This allows reproducibility and auditing.

---

# Public Interface

Input

Frame

↓

Vision Engine

↓

Observations

No downstream module should know:

- which model produced the observation
- which framework executed the model
- whether inference used CPU or GPU
- which optimization library was used

Those implementation details belong exclusively to the Vision Engine.

---

# Design Principles

## Single Responsibility

Observe the world.

Nothing else.

---

## Deterministic Contract

Same input.

Same observations.

Regardless of internal implementation.

---

## Model Agnostic

Models are replaceable.

Observations are permanent.

---

## Extensible

Adding new visual capabilities should not require redesigning the engine.

---

## Observable

Every detector should expose metrics.

Examples

- inference time
- FPS
- memory usage
- GPU usage
- detector failures

These metrics help operate the system but never affect business logic.

---

# Out of Scope

The Vision Engine does not:

- identify people
- remember identities
- detect long-term behavior
- understand zones
- understand business operations
- detect anomalies
- evaluate rules
- notify users

Its only responsibility is transforming pixels into observations.