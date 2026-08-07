# Video Gateway

## Purpose

The Video Gateway is responsible for connecting Vision to video sources.

It abstracts every camera manufacturer, protocol and transport mechanism into a single, consistent interface for the rest of the platform.

The Video Gateway never performs computer vision.

It never detects objects.

It never evaluates business rules.

Its only responsibility is to produce a continuous stream of frames.

---

# Responsibilities

The Video Gateway is responsible for:

- Discovering cameras.
- Connecting to cameras.
- Authenticating with cameras.
- Receiving video streams.
- Decoding video.
- Delivering frames to the Vision Engine.
- Detecting connection failures.
- Reconnecting automatically.
- Reporting camera health.

It is not responsible for:

- Object detection.
- Tracking.
- Event generation.
- Business logic.
- Notifications.

---

# Supported Sources

The gateway should support multiple video sources.

Examples:

- RTSP streams
- ONVIF cameras
- Video files
- USB webcams
- IP cameras
- Future protocols

Every source must expose exactly the same interface to the rest of the system.

---

# Camera

A camera represents a video source.

A camera contains metadata only.

Example

- Camera ID
- Name
- Organization
- Site
- Location
- Manufacturer
- Model
- Resolution
- FPS
- Status

A camera never stores business logic.

---

# Stream

A stream is a continuous sequence of frames.

A stream may start.

A stream may stop.

A stream may reconnect.

The Vision Engine should not care how the stream is produced.

---

# Frame

A frame is the output of the Video Gateway.

A frame contains:

- image
- timestamp
- camera id
- frame number

Frames are immutable.

Frames are never modified.

---

# Camera States

Every camera should expose its current state.

Possible states:

- Offline
- Connecting
- Connected
- Streaming
- Reconnecting
- Error

---

# Health Monitoring

The gateway continuously monitors every camera.

Examples

- Last frame received
- FPS
- Bitrate
- Resolution
- Packet loss
- Connection latency

If a camera stops sending frames the gateway should automatically reconnect.

---

# Failure Recovery

Temporary failures are expected.

Examples

- Camera reboot
- Network interruption
- Router restart
- Power outage

The gateway should retry automatically.

No manual intervention should be required.

---

# Multiple Cameras

The gateway must support multiple cameras simultaneously.

Each camera operates independently.

A failure in one camera must never stop another camera.

---

# Performance

The gateway should avoid unnecessary processing.

Responsibilities include:

- Efficient decoding
- Frame buffering
- Backpressure handling
- Memory reuse when possible

Computer vision must never occur here.

---

# Security

Credentials must never be hardcoded.

Passwords should be encrypted at rest.

Communication should use secure protocols whenever possible.

Access should follow least privilege.

---

# Public Interface

The rest of the system should only interact with the gateway through a simple interface.

Example

Camera

↓

Frame Stream

↓

Vision Engine

No other module should know:

- RTSP
- ONVIF
- FFmpeg
- OpenCV
- Camera manufacturer

Those implementation details belong exclusively to the Video Gateway.

---

# Design Principles

## Abstraction

The rest of the system must never know how frames are obtained.

---

## Reliability

Temporary failures should recover automatically.

---

## Scalability

Adding a second camera should not require changing the architecture.

Adding one hundred cameras should use the same abstractions.

---

## Replaceability

The internal implementation may change completely without affecting other modules.

RTSP may be replaced.

FFmpeg may be replaced.

OpenCV may be replaced.

The external contract must remain stable.

---

# Out of Scope

The Camera Gateway does not:

- detect people
- detect fire
- detect smoke
- detect weapons
- classify objects
- generate events
- evaluate rules
- send notifications

Its only purpose is to deliver frames reliably.