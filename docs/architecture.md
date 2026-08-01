# Architecture

## Vision

Vision transforma video en información accionable para negocios.

El sistema recibe video de una o más cámaras, detecta eventos relevantes, aplica reglas de negocio y notifica a los usuarios cuando ocurre algo importante.

El objetivo no es almacenar video.

El objetivo es transformar horas de video en decisiones claras.

---

# Principios

## 1. Arquitectura modular

Cada componente tiene una única responsabilidad.

Los módulos pueden evolucionar independientemente.

Por ejemplo, el motor de visión puede cambiar completamente sin modificar el motor de reglas.

---

## 2. Event-Driven

Todo el sistema gira alrededor de eventos.

Nunca alrededor de imágenes.

Nunca alrededor de modelos.

Los modelos producen eventos.

Los eventos producen decisiones.

Ejemplo:

Frame

↓

Persona detectada

↓

Persona entra a zona

↓

Fila supera 6 personas

↓

Enviar notificación

---

## 3. IA desacoplada

Los modelos de IA son intercambiables.

YOLO puede ser reemplazado por cualquier otro detector sin modificar el resto del sistema.

---

## 4. Local First

Siempre que sea posible, el análisis ocurre dentro del negocio.

Esto reduce:

- latencia
- costos
- uso de Internet
- problemas de privacidad

La nube recibe únicamente eventos y metadatos.

---

## 5. Cloud Assisted

La nube es responsable de:

- sincronización
- usuarios
- organizaciones
- notificaciones
- configuración
- dashboards
- reportes

Nunca procesa video continuamente.

---

# Componentes

## Camera Gateway

Responsabilidad

Conectarse a cámaras.

Nunca interpreta imágenes.

Solo entrega video.

Entrada

RTSP
ONVIF
Archivos de video

Salida

Frames.

---

## Vision Engine

Responsabilidad

Transformar imágenes en observaciones.

Ejemplos

Persona

Mesa

Caja

Vehículo

Fuego

Humo

No toma decisiones.

Solo observa.

---

## Tracking Engine

Responsabilidad

Mantener identidad temporal.

Ejemplo

Persona 17 entra.

Camina.

Sale.

---

## Zone Engine

Responsabilidad

Interpretar la geometría del negocio.

Ejemplos

Entrada

Caja

Comedor

Cocina

Bodega

---

## Event Engine

Responsabilidad

Convertir observaciones en eventos.

Ejemplo

Observación

Persona permanece 5 minutos.

↓

Evento

LongStayDetected

---

## Rule Engine

Responsabilidad

Evaluar reglas configuradas por el cliente.

Ejemplo

SI

Más de seis personas

Durante tres minutos

↓

Generar alerta.

---

## Notification Engine

Responsabilidad

Entregar eventos importantes.

Push

Email

SMS

Webhook

---

## Cloud Backend

Responsabilidad

Administrar:

Usuarios

Sucursales

Cámaras

Configuraciones

Eventos

Reportes

No ejecuta visión computacional.

---

## Mobile App

Responsabilidad

Mostrar:

Alertas

Historial

Estado del sistema

Configuración

Dashboard

Nunca analiza video.

---

# Flujo General

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

# Filosofía

Vision no intenta responder:

"¿Qué hay en esta imagen?"

Vision intenta responder:

"¿Qué necesita saber el dueño del negocio en este momento?"