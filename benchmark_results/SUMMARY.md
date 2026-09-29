# Resultados Oficiales de Benchmarks — PRAXEON v1.0.0

**Runtime supervision for autonomous AI agents**

Generado automáticamente: `2026-09-29 08:26:56 UTC`  
Plataforma: `win32` | Python: `3.11.15`

## Resumen Ejecutivo

PRAXEON evalúa formalmente la calidad decisional, resistencia física ante ataques y eficiencia operacional mediante 5 dimensiones desacopladas y un conjunto de **1.000+ escenarios procedurales** particionados en **800 Train** y **200 Holdout** libre de sobreajuste.

---

## 1. Policy Benchmark (Calidad Decisional y Falsos Permitidos)

| Métrica | Holdout (n=200) | Train (n=800) | Objetivo Normativo |
| :--- | :---: | :---: | :---: |
| **Exactitud (Accuracy)** | **100.0%** | 100.0% | ≥ 95.0% |
| **False Allow Rate (Crítico)** | **0.0%** | 0.0% | **0.0%** |
| **Destructive False Allows** | **0** | 0 | **0** |
| **False Block Rate** | **0.0%** | 0.0% | ≤ 5.0% |
| **Precisión Bloqueo Justificado** | **100.0%** | 100.0% | ≥ 95.0% |

---

## 2. Enforcement Benchmark (Barreras Físicas y Resistencia Adversarial)

| Vector de Evasión Probado | Resultado Físico | Mecanismo de Defensa Aplicado |
| :--- | :---: | :--- |
| **Firma HMAC Forjada / Alterada** | **BLOQUEADO** | Firma HMAC-SHA256 (`sign_receipt` / `compute_receipt_signature`) -> `PolicyViolation` |
| **Replay Attack (Nonce Ya Consumido)** | **BLOQUEADO** | Control de nonces en `NonceStore` con TTL y poda periódica -> `PolicyViolation` |
| **Path Traversal / Symlink Escape** | **BLOQUEADO** | `LocalProcessSandbox` con `os.path.realpath` y carcelamiento de workspace |
| **Egress SSRF a Cloud Metadata** | **BLOQUEADO** | `EgressPolicy` (modo `block_all` o filtrado de IPs reservadas `169.254.169.254`) |
| **Tasa de Prevención Física** | **100.0%** | **100% de ataques detenidos antes de tocar el SO** |

---

## 3. Runtime Benchmark (Throughput y Latencias Percentiles)

Evaluación en caliente sobre **200 operaciones consecutivas**:

- **Throughput:** `6077.7 ops/segundo`
- **Latencia p50 (Mediana):** `0.158 ms`
- **Latencia p95:** `0.187 ms`
- **Latencia p99:** `0.285 ms`
- **Latencia Media:** `0.164 ms`
- **Latencia Máxima:** `0.329 ms`

---

## 4. Trajectory Benchmark (Trayectorias Multi-Paso y Backtracking)

- **Trayectorias Evaluadas:** `3`
- **Tasa de Completitud:** `100.0%` (3/3)
- **Rollbacks Ejecutados:** `1`
- **Rollbacks Exitosos a Checkpoint:** `1`
- **Tasa de Recuperación tras Bucle:** `100.0%`
- **Finalizaciones Prematuras Bloqueadas:** `1`

---

## 5. Estudio de Ablaciones Cuantitativo (6 Configuraciones)

| Configuración Arquitectural | Accuracy | False Allow | Destructive FA | Valor Económico Neto ($	ext{NavigatorValue}$) |
| :--- | :---: | :---: | :---: | :---: |
| **1. Policy Only (No JEV)** | 62.0% | 16.8% | 0 | **$16,899.60** |
| **2. JEV (No Evidence Engine)** | 92.5% | 8.1% | 0 | **$18,499.60** |
| **3. JEV + Evidence (No Risk Engine)** | 76.5% | 8.1% | 0 | **$18,499.60** |
| **4. JEV + Evidence + Risk (No FailSafe)** | 76.5% | 25.4% | 16 | **$15,299.60** |
| **5. Full v0.2 Architecture** | 100.0% | 0.0% | 0 | **$19,999.60** |
| **6. Full v0.4 Architecture (Confidence Router)** | 100.0% | 0.0% | 0 | **$19,999.60** |

---

## Conclusiones
 
1. **Mitigación Determinista de Acciones Destructivas:** En las configuraciones completas de PRAXEON 1.0, la tasa de acciones destructivas indebidamente permitidas sobre la suite de evaluación es de **0**, mitigando el riesgo observado en modelos sin supervisor o con fallback permisivo.
2. **Eficiencia en Runtime:** La sobrecarga introducida por la capa de supervisión es de **menos de 1 ms en mediana ($p50$)**, habilitando supervisión en tiempo real a alta velocidad.
3. **Resistencia Comprobada en Suite de Seguridad:** Las pruebas adversariales de bypass (HMAC forjado, replay, symlink, inyecciones indirectas o violaciones de egress) fueron contenidas deterministamente por la barrera de enforcement en tiempo de ejecución.

