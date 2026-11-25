# AeroSense Telemetry Unit (ATU-400) — Technical User Manual (EN)

## 1. System Overview & Certification

The **AeroSense Telemetry Unit (`ATU-400`)** is a ruggedized airborne and industrial data-acquisition module engineered for continuous structural and avionics health monitoring.

- **Certification Standard:** `DO-160G / IP67`
- **Total Unit Mass:** `1.45 kg`
- **Base API Endpoint:** `https://api.aerosense.internal/v2`

## 2. Electrical & Power Specifications

The `ATU-400` operates from a regulated DC avionics bus. Always verify line regulation prior to energizing the harness.

| Parameter | Nominal / Limit | Unit | Notes |
| :--- | :--- | :--- | :--- |
| Nominal Supply Voltage | `24.0` | `V DC` | Regulated bus input |
| Maximum Continuous Current | `2.5` | `A` | Over-current fused |
| Power Stage Efficiency | `0.92` | `ratio` | Synchronous buck converter |

Nominal supply voltage is fixed at $V_{\text{nom}} = 24.0\,\text{V}$ with maximum continuous draw $I_{\text{max}} = 2.5\,\text{A}$ and stage efficiency $\eta = 0.92$:

$$P_{\text{max}} = V_{\text{nom}} \times I_{\text{max}} \times \eta$$

## 3. Environmental & Thermal Limits

All flight-qualified units must remain within the certified ambient operating envelope during mission profiles.

| Parameter | Value | Unit | Condition |
| :--- | :--- | :--- | :--- |
| Minimum Operating Temperature | `-40.0` | `°C` | Cold-soak startup verified |
| Maximum Operating Temperature | `70.0` | `°C` | Continuous full-load rating |

The certified thermal envelope satisfies $-40.0\,^{\circ}\text{C} \le T_{\text{op}} \le 70.0\,^{\circ}\text{C}$ under natural convection.

## 4. Telemetry Acquisition & Filtering

Each analog channel is digitized synchronously before packet framing and transmission over the telemetry uplink.

| Parameter | Value | Unit | Notes |
| :--- | :--- | :--- | :--- |
| Sampling Rate | `500` | `Hz` | Synchronous multi-channel capture |
| ADC Resolution | `16` | `bits` | Differential SAR converter |

At the configured sampling frequency $f_s = 500\,\text{Hz}$, the theoretical anti-aliasing Nyquist cutoff is governed by:

$$f_{\text{Nyquist}} = \frac{f_s}{2}$$

## 5. REST API Reference

Telemetry frames are ingested over HTTPS using the `POST /v2/telemetry/ingest` endpoint.

- **Endpoint:** `POST /v2/telemetry/ingest`
- **Rate Limit:** `120 req/min` per authenticated device token

### Query Parameters

| Parameter | Type | Required | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `device_id` | `string` | `true` | `—` | `any serial` | Unique ATU hardware serial identifier |

## 6. Mechanical Mounting & Maintenance

Mount the chassis onto a flat structural bulkhead using four M5 stainless steel fasteners.

- **Fastener Tightening Torque:** `4.5 N·m`
- **Scheduled Calibration Interval:** `12 months`
