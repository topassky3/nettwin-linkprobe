# Guía de ejecución — Piloto HacheNet con NetTwin LinkProbe v0.3

Esta guía acompaña el flujo principal del `README.md`.

La idea es simple: **primero se obtiene y confirma la información necesaria; después se configura NetTwin; finalmente se ejecuta un solo comando que captura durante cuatro horas y genera el informe automáticamente.**

No hace falta abrir ni analizar CSV manualmente.

---

# 1. Resultado esperado

Una ejecución correcta sigue este flujo:

```text
instalar
  ↓
prueba local PASS
  ↓
confirmar interfaz + target + autorización
  ↓
pilot-validate PASS
  ↓
pilot-run --authorized
  ↓
4 horas
  ↓
análisis automático
  ↓
report/report.html
```

---

# 2. Antes de pedir valores: cómo obtenerlos

## Interfaz

Lista las interfaces visibles:

```powershell
python nettwin.py interfaces
```

Puedes complementar con:

```powershell
Get-NetIPConfiguration |
  Select-Object InterfaceAlias, InterfaceDescription, IPv4Address, IPv4DefaultGateway |
  Format-Table -AutoSize
```

Escoge el nombre exacto de la interfaz por donde pasa el enlace que se quiere observar.

Si no está claro cuál es, no adivines: confírmalo con la persona que conoce la conexión del servidor.

---

## Target

El target es una IP estable contra la que NetTwin enviará pequeñas sondas ICMP.

Puedes consultar la ruta por defecto, sin enviar sondas:

```powershell
Get-NetRoute `
  -AddressFamily IPv4 `
  -DestinationPrefix "0.0.0.0/0" |
  Sort-Object RouteMetric |
  Select-Object InterfaceAlias, NextHop, RouteMetric |
  Format-Table -AutoSize
```

El `NextHop` puede ser una referencia útil, pero **no se convierte automáticamente en un target autorizado**.

Debe confirmarse una IP estable y permitida para el piloto.

Para el primer piloto, un solo target es suficiente.

---

## Qué representa el target

El campo `role` es solo una etiqueta explicativa.

Ejemplos:

```text
upstream_reference
controlled_reference
external_reference
core_router_reference
```

Escoge una etiqueta que describa qué representa la IP real utilizada.

---

## Autorización

Antes de ejecutar el piloto se necesitan dos datos:

```text
APPROVED_BY = quién aprobó las sondas
AUTH_REF    = dónde quedó registrada esa aprobación
```

Ejemplos de referencia:

```text
Ticket CHG-1234
Correo "Piloto NetTwin LINK01" 2026-08-10
Reunión de aprobación 2026-08-10
```

---

## Capacidad

Solo usa una capacidad en Mbps si proviene de una fuente real: contrato, configuración, inventario, NMS o confirmación de quien opera el enlace.

No uses la velocidad mostrada por la tarjeta de red como sustituto.

Si no se conoce:

```powershell
$CAPACITY_MBPS = $null
```

Eso es válido.

---

# 3. Cuando ya tengas todos los datos

La referencia paso a paso completa, incluyendo los bloques PowerShell para generar el JSON automáticamente, está en:

```text
README.md
```

Busca la sección:

```text
# 7. Ahora sí: colocar los datos en PowerShell
```

No saltes directamente a esa sección sin haber completado primero la recolección de información.

---

# 4. Qué ocurre después de pilot-run

Cuando `pilot-validate` da `PASS`, se ejecuta:

```powershell
python nettwin.py pilot-run `
  --config $PILOT_CONFIG `
  --authorized
```

A partir de ahí el proceso es automático:

```text
preflight
  ↓
captura coordinada durante 4 horas
  ↓
sellado SHA-256
  ↓
verificación
  ↓
Quality Gate
  ↓
eventos
  ↓
correlaciones
  ↓
evidencia
  ↓
fingerprints
  ↓
informe
  ↓
segunda verificación
```

No hace falta ejecutar comandos analíticos después de las cuatro horas.

---

# 5. Qué se consulta al terminar

Estado general:

```text
pilot_summary.json
```

Informe principal:

```text
report/report.html
```

Los archivos dentro de:

```text
run/
```

son la evidencia original y no deben editarse manualmente.

---

# 6. Qué se entrega

Entrega la carpeta completa del piloto o el ZIP generado según el README.

Debe contener:

```text
hachenet_pilot.json
pilot_summary.json
run/
report/
```

No hace falta separar los CSV ni preparar cálculos manuales.

---

# 7. Regla principal

```text
Si no sabes de dónde sale un valor, no lo inventes.
```

Vuelve a la sección correspondiente del README, obtén o confirma el dato y después continúa.

Ese principio mantiene el piloto reproducible y evita que una configuración aparentemente válida mida algo distinto del enlace que se quería observar.
