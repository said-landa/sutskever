# Architecture

## Vision

Vision transforms video into actionable information for businesses.

The system receives video from one or more cameras, detects relevant events, applies business rules and notifies users when something important happens.

The goal is not to store video.

The goal is to transform hours of video into clear decisions.

---

# Principles

## 1. Modular Architecture

Each component has a single responsibility.

Modules can evolve independently.

For example, the Vision Engine can change completely without modifying the Rule Engine.

---

## 2. Event-Driven

The entire system revolves around events.

Never around images.

Never around models.

Models produce events.

Events produce decisions.

Example:

Frame

↓

Person detected

↓

Person enters zone

↓

Queue exceeds 6 people

↓

Send notification

---

## 3. Decoupled AI

AI models are interchangeable.

YOLO can be replaced by any other detector without modifying the rest of the system.

---

## 4. Local First

Whenever possible, analysis takes place on the business premises.

This reduces:

- latency
- costs
- Internet usage
- privacy concerns

The cloud receives only events and metadata.

---

## 5. Cloud Assisted

The cloud is responsible for:

- synchronization
- users
- organizations
- notifications
- configuration
- dashboards
- reports

It never processes video continuously.

---

# Components

## Camera Gateway

Responsibility

Connect to cameras.

It never interprets images.

It only delivers video.

Input

RTSP
ONVIF
Video files

Output

Frames.

---

## Vision Engine

Responsibility

Transform images into observations.

Examples

Person

Table

Checkout Counter

Vehicle

Fire

Smoke

It does not make decisions.

It only observes.

---

## Tracking Engine

Responsibility

Maintain temporal identity.

Example

Person 17 enters.

Walks.

Leaves.

---

## Zone Engine

Responsibility

Interpret the geometry of the business.

Examples

Entrance

Checkout

Dining Area

Kitchen

Storage Area

---

## Event Engine

Responsibility

Convert observations into events.

Example

Observation

Person remains for 5 minutes.

↓

Event

LongStayDetected

---

## Rule Engine

Responsibility

Evaluate rules configured by the customer.

Example

IF

More than six people

For three minutes

↓

Generate alert.

---

## Notification Engine

Responsibility

Deliver important events.

Push

Email

SMS

Webhook

---

## Cloud Backend

Responsibility

Manage:

Users

Sites

Cameras

Configurations

Events

Reports

It does not run computer vision.

---

## Mobile App

Responsibility

Display:

Alerts

History

System Status

Configuration

Dashboard

It never analyzes video.

---

# General Flow

Camera

↓

Frames

↓

Vision

↓

Tracking

↓

Zones

↓

Events

↓

Rules

↓

Notifications

↓

Mobile

---

# Philosophy

Vision does not try to answer:

"What is in this image?"

Vision tries to answer:

"What does the business owner need to know right now?"
