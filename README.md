# Ambilight Tuya for PC

Aplicacion local para Windows que captura la pantalla principal, extrae colores por zonas y sincroniza una o varias luces RGB Tuya detras del monitor.

## Que reutiliza este repo

Este proyecto conserva el SDK cloud original de Tuya en `tuya_connector/` y lo encapsula dentro de una arquitectura nueva en `src/ambilight_tuya/`.

Se reutiliza:

- `tuya_connector.openapi.TuyaOpenAPI` para autenticacion y llamadas HTTP
- `tuya_connector.openlogging` para logs filtrados del SDK
- la base del repo original como proveedor del cliente Tuya

Se reemplaza:

- ejemplos inseguros con secretos hardcodeados
- organizacion del repo orientada al SDK
- scripts de prueba acoplados
- configuracion directa en codigo

## Estructura

```text
.
|-- config/
|   `-- config.yaml
|-- docs/
|   `-- architecture.md
|-- legacy/
|   `-- example/
|-- scripts/
|   |-- get_device_status.py
|   |-- list_devices.py
|   |-- run_sync.py
|   |-- screen_sample_test.py
|   `-- set_fixed_color.py
|-- src/
|   `-- ambilight_tuya/
|       |-- color_extractor/
|       |-- config/
|       |-- device_mapper/
|       |-- models/
|       |-- screen_capture/
|       |-- smoothing/
|       |-- sync_engine/
|       |-- tuya_client/
|       `-- utils/
|-- tests/
|-- .env.example
`-- tuya_connector/
```

## Requisitos

- Python 3.11+
- Windows para la captura en tiempo real recomendada
- un proyecto cloud de Tuya con dispositivos vinculados

## Instalacion

1. Crea y activa un entorno virtual.
2. Instala dependencias:

```powershell
pip install -r requirements.txt
pip install -e .
```

3. Copia `.env.example` a `.env` y completa tus credenciales.
4. Revisa `config/config.yaml` y ajusta monitor, zonas, smoothing y mapeo de luces.

## Configuracion segura

Las credenciales viven en `.env`.

Variables requeridas:

- `TUYA_ACCESS_ID`
- `TUYA_ACCESS_KEY`
- `TUYA_API_ENDPOINT`

Variables opcionales:

- `TUYA_MQ_ENDPOINT`
- `TUYA_DEFAULT_DEVICE_ID`
- `AMBILIGHT_CONFIG_PATH`
- `AMBILIGHT_LOG_LEVEL`

Ejemplo rapido:

```env
TUYA_ACCESS_ID=replace-me
TUYA_ACCESS_KEY=replace-me
TUYA_API_ENDPOINT=https://openapi.tuyaus.com
TUYA_MQ_ENDPOINT=wss://mqe.tuyaus.com:8285/
TUYA_DEFAULT_DEVICE_ID=
AMBILIGHT_CONFIG_PATH=config/config.yaml
AMBILIGHT_LOG_LEVEL=INFO
```

## Descubrir dispositivos Tuya

Lista los dispositivos disponibles en tu proyecto:

```powershell
python scripts/list_devices.py
```

Consulta el estado de un dispositivo:

```powershell
python scripts/get_device_status.py --device-id <device-id>
```

## Probar color fijo

Envia un color RGB fijo a una luz:

```powershell
python scripts/set_fixed_color.py --device-id <device-id> --rgb 255,80,40
```

Tambien puedes usar `--zone left` para resolver el dispositivo desde el `config.yaml`.

## Probar captura de pantalla

Captura un frame del monitor configurado y muestra el muestreo por zonas:

```powershell
python scripts/screen_sample_test.py --save-preview docs/sample_frame.png
```

## Dashboard localhost

Puedes manipular las acciones principales desde una UI local:

```powershell
python scripts/run_localhost.py
```

Luego abre:

```text
http://127.0.0.1:8787
```

Desde ahi puedes:

- listar dispositivos Tuya
- consultar estado de un dispositivo
- enviar un color fijo por `device_id` o por zona
- capturar una muestra de pantalla
- iniciar y detener el sync engine

Notas:

- `screen sample` y `sync dry-run` no requieren credenciales Tuya
- las acciones contra la nube si requieren `.env` configurado
- el sync puede iniciarse en `dry-run` para validar colores sin tocar las luces

## Ejecutar modo sync

Arranca el loop de sincronizacion:

```powershell
python scripts/run_sync.py
```

Opciones utiles:

- `--duration 30` para una prueba temporal
- `--dry-run` para ver colores sin enviar comandos
- `--monitor-index 2` para sobreescribir el monitor configurado

## Diferencias entre dispositivos Tuya

No todas las luces Tuya exponen los mismos codigos. Algunas usan:

- `switch_led`, `work_mode`, `colour_data_v2`
- otras requieren DPs o codigos alternativos

Por eso el proyecto soporta perfiles de comando por dispositivo en `config/config.yaml`, con fallback configurable.

## Testing

Ejecuta tests unitarios con:

```powershell
pytest
```

Cobertura actual:

- extraccion de color
- smoothing
- mapeo de dispositivos
- cliente Tuya con mock

## Legacy y seguridad

Los notebooks y ejemplos originales fueron movidos a `legacy/example/` y saneados. Se conservan solo como referencia historica; no deben usarse en produccion.

## Estado actual

Ya queda funcional:

- carga segura de credenciales
- cliente Tuya desacoplado
- captura de pantalla por monitor
- extraccion de color promedio o dominante por zonas
- smoothing basico configurable
- mapping zona -> dispositivos
- scripts operativos minimos
- dashboard localhost para manipular acciones desde navegador
- tests unitarios base

Pendiente para siguientes iteraciones:

- UI de escritorio
- calibracion visual avanzada
- streaming de captura mas optimizado
- soporte por modelo para mas formatos de color
