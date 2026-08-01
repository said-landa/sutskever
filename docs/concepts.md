# Concepts

Este documento define el vocabulario oficial de Vision.

Todos los documentos y el código deben utilizar exactamente estos conceptos.

---

# Organization

Una organización representa una empresa.

Ejemplos

- Restaurante
- Tienda
- Farmacia
- Hotel

Una organización puede tener múltiples sucursales.

---

# Site

Un sitio representa una ubicación física.

Ejemplos

- Restaurante Centro
- Restaurante Norte

Cada sitio contiene cámaras, zonas y usuarios.

---

# Camera

Una cámara representa una fuente de video.

Una cámara nunca interpreta imágenes.

Únicamente produce video.

Entradas

- RTSP
- ONVIF
- Archivo de video

Salida

- Frames

---

# Frame

Un frame representa una única imagen capturada por una cámara.

Los frames son efímeros.

Nunca representan conocimiento.

---

# Observation

Una observación representa algo visto por el sistema.

Ejemplos

- Persona detectada.
- Mesa detectada.
- Vehículo detectado.
- Humo detectado.

Las observaciones únicamente describen el mundo.

Nunca toman decisiones.

Ejemplo

```
Observation

Person #17

confidence = 0.94

position = (x, y)
```

---

# Track

Un track representa una entidad observada durante un periodo de tiempo.

Ejemplo

Persona 17

entra

camina

sale

Un track mantiene identidad temporal.

---

# Zone

Una zona representa una región definida por el negocio.

Ejemplos

- Entrada
- Caja
- Cocina
- Comedor
- Bodega

Las zonas son independientes de la IA.

---

# Detector

Un detector transforma frames en observaciones.

Ejemplos

Person Detector

↓

Persona

Fire Detector

↓

Fuego

Smoke Detector

↓

Humo

Todos los detectores producen observaciones.

Nunca generan alertas.

---

# Event

Un evento representa un hecho importante ocurrido en el negocio.

Ejemplos

- Persona entra a cocina.
- Persona sale del edificio.
- Cola supera cinco personas.
- Mesa permanece ocupada durante veinte minutos.

Los eventos se construyen a partir de observaciones.

---

# Rule

Una regla representa una condición definida por el cliente.

Ejemplo

SI

Más de seis personas

en

Fila

durante

Tres minutos

ENTONCES

Enviar notificación.

Las reglas transforman eventos en acciones.

---

# Action

Una acción representa una respuesta del sistema.

Ejemplos

- Enviar notificación.
- Enviar correo.
- Ejecutar webhook.
- Registrar incidente.
- Crear reporte.

---

# Alert

Una alerta representa una acción visible para un usuario.

Ejemplos

"Hay nueve personas esperando."

"La cocina lleva diez minutos saturada."

---

# Incident

Un incidente representa una colección de eventos relacionados.

Ejemplo

Evento

Fila larga

↓

Caja sin operador

↓

Clientes abandonan fila

↓

Incidente

Problema de atención.

---

# Detector Package

Un paquete de detectores representa un conjunto de detectores orientados a un problema.

Ejemplo

Restaurant Package

Incluye

- Personas
- Filas
- Mesas
- Cocina

---

# Business Template

Una plantilla representa una configuración predeterminada para un tipo de negocio.

Ejemplos

Restaurante

Farmacia

Supermercado

Hotel

Cada plantilla incluye

- zonas
- detectores
- reglas
- dashboard

---

# Timeline

La línea de tiempo representa la secuencia cronológica de eventos.

Nunca almacena video.

Únicamente hechos.

Ejemplo

12:01

Cliente entra.

12:03

Cliente hace fila.

12:08

Fila supera seis personas.

12:09

Se envía alerta.

---

# Dashboard

El dashboard representa el estado actual del negocio.

Se construye únicamente utilizando eventos.

Nunca analiza video directamente.

---

# Vision Engine

El motor de visión transforma imágenes en observaciones.

No conoce reglas.

No conoce negocios.

No conoce usuarios.

Solo observa.

---

# Capability
Una capacidad es algo que el sistema sabe hacer, independientemente del tipo de negocio.

Ejemplos:

Detectar personas.
Contar personas.
Seguir personas.
Detectar fuego.
Medir permanencia.
Detectar cruce de línea.

Las capacidades son reutilizables. Una misma capacidad puede servir para un restaurante, un banco o una fábrica.

---

# Solution
Una solución es un producto construido para un sector específico.

Por ejemplo:

Restaurant Operations

Utiliza las capacidades:

Detectar personas.
Contar personas.
Medir permanencia.
Detectar zonas.
Motor de reglas.

Mientras que:

Factory Safety

Utiliza:

Detectar personas.
Detectar cascos.
Detectar chalecos.
Detectar zonas peligrosas.

---

# Rule Engine

El motor de reglas transforma eventos en acciones.

No analiza imágenes.

---

# Notification Engine

El motor de notificaciones entrega acciones a los usuarios.

No interpreta eventos.

---

# Core Philosophy

Vision nunca intenta responder:

"¿Qué hay en esta imagen?"

Vision intenta responder:

"¿Qué necesita saber el negocio en este momento?"

Toda la arquitectura debe respetar esta filosofía.