# Concepts

This document defines the official vocabulary of Vision.

All documents and code must use these exact concepts.

---

# Organization

An organization represents a company.

Examples

- Restaurant
- Store
- Pharmacy
- Hotel

An organization may have multiple sites.

---

# Site

A site represents a physical location.

Examples

- Downtown Restaurant
- North Restaurant

Each site contains cameras, zones and users.

---

# Camera

A camera represents a video source.

A camera never interprets images.

It only produces video.

Inputs

- RTSP
- ONVIF
- Video file

Output

- Frames

---

# Frame

A frame represents a single image captured by a camera.

Frames are ephemeral.

They never represent knowledge.

---

# Observation

An observation represents something seen by the system.

Examples

- Person detected.
- Table detected.
- Vehicle detected.
- Smoke detected.

Observations only describe the world.

They never make decisions.

Example

```
Observation

Person #17

confidence = 0.94

position = (x, y)
```

---

# Track

A track represents an entity observed over a period of time.

Example

Person 17

enters

walks

leaves

A track maintains temporal identity.

---

# Zone

A zone represents a region defined by the business.

Examples

- Entrance
- Checkout
- Kitchen
- Dining Area
- Storage Area

Zones are independent of AI.

---

# Detector

A detector transforms frames into observations.

Examples

Person Detector

↓

Person

Fire Detector

↓

Fire

Smoke Detector

↓

Smoke

All detectors produce observations.

They never generate alerts.

---

# Event

An event represents an important fact that occurred in the business.

Examples

- Person enters the kitchen.
- Person leaves the building.
- Queue exceeds five people.
- Table remains occupied for twenty minutes.

Events are built from observations.

---

# Rule

A rule represents a condition defined by the customer.

Example

IF

More than six people

in

Queue

for

Three minutes

THEN

Send notification.

Rules transform events into actions.

---

# Action

An action represents a system response.

Examples

- Send notification.
- Send email.
- Execute webhook.
- Record incident.
- Create report.

---

# Alert

An alert represents an action visible to a user.

Examples

"Nine people are waiting."

"The kitchen has been overloaded for ten minutes."

---

# Incident

An incident represents a collection of related events.

Example

Event

Long queue

↓

Checkout without an operator

↓

Customers abandon the queue

↓

Incident

Customer service problem.

---

# Detector Package

A detector package represents a set of detectors focused on a specific problem.

Example

Restaurant Package

Includes

- People
- Queues
- Tables
- Kitchen

---

# Business Template

A business template represents a default configuration for a type of business.

Examples

Restaurant

Pharmacy

Supermarket

Hotel

Each template includes

- zones
- detectors
- rules
- dashboard

---

# Timeline

The timeline represents the chronological sequence of events.

It never stores video.

It stores facts only.

Example

12:01

Customer enters.

12:03

Customer joins the queue.

12:08

Queue exceeds six people.

12:09

Alert is sent.

---

# Dashboard

The dashboard represents the current state of the business.

It is built exclusively from events.

It never analyzes video directly.

---

# Vision Engine

The Vision Engine transforms images into observations.

It does not know about rules.

It does not know about businesses.

It does not know about users.

It only observes.

---

# Capability

A capability is something the system knows how to do, independently of the type of business.

Examples:

Detect people.
Count people.
Track people.
Detect fire.
Measure dwell time.
Detect line crossing.

Capabilities are reusable. The same capability may serve a restaurant, a bank or a factory.

---

# Solution

A solution is a product built for a specific industry.

For example:

Restaurant Operations

Uses the following capabilities:

Detect people.
Count people.
Measure dwell time.
Detect zones.
Rule Engine.

Whereas:

Factory Safety

Uses:

Detect people.
Detect helmets.
Detect safety vests.
Detect dangerous zones.

---

# Rule Engine

The Rule Engine transforms events into actions.

It does not analyze images.

---

# Notification Engine

The Notification Engine delivers actions to users.

It does not interpret events.

---

# Core Philosophy

Vision never tries to answer:

"What is in this image?"

Vision tries to answer:

"What does the business need to know right now?"

The entire architecture must follow this philosophy.
