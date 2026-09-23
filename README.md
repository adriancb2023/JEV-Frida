# KEV Backend — Decision Model Service

Microservicio contenerizado en Docker del modelo de decisiones **KEV** (reconstrucción de código abierto de **JEV**, compatible con la API de [TypeSafe System One](https://docs.typesafe.ai/api)).

Este servicio ejecuta inferencia **prefill-only** (sin generación de texto palabra por palabra). En un único pase de red neuronal, evalúa un documento o estado y calcula probabilidades matemáticas calibradas sobre preguntas discretas (`choice`, `noul`, `score`).

Está optimizado para ejecutarse **100% en CPU** con bajo consumo de memoria (**~1.8 GB de RAM** con el modelo `kev-0.8b`), ideal para cualquier ordenador o servidor con 16 GB de RAM sin necesidad de tarjeta gráfica NVIDIA.

---

## 🚀 Inicio Rápido con Docker

### 1. Clonar y configurar entorno
El archivo `.env` ya viene configurado para CPU y alta fidelidad:
```bash
# Si no existe .env, copia la plantilla:
cp .env.example .env
```

### 2. Levantar el servicio
```bash
docker compose up -d --build
```

### 3. Verificar estado
La primera vez descargará los pesos de Hugging Face (~1.6 GB) a un volumen persistente (`kev_hf_cache`). Puedes seguir el progreso con:
```bash
docker compose logs -f
```

Una vez levantado, la API responderá en `http://localhost:9936`.

---

## 📡 Referencia de Endpoints

### 1. `POST /v1/systemone`
El endpoint principal para evaluar estados y tomar decisiones estructuradas.

#### Estructura de la Petición
```jsonc
{
  "state": "...",           // string | object | array (El texto, documento o datos a evaluar)
  "model": "kev-latest",    // "kev-latest" o "jev-latest"
  "questions": {
    "<question_id>": {      // ID personalizado para cada pregunta
      // Definición de la pregunta (choice, noul o score)
    }
  }
}
```

---

### Tipos de Preguntas y Reglas

#### A) `choice` — Clasificación / Elección Múltiple
Elige la mejor categoría entre 1 y 255 opciones y devuelve la distribución de probabilidad completa.

* **Parámetros:**
  * `type` (string, requerido): `"choice"`
  * `instructions` (string | object, opcional): Contexto o pregunta que guía la decisión.
  * `criteria` (object, requerido): Mapa de `{ "nombre_opcion": "descripción" | null }`.
* **Ejemplo de Petición:**
  ```json
  {
    "state": "El cliente solicita cancelar su suscripción anual porque no utiliza el servicio.",
    "model": "kev-latest",
    "questions": {
      "departamento": {
        "type": "choice",
        "instructions": "¿A qué departamento debe derivarse este ticket?",
        "criteria": {
          "bajas": "Cancelaciones, reembolsos y rescisión de contratos",
          "soporte": "Problemas técnicos, bugs o caídas del servicio",
          "facturacion": "Dudas con recibos, cobros dobles o métodos de pago"
        }
      }
    }
  }
  ```
* **Respuesta Devuelta:**
  ```json
  {
    "model": "kev-latest",
    "answers": {
      "departamento": {
        "type": "choice",
        "choice": "bajas",
        "confidence": 0.8842,
        "probabilities": {
          "bajas": 0.9228,
          "facturacion": 0.0512,
          "soporte": 0.0260
        }
      }
    },
    "usage": { "input_tokens": 84, "output_tokens": 42 },
    "latency_ms": 285.4
  }
  ```

---

#### B) `noul` — Decisión Booleana (Sí / No)
Evalúa una proposición afirmativa y devuelve la probabilidad de que sea verdadera (`true`).

* **Parámetros:**
  * `type` (string, requerido): `"noul"`
  * `instructions` (string, requerido): Pregunta afirmativa.
  * `criteria` (object, opcional): Descripciones para `true` y `false`.
* **Ejemplo de Petición:**
  ```json
  {
    "state": "He pedido la devolución hace 3 semanas y nadie me responde. Estoy harto.",
    "model": "kev-latest",
    "questions": {
      "urgente": {
        "type": "noul",
        "instructions": "¿Requiere intervención humana urgente?",
        "criteria": {
          "true": "El cliente está muy insatisfecho o hay retrasos graves",
          "false": "Es una consulta rutinaria sin urgencia"
        }
      }
    }
  }
  ```
* **Respuesta Devuelta:**
  ```json
  {
    "model": "kev-latest",
    "answers": {
      "urgente": {
        "type": "noul",
        "noul": 0.9415
      }
    },
    "usage": { "input_tokens": 62, "output_tokens": 18 },
    "latency_ms": 194.2
  }
  ```

---

#### C) `score` — Escala Ordinal / Calificación por Niveles
Evalúa un nivel dentro de una lista ordenada de menor a mayor (1 a 255 niveles).

* **Parámetros:**
  * `type` (string, requerido): `"score"`
  * `instructions` (string, opcional): Qué evaluar.
  * `criteria` (array, requerido): Lista de niveles ordenados de menor a mayor intensidad.
* **Ejemplo de Petición:**
  ```json
  {
    "state": "Todo funcionó perfecto, entrega rápida y producto impecable.",
    "model": "kev-latest",
    "questions": {
      "satisfaccion": {
        "type": "score",
        "instructions": "Nivel de satisfacción del cliente",
        "criteria": ["Muy insatisfecho", "Neutral", "Satisfecho", "Encantado"]
      }
    }
  }
  ```
* **Respuesta Devuelta:**
  ```json
  {
    "model": "kev-latest",
    "answers": {
      "satisfaccion": {
        "type": "score",
        "score": 2.9104,
        "confidence": 0.9125,
        "legend": {
          "0": "Muy insatisfecho",
          "1": "Neutral",
          "2": "Satisfecho",
          "3": "Encantado"
        },
        "probabilities": {
          "0": 0.0012,
          "1": 0.0145,
          "2": 0.0578,
          "3": 0.9265
        }
      }
    },
    "usage": { "input_tokens": 58, "output_tokens": 36 },
    "latency_ms": 210.1
  }
  ```

---

### 2. `GET /v1/models`
Devuelve la ficha técnica del modelo cargado en memoria, dispositivo de ejecución (`cpu`), precisión (`fp32`), temperatura de calibración y estadísticas del caché de prefijos.

* **Ejemplo:**
  ```bash
  curl http://localhost:9936/v1/models
  ```
* **Respuesta Devuelta:**
  ```json
  {
    "models": [
      {
        "name": "kev-latest",
        "description": "Kev pointer head on Qwen/Qwen3.5-0.8B-Base, serving jaredpalmer/kev-0.8b at temperature 2.41",
        "release_date": "2026-09-21",
        "run": "jaredpalmer/kev-0.8b",
        "base": "Qwen/Qwen3.5-0.8B-Base",
        "lora": 16,
        "device": "cpu",
        "backend": "torch",
        "dtype": "float32",
        "temperature": 2.41,
        "prefix_cache": {
          "size": 4,
          "min_state_tokens": 0,
          "hits": 0,
          "misses": 0,
          "cached_states": 0
        }
      },
      {
        "name": "jev-latest",
        "description": "Kev pointer head on Qwen/Qwen3.5-0.8B-Base, serving jaredpalmer/kev-0.8b at temperature 2.41",
        "release_date": "2026-09-21",
        "run": "jaredpalmer/kev-0.8b",
        "base": "Qwen/Qwen3.5-0.8B-Base",
        "lora": 16,
        "device": "cpu",
        "backend": "torch",
        "dtype": "float32",
        "temperature": 2.41,
        "prefix_cache": {
          "size": 4,
          "min_state_tokens": 0,
          "hits": 0,
          "misses": 0,
          "cached_states": 0
        }
      }
    ]
  }
  ```

---

### 3. `POST /v1/systemone/permute`
Prueba de robustez posicional: ejecuta una pregunta `choice` bajo varias permutaciones aleatorias del orden de las opciones para verificar que el resultado es estable y no sufre de sesgos de posición.

* **Parámetros:**
  * `request` (object, requerido): Una petición `SystemOneRequest` completa (con `state`, `model` y `questions`).
  * `question` (string, requerido): El ID de la pregunta `choice` a permutar.
  * `n_perm` (integer, opcional, default `6`, máx `64`): Número de órdenes aleatorios a probar.
  * `seed` (integer, opcional, default `0`): Semilla para reproducibilidad de las permutaciones.

* **Ejemplo de Petición:**
  ```json
  {
    "request": {
      "state": "El cliente solicita cancelar su suscripción anual porque no utiliza el servicio.",
      "model": "kev-latest",
      "questions": {
        "departamento": {
          "type": "choice",
          "instructions": "¿A qué departamento debe derivarse este ticket?",
          "criteria": {
            "bajas": "Cancelaciones, reembolsos y rescisión de contratos",
            "soporte": "Problemas técnicos, bugs o caídas del servicio",
            "facturacion": "Dudas con recibos, cobros dobles o métodos de pago"
          }
        }
      }
    },
    "question": "departamento",
    "n_perm": 4,
    "seed": 42
  }
  ```

* **Respuesta Devuelta:**
  ```json
  {
    "runs": [
      {
        "order": ["bajas", "soporte", "facturacion"],
        "probabilities": { "bajas": 0.9228, "soporte": 0.0260, "facturacion": 0.0512 },
        "choice": "bajas",
        "latency_ms": 285.4
      },
      {
        "order": ["facturacion", "bajas", "soporte"],
        "probabilities": { "bajas": 0.9195, "soporte": 0.0283, "facturacion": 0.0522 },
        "choice": "bajas",
        "latency_ms": 278.1
      },
      {
        "order": ["soporte", "facturacion", "bajas"],
        "probabilities": { "bajas": 0.9210, "soporte": 0.0271, "facturacion": 0.0519 },
        "choice": "bajas",
        "latency_ms": 281.7
      },
      {
        "order": ["bajas", "facturacion", "soporte"],
        "probabilities": { "bajas": 0.9241, "soporte": 0.0248, "facturacion": 0.0511 },
        "choice": "bajas",
        "latency_ms": 276.3
      }
    ],
    "argmax_stable": true,
    "spread": {
      "bajas": 0.0046,
      "soporte": 0.0035,
      "facturacion": 0.0011
    }
  }
  ```

  > **`argmax_stable: true`** confirma que la opción ganadora es la misma en todas las permutaciones.
  > **`spread`** muestra la diferencia máxima de probabilidad para cada opción entre todas las permutaciones (cuanto menor, más robusto).

---

### 4. `POST /v1/systemone/separate`
Ejecuta cada una de las preguntas de la petición en un pase independiente hacia el modelo para comparar si el aislamiento de ramas varía respecto al pase conjunto.

* **Petición:** Idéntica a `POST /v1/systemone` (misma estructura `SystemOneRequest`).

* **Ejemplo de Petición:**
  ```json
  {
    "state": "Mi pedido llegó con la talla equivocada. Quiero cambiarlo por una 42.",
    "model": "kev-latest",
    "questions": {
      "tipo": {
        "type": "choice",
        "instructions": "Clasifica la incidencia",
        "criteria": {
          "cambio_talla": "Cambio por otra talla",
          "reembolso": "Devolución del dinero",
          "otro": "Otra incidencia"
        }
      },
      "urgente": {
        "type": "noul",
        "instructions": "¿Es urgente?"
      }
    }
  }
  ```

* **Respuesta Devuelta:**
  ```json
  {
    "model": "kev-latest",
    "answers": {
      "tipo": {
        "type": "choice",
        "choice": "cambio_talla",
        "confidence": 0.9310,
        "probabilities": {
          "cambio_talla": 0.9540,
          "reembolso": 0.0320,
          "otro": 0.0140
        }
      },
      "urgente": {
        "type": "noul",
        "noul": 0.2180
      }
    },
    "usage": { "input_tokens": 142, "output_tokens": 54 },
    "latency_ms": 563.8
  }
  ```

  > A diferencia de `/v1/systemone`, aquí cada pregunta se evalúa en un forward pass separado (N preguntas = N pases), por lo que la latencia total es la suma de las individuales. El `input_tokens` también es mayor porque el estado se tokeniza N veces.

---

## 💻 Ejemplos de Integración en Proyectos

### Con `cURL`
```bash
curl -X POST http://localhost:9936/v1/systemone \
  -H "Content-Type: application/json" \
  -d '{
    "state": "Mi pedido llegó con la talla equivocada. Quiero cambiarlo por una 42.",
    "model": "kev-latest",
    "questions": {
      "tipo": {
        "type": "choice",
        "instructions": "Clasifica la incidencia",
        "criteria": {
          "cambio_talla": "Cambio por otra talla",
          "reembolso": "Devolución del dinero",
          "otro": "Otra incidencia"
        }
      },
      "urgente": {
        "type": "noul",
        "instructions": "¿Es urgente?"
      }
    }
  }'
```

### Con Python (`requests`)
```python
import requests

url = "http://localhost:9936/v1/systemone"
payload = {
    "state": "Error 500 en la base de datos de producción desde las 10:00 AM.",
    "model": "kev-latest",
    "questions": {
        "severidad": {
            "type": "score",
            "instructions": "Gravedad de la incidencia",
            "criteria": ["Baja", "Media", "Alta", "Crítica"]
        },
        "notificar_guardia": {
            "type": "noul",
            "instructions": "¿Debe avisarse al equipo de guardia?"
        }
    }
}

response = requests.post(url, json=payload).json()
print("Score de severidad:", response["answers"]["severidad"]["score"])
print("Probabilidad notificar guardia:", response["answers"]["notificar_guardia"]["noul"])
```

### Con TypeScript / Node.js (`fetch`)
```typescript
async function decidir() {
  const res = await fetch("http://localhost:9936/v1/systemone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      state: "Factura INV-2026-004 de 12.000€ recibida sin orden de compra asociada.",
      model: "kev-latest",
      questions: {
        accion: {
          type: "choice",
          instructions: "¿Qué acción procede según la política de compras?",
          criteria: {
            aprobar: "Aprobación directa",
            rechazar: "Rechazar por falta de PO",
            auditar: "Enviar a auditoría manual"
          }
        }
      }
    })
  });

  const data = await res.json();
  console.log("Acción sugerida:", data.answers.accion.choice);
  console.log("Probabilidades:", data.answers.accion.probabilities);
}

decidir();
```

---

## ⚙️ Configuración (`.env`)

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `KEV_MODEL` | `jaredpalmer/kev-0.8b` | Modelo de Hugging Face. `kev-0.8b` requiere solo ~1.8 GB RAM en CPU. |
| `KEV_DATE_FACTS` | `1` | Precalcula la diferencia en días entre fechas absolutas (aumenta la fidelidad a JEV del 80% al 90%). |
| `KEV_DTYPE` | `fp32` | Precisión matemática exacta de 32 bits en CPU. |
| `KEV_HOST` | `0.0.0.0` | IP de escucha dentro del contenedor. |
| `PORT` | `9936` | Puerto expuesto para el servicio. |
| `KEV_API_KEY` | *(vacío)* | Si se especifica, exige cabecera `Authorization: Bearer <clave>`. |

---

## 📊 Reglas de Entrada y Límites del Sistema

1. **Estructura del `state`**:
   - Puede ser un string plano, un array o un objeto JSON anidado (el modelo aplana objetos preservando nombres de campos como etiquetas jerárquicas).
2. **Número de preguntas por petición**:
   - Ilimitado: El servidor procesa preguntas por lotes automáticos (*token budget* de 16.384 tokens por forward pass), por lo que la memoria no crece descontroladamente.
3. **Número de opciones en `choice` y niveles en `score`**:
   - De 1 a 255 opciones por pregunta.
4. **Delimitadores seguros**:
   - El texto del usuario se sanea automáticamente contra inyecciones de delimitadores internos (`<|fim_prefix|>`, `<opt>`, `<decide>`).
